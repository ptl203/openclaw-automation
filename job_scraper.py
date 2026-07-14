import os
import re
import json
import requests
from datetime import datetime
from google import genai
from google.genai import types
from utils import notify, log_event, send_html_email, wait_for_network

# Model IDs — single source of truth
_MODEL_KEYWORDS = "gemini-2.5-flash"          # resume keyword extraction
_MODEL_SEARCH   = "gemini-2.5-pro"             # broad job search (needs Google Search tool)

GREENHOUSE_TARGETS = [
    ("braincorporation",  "Brain Corp"),
    ("andurilindustries", "Anduril Industries"),
    ("epirus",            "Epirus"),
    ("vannevarlabs",      "Vannevar Labs"),
]

LEVER_TARGETS = [
    ("shieldai", "Shield AI"),
    ("saronic",  "Saronic"),
    ("palantir", "Palantir"),
]

ASHBY_TARGETS = [
    ("skydio", "Skydio"),
    # Applied Intuition, Seasats, Saildrone, Vatn: probed — not on Ashby (404).
]

# (tenant, datacenter, site, company_name)
# Probed live: Qualcomm uses Eightfold (not Workday); GA/ViaSat Workday boards
# are private (401) or have no public external site — those three stay in the AI tier.
WORKDAY_TARGETS = [
    ("ngc", "wd1", "Northrop_Grumman_External_Site", "Northrop Grumman"),
]

