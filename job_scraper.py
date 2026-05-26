import os
import json
import requests
from datetime import datetime
from google import genai
from google.genai import types
from utils import notify, log_event, send_html_email

GREENHOUSE_TARGETS = [
    ("braincorporation", "Brain Corp"),
]

LEVER_TARGETS = [
    ("shieldai", "Shield AI"),
    ("saronic", "Saronic"),
]

GEMINI_COMPANIES = [
    "Anduril Industries", "Northrop Grumman", "General Atomics", "Kratos Defense",
    "Firestorm Labs", "Seasats", "Qualcomm", "Apple", "Google", "ServiceNow",
]


def load_resume():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(base_dir, "resume-summary.txt"), "r") as f:
            return f.read()
    except Exception as e:
        log_event(f"Error loading resume: {e}")
        return ""


def load_seen_jobs():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    seen_path = os.path.join(base_dir, "jobs-seen.json")
    try:
        if os.path.exists(seen_path):
            with open(seen_path, "r") as f:
                return json.load(f)
    except Exception as e:
        log_event(f"Error loading seen jobs: {e}")
    return []


def save_seen_jobs(seen_list):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(base_dir, "jobs-seen.json"), "w") as f:
        json.dump(seen_list, f, indent=2)


def extract_resume_keywords(resume_content):
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    prompt = f"""Extract job search keywords from this resume. Output ONLY a raw JSON object — no markdown, no explanation:
{{
  "titles": ["relevant job title variants to search for"],
  "skills": ["technical skills and certifications"],
  "domains": ["industry domains, program types, platforms"]
}}

RESUME:
{resume_content}"""
    try:
        response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        clean = response.text.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return json.loads(clean)
    except Exception as e:
        log_event(f"Keyword extraction failed: {e} — using fallback keywords")
        return {
            "titles": ["systems engineer", "systems architect", "autonomy engineer",
                       "mission systems", "product manager", "digital engineering"],
            "skills": ["mbse", "sysml", "ros", "defense"],
            "domains": ["uuv", "uas", "autonomy", "defense", "maritime", "undersea"],
        }


def _title_matches(title, keywords):
    title_lower = title.lower()
    return any(kw.lower() in title_lower for kw in keywords["titles"] + keywords["domains"])


def _location_matches(location):
    loc_lower = location.lower()
    return any(lw in loc_lower for lw in ["san diego", "remote", "california"])


def fetch_greenhouse_jobs(slug, company_name, keywords):
    try:
        resp = requests.get(
            f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", timeout=10
        )
        if resp.status_code == 404:
            log_event(f"Greenhouse: {company_name} ({slug}) not found — skipping")
            return []
        resp.raise_for_status()
        matches = []
        for job in resp.json().get("jobs", []):
            title = job.get("title", "")
            location = job.get("location", {}).get("name", "")
            if _title_matches(title, keywords) and _location_matches(location):
                matches.append({
                    "company": company_name,
                    "title": title,
                    "location": location,
                    "url": job.get("absolute_url", ""),
                    "req_id": str(job.get("id", "")),
                    "source": "greenhouse",
                })
        return matches
    except Exception as e:
        log_event(f"Greenhouse fetch error for {company_name}: {e}")
        return []


def fetch_lever_jobs(slug, company_name, keywords):
    try:
        resp = requests.get(
            f"https://api.lever.co/v0/postings/{slug}?mode=json", timeout=10
        )
        if resp.status_code == 404:
            log_event(f"Lever: {company_name} ({slug}) not found — skipping")
            return []
        resp.raise_for_status()
        jobs = resp.json()
        if not isinstance(jobs, list):
            return []
        matches = []
        for job in jobs:
            title = job.get("text", "")
            location = job.get("categories", {}).get("location", "")
            if _title_matches(title, keywords) and _location_matches(location):
                matches.append({
                    "company": company_name,
                    "title": title,
                    "location": location,
                    "url": job.get("hostedUrl", ""),
                    "req_id": "",  # Lever IDs are UUIDs; title often includes req number inline
                    "source": "lever",
                })
        return matches
    except Exception as e:
        log_event(f"Lever fetch error for {company_name}: {e}")
        return []


def fetch_gemini_jobs(resume_content, keywords, seen_urls):
    title_list = ", ".join(keywords.get("titles", [])[:8])
    domain_list = ", ".join(keywords.get("domains", [])[:8])
    company_list = ", ".join(GEMINI_COMPANIES)

    prompt = f"""You are a job search agent. Search for open job postings and return structured JSON.

CANDIDATE RESUME:
{resume_content}

SEARCH TASK 1 — Named companies:
Search for open roles at each of these companies: {company_list}
Focus on titles matching: {title_list}
Also match domain keywords: {domain_list}
Location: San Diego, CA ONLY (include hybrid; exclude remote-only and non-SD locations)

SEARCH TASK 2 — Broad keyword search:
Search: (site:greenhouse.io OR site:lever.co) AND ("Systems Engineer" OR "Mission Systems" OR "Autonomy Engineer" OR "MBSE" OR "UUV" OR "UAS") AND "San Diego" 2026
Include any company not already in the named list above.

RULES:
- Every result MUST have a direct URL to the job posting page. Omit any job you cannot find a real URL for.
- Do NOT fabricate or guess URLs. Only include jobs you found via search.
- Skip any job whose URL appears in this already-seen list: {json.dumps(list(seen_urls)[-30:])}
- San Diego area only. No remote-only.

OUTPUT — return ONLY a raw JSON object, no markdown, no explanation:
{{
  "jobs": [
    {{
      "company": "Company Name",
      "title": "Exact Job Title as listed",
      "location": "City, State",
      "url": "https://direct-link-to-posting",
      "req_id": "R12345 or empty string"
    }}
  ]
}}"""

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    try:
        response = client.models.generate_content(
            model='gemini-3.1-pro-preview',
            contents=prompt,
            config=types.GenerateContentConfig(tools=[{"google_search": {}}]),
        )
        clean = response.text.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        data = json.loads(clean)
        return [
            {
                "company": j.get("company", ""),
                "title": j.get("title", ""),
                "location": j.get("location", ""),
                "url": j.get("url", ""),
                "req_id": j.get("req_id", ""),
                "source": "gemini",
            }
            for j in data.get("jobs", [])
            if j.get("url")
        ]
    except Exception as e:
        log_event(f"Gemini job search error: {e}")
        return []