# Companies with no usable public API — covered by Gemini broad search.
# Northrop Grumman removed: now verified via Workday above.
GEMINI_COMPANIES = [
    "General Atomics", "Kratos Defense",
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
        response = client.models.generate_content(model=_MODEL_KEYWORDS, contents=prompt)
        clean = response.text.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return json.loads(clean)
    except Exception as e:
        log_event(f"Keyword extraction failed: {e} — using fallback keywords")
        return {
            "titles": [
                "systems engineer", "systems architect", "autonomy engineer",
                "mission systems", "digital engineering",
                "product manager", "technical product manager", "senior product manager",
                "group product manager", "product owner",
            ],
            "skills": ["mbse", "sysml", "ros", "defense"],
            "domains": ["uuv", "uas", "autonomy", "defense", "maritime", "undersea",
                        "product management", "roadmap"],
        }


# Extra title synonyms / abbreviations applied on every run regardless of extracted keywords.
# Catches PM shorthands that the AI might normalise differently.
_TITLE_SYNONYMS = [
    "product manager", "product owner", "technical product manager",
    "senior product manager", "group product manager", "director of product",
    "head of product", "principal product manager", "tpm",
]


def _title_matches(title, keywords):
    """True if *title* contains any keyword or synonym as a whole word."""
    title_lower = title.lower()
    all_terms = keywords.get("titles", []) + keywords.get("domains", []) + _TITLE_SYNONYMS
    return any(
        re.search(r"\b" + re.escape(kw.lower()) + r"\b", title_lower)
        for kw in all_terms
    )


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


def fetch_ashby_jobs(slug, company_name, keywords):
    try:
        resp = requests.get(
            f"https://api.ashbyhq.com/posting-api/job-board/{slug}", timeout=10
        )
        if resp.status_code == 404:
            log_event(f"Ashby: {company_name} ({slug}) not found — skipping")
            return []
        resp.raise_for_status()
        matches = []
        for job in resp.json().get("jobs", []):
            if not job.get("isListed", True):
                continue
            title    = job.get("title", "")
            location = job.get("location", "")
            if _title_matches(title, keywords) and _location_matches(location):
                matches.append({
                    "company": company_name,
                    "title":   title,
                    "location": location,
                    "url":     job.get("jobUrl", ""),
                    "req_id":  "",
                    "source":  "ashby",
                })
        return matches
    except Exception as e:
        log_event(f"Ashby fetch error for {company_name}: {e}")
        return []


def fetch_workday_jobs(tenant, dc, site, company_name, keywords):
    base     = f"https://{tenant}.{dc}.myworkdayjobs.com"
    endpoint = f"{base}/wday/cxs/{tenant}/{site}/jobs"
    headers  = {
        **_LINK_CHECK_HEADERS,
        "Content-Type": "application/json",
        "Accept":        "application/json",
    }
    seen_paths = set()
    matches    = []
    # Loop over title keywords to broaden coverage without a single huge query.
    search_terms = keywords.get("titles", [])[:5] or ["systems engineer"]
    for term in search_terms:
        payload = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": term}
        try:
            resp = requests.post(endpoint, json=payload, headers=headers, timeout=12)
            if resp.status_code == 404:
                log_event(f"Workday: {company_name} board not found — skipping")
                return []
            if resp.status_code != 200:
                log_event(f"Workday: {company_name} returned {resp.status_code} for '{term}'")
                continue
            for jp in resp.json().get("jobPostings", []):
                path = jp.get("externalPath", "")
                if not path or path in seen_paths:
                    continue
                seen_paths.add(path)
                title    = jp.get("title", "")
                location = jp.get("locationsText", "")
                req_id   = (jp.get("bulletFields") or [""])[0]
                url      = f"{base}/en-US/{site}{path}"
                if _title_matches(title, keywords) and _location_matches(location):
                    matches.append({
                        "company":  company_name,
                        "title":    title,
                        "location": location,
                        "url":      url,
                        "req_id":   req_id,
                        "source":   "workday",
                    })
        except Exception as e:
            log_event(f"Workday fetch error for {company_name} ('{term}'): {e}")
    return matches


_LINK_CHECK_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# High-precision body markers that indicate an expired/filled posting.
# Keep this list tight — HTTP status handles true 404s; these catch soft-404 pages.
_EXPIRED_MARKERS = [
    "no longer available",
    "no longer accepting",
    "position has been filled",
    "this posting has expired",
    "job is no longer",
]


def _url_is_live(url, timeout=8):
    """Return True only if *url* resolves to a live, open job posting.

    Runs three checks in order:
    1. Any redirect in the chain contains an error flag (e.g. Greenhouse ?error=true).
    2. The final response landed on a different registered domain — indicates a
       soft-404 redirect to the company's generic jobs listing page.
    3. HTTP status must be 200; body must not contain an expired-posting marker.

    Returns False on any network error or timeout.
    Every drop is logged so automation.log shows what was filtered.
    """
    from urllib.parse import urlparse

    def _reg_domain(host):
        """Return the last two dot-parts of a hostname (the registered domain)."""
        parts = host.split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else host

    try:
        original_domain = _reg_domain(urlparse(url).netloc)
        resp = requests.get(url, headers=_LINK_CHECK_HEADERS,
                            allow_redirects=True, timeout=timeout)

        # Check 1: error flag in any redirect URL (Greenhouse soft-404 pattern)
        for r in resp.history:
            if "error=true" in r.url or "error=1" in r.url:
                log_event(f"Link check — redirect error flag: {url}")
                return False

        # Check 2: final URL landed on a different registered domain (generic soft-404 redirect)
        final_domain = _reg_domain(urlparse(resp.url).netloc)
        if final_domain != original_domain:
            log_event(
                f"Link check — redirected off original domain "
                f"({original_domain} -> {final_domain}): {url}"
            )
            return False

        # Check 3: HTTP status and body content
        if resp.status_code != 200:
            log_event(f"Link check failed ({resp.status_code}): {url}")
            return False
        body = resp.text[:5000].lower()
        for marker in _EXPIRED_MARKERS:
            if marker in body:
                log_event(f"Link check — posting expired ('{marker}'): {url}")
                return False

        return True
    except requests.RequestException as e:
        log_event(f"Link check error: {url} — {e}")
        return False


def fetch_gemini_jobs(resume_content, keywords, seen_urls):
    title_list = ", ".join(keywords.get("titles", [])[:8])
    domain_list = ", ".join(keywords.get("domains", [])[:8])
    company_list = ", ".join(GEMINI_COMPANIES)
    # Build a dynamic boolean clause from extracted titles so PM and Eng roles are
    # both included without any hardcoded strings.
    title_clause = " OR ".join(
        f'"{t}"' for t in keywords.get("titles", [])[:10]
    ) or '"Systems Engineer" OR "Product Manager"'

    prompt = f"""You are a job search agent. Search for open job postings and return structured JSON.

CANDIDATE RESUME:
{resume_content}

SEARCH TASK 1 — Named companies:
Search for open roles at each of these companies: {company_list}
Focus on titles matching: {title_list}
Also match domain keywords: {domain_list}
Location: San Diego, CA ONLY (include hybrid; exclude remote-only and non-SD locations)

SEARCH TASK 2 — Broad keyword search:
Search: (site:greenhouse.io OR site:lever.co) AND ({title_clause}) AND "San Diego" 2026
Include any company not already in the named list above.

RULES:
- Every result MUST have a direct URL to the job posting page. Omit any job you cannot find a real URL for.
- Do NOT fabricate or guess URLs. Only include jobs you found via search.
- Prefer canonical posting URLs on the company's ATS (greenhouse.io, lever.co, myworkdayjobs.com, or the official careers site). Do not return search-result or aggregator URLs.
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
            model=_MODEL_SEARCH,
            contents=prompt,
            config=types.GenerateContentConfig(tools=[{"google_search": {}}]),
        )
        clean = response.text.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        data = json.loads(clean)
        candidates = [
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
        # Validate every AI-tier link before it reaches the email.
        # Greenhouse/Lever results skip this — they're already verified by the API.
        verified = []
        for job in candidates:
            if _url_is_live(job["url"]):
                verified.append(job)
            else:
                log_event(
                    f"Dropped unverified Gemini job: {job['company']} — {job['title']}"
                )
        return verified
    except Exception as e:
        log_event(f"Gemini job search error: {e}")
        return []


_PM_MARKERS = {
    "product manager", "product owner", "head of product",
    "director of product", "technical product", "group product",
    "principal product manager", "tpm",
}


def _classify_track(title):
    """Return 'Product' if the title is a PM/PO role, else 'Engineering'."""
    t = title.lower()
    return "Product" if any(m in t for m in _PM_MARKERS) else "Engineering"


def build_email_html(new_jobs):
    now_str = datetime.now().strftime("%A, %B %d, %Y")
    today_str = datetime.now().strftime("%B %d, %Y")
    company_count = len(set(j["company"] for j in new_jobs))
    role_word = "role" if len(new_jobs) == 1 else "roles"
    co_word = "company" if company_count == 1 else "companies"

    # Group jobs by track → company
    by_track = {"Engineering": {}, "Product": {}}
    for job in new_jobs:
        track = _classify_track(job["title"])
        by_track[track].setdefault(job["company"], []).append(job)

    def _render_company_jobs(jobs_for_company):
        rows = ""
        for job in jobs_for_company:
            verified = job.get("source") in ("greenhouse", "lever", "ashby", "workday")
            badge = (
                '<span style="color:#2e7d32;font-size:11px;font-weight:600;'
                'margin-right:10px;">&#10003; Verified</span>'
                if verified else ""
            )
            req_part = (
                f"&nbsp;&nbsp;&middot;&nbsp;&nbsp;Req&nbsp;#{job['req_id']}"
                if job.get("req_id") else ""
            )
            rows += f"""
            <div style="margin-bottom:14px;padding:12px 14px;background:#fafafa;border-left:3px solid #1a1a1a;">
              <p style="margin:0 0 4px;font-size:15px;font-weight:700;color:#1a1a1a;line-height:1.3;">{job['title']}</p>
              <p style="margin:0 0 8px;font-size:13px;color:#666;">{job['location']}{req_part}</p>
              <div>{badge}<a href="{job['url']}" style="background:#1a1a1a;color:#ffffff;padding:4px 14px;border-radius:3px;font-size:12px;text-decoration:none;display:inline-block;">Apply &rarr;</a></div>
            </div>"""
        return rows

    # Track accent colours: Engineering = dark slate, Product = deep navy
    _TRACK_STYLES = {
        "Engineering": ("ENGINEERING ROLES", "#1a1a1a"),
        "Product":     ("PRODUCT ROLES",     "#1a3a5c"),
    }

    company_sections = ""
    for track in ("Engineering", "Product"):
        companies = by_track[track]
        if not companies:
            continue
        track_label, track_color = _TRACK_STYLES[track]
        company_sections += f"""
        <div style="margin-top:30px;margin-bottom:4px;padding:8px 14px;background:{track_color};">
          <p style="margin:0;font-size:11px;letter-spacing:3px;text-transform:uppercase;color:#ffffff;font-weight:700;">{track_label}</p>
        </div>"""
        for company in sorted(companies.keys()):
            job_rows = _render_company_jobs(companies[company])
            company_sections += f"""
        <h2 style="margin:16px 0 10px;font-size:12px;letter-spacing:2px;text-transform:uppercase;color:#1a1a1a;border-bottom:1px solid #e0e0e0;padding-bottom:6px;">{company}</h2>
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
      <p style="margin:0;font-size:11px;color:#aaa;">&#10003; Verified listings sourced directly from company job boards.&nbsp;&nbsp;Other listings via AI-assisted search are link-checked but unofficial &mdash; confirm details before applying.</p>
    </div>

  </div>
</body>
</html>"""


def main():
    log_event("Starting Weekly Job Scraper...")
    wait_for_network()
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
    for slug, name in ASHBY_TARGETS:
        all_jobs.extend(fetch_ashby_jobs(slug, name, keywords))
    for tenant, dc, site, name in WORKDAY_TARGETS:
        all_jobs.extend(fetch_workday_jobs(tenant, dc, site, name, keywords))

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