def build_email_html(new_jobs):
    now_str = datetime.now().strftime("%A, %B %d, %Y")
    today_str = datetime.now().strftime("%B %d, %Y")
    company_count = len(set(j["company"] for j in new_jobs))
    role_word = "role" if len(new_jobs) == 1 else "roles"
    co_word = "company" if company_count == 1 else "companies"

    by_company = {}
    for job in new_jobs:
        by_company.setdefault(job["company"], []).append(job)

    company_sections = ""
    for company in sorted(by_company.keys()):
        job_rows = ""
        for job in by_company[company]:
            verified = job.get("source") in ("greenhouse", "lever")
            badge = (
                '<span style="color:#2e7d32;font-size:11px;font-weight:600;'
                'margin-right:10px;">&#10003; Verified</span>'
                if verified else ""
            )
            req_part = (
                f"&nbsp;&nbsp;&middot;&nbsp;&nbsp;Req&nbsp;#{job['req_id']}"
                if job.get("req_id") else ""
            )
            job_rows += f"""
            <div style="margin-bottom:14px;padding:12px 14px;background:#fafafa;border-left:3px solid #1a1a1a;">
              <p style="margin:0 0 4px;font-size:15px;font-weight:700;color:#1a1a1a;line-height:1.3;">{job['title']}</p>
              <p style="margin:0 0 8px;font-size:13px;color:#666;">{job['location']}{req_part}</p>
              <div>{badge}<a href="{job['url']}" style="background:#1a1a1a;color:#ffffff;padding:4px 14px;border-radius:3px;font-size:12px;text-decoration:none;display:inline-block;">Apply &rarr;</a></div>
            </div>"""

        company_sections += f"""
        <h2 style="margin:24px 0 10px;font-size:12px;letter-spacing:2px;text-transform:uppercase;color:#1a1a1a;border-bottom:1px solid #e0e0e0;padding-bottom:6px;">{company}</h2>
        <div style="margin-bottom:4px;">{job_rows}</div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Job Matches</title>
</head>
<body style="margin:0;padding:0;background-color:#f0f0f0;font-family:system-ui,-apple-system,Arial,sans-serif;">
  <div style="max-width:680px;margin:0 auto;background:#ffffff;padding:36px 44px;">

    <div style="text-align:center;border-bottom:3px solid #1a1a1a;padding-bottom:18px;margin-bottom:28px;">
      <h1 style="margin:0;font-size:30px;letter-spacing:4px;text-transform:uppercase;font-weight:800;color:#1a1a1a;">Job Matches</h1>
      <p style="margin:8px 0 0;font-size:14px;color:#555;letter-spacing:1px;">{now_str}&nbsp;&nbsp;&mdash;&nbsp;&nbsp;{len(new_jobs)} new {role_word} across {company_count} {co_word}</p>
    </div>

    {company_sections}

    <div style="margin-top:36px;border-top:1px solid #e0e0e0;padding-top:14px;text-align:center;">
      <p style="margin:0 0 4px;font-size:11px;color:#aaa;letter-spacing:1px;">THE LOBSTERCLAW JOB TRACKER &mdash; {today_str}</p>
      <p style="margin:0;font-size:11px;color:#aaa;">&#10003; Verified listings sourced directly from company job boards.&nbsp;&nbsp;Other listings via AI-assisted search &mdash; confirm posting before applying.</p>
    </div>

  </div>
</body>
</html>"""


def main():
    log_event("Starting Weekly Job Scraper...")
    resume_content = load_resume()
    seen_urls = set(load_seen_jobs())

    # Stage 0: extract keywords from resume — drives all filtering
    keywords = extract_resume_keywords(resume_content)
    log_event(f"Keywords extracted: {len(keywords.get('titles', []))} titles, {len(keywords.get('domains', []))} domains")

    # Tier 1: direct job board APIs (verified listings)
    all_jobs = []
    for slug, name in GREENHOUSE_TARGETS:
        all_jobs.extend(fetch_greenhouse_jobs(slug, name, keywords))
    for slug, name in LEVER_TARGETS:
        all_jobs.extend(fetch_lever_jobs(slug, name, keywords))

    # Tier 2: Gemini broad search (named companies without APIs + open keyword search)
    all_jobs.extend(fetch_gemini_jobs(resume_content, keywords, seen_urls))

    # Dedup
    new_jobs = [j for j in all_jobs if j.get("url") and j["url"] not in seen_urls]

    if not new_jobs:
        log_event("Job Scraper finished: No new matches.")
        return

    seen_urls.update(j["url"] for j in new_jobs)
    save_seen_jobs(list(seen_urls))

    html = build_email_html(new_jobs)
    send_html_email(f"Job Matches — {len(new_jobs)} New Roles", html)
    log_event(f"Job Scraper finished: {len(new_jobs)} new roles across {len(set(j['company'] for j in new_jobs))} companies.")


if __name__ == "__main__":
    main()
