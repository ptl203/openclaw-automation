# Routine: Job Scraper

Exported 2026-10-08 from `claude.ai/code/routines` via the remote-trigger API.
**This file is reference only — editing it changes nothing.** See `README.md` in this directory.

## Configuration

```json
{
  "routine_id": "trig_01LKJqozwmUKgK6DXasCg4ey",
  "name": "Job Scraper",
  "enabled": false,
  "cron_expression": "0 23 * * 3",
  "run_once_at": null,
  "model": "claude-sonnet-5",
  "environment_id": "env_01PGViDA3Cjkh1aMfCf73kyi",
  "repository": [
    "https://github.com/ptl203/openclaw-automation"
  ],
  "allowed_tools": [
    "Bash",
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "WebSearch"
  ],
  "mcp_connections": [],
  "last_fired_at": "2026-08-12T23:06:54.336053Z",
  "next_run_at": "2026-08-19T23:02:44.502452575Z"
}
```

## Prompt (verbatim)

```text
You are running the weekly Job Scraper for Paul. The repo `openclaw-automation` is already checked out as your working directory.

STEP 1 — Install dependencies:
Run `python3 -m pip install -q -r requirements.txt`. Every package in that file ships a prebuilt wheel, so this is expected to succeed as a whole — including playwright and 2captcha-python, which belong to an unrelated archived script but still install cleanly.

pip resolves and builds the whole set before installing any of it, so the outcome is all-or-nothing: if the command reports an error, assume NOTHING was installed, including packages unrelated to the error and packages whose own download succeeded. Do not assume only the named package failed. Confirm the install actually worked before continuing:
python3 -c "import requests, dotenv, google.genai; print('deps ok')"

If that import check fails, do not install packages one at a time, do not edit requirements.txt, and do not `pip install --upgrade pip setuptools wheel` (the sandbox's wheel is Debian-managed and cannot be uninstalled). Instead retry once in a clean virtualenv:
python3 -m venv /tmp/jobs-venv && /tmp/jobs-venv/bin/python -m pip install -q -r requirements.txt
If that succeeds, use `/tmp/jobs-venv/bin/python` in place of `python3` for EVERY later python3 command in this run. If it also fails, stop and report the exact pip error.

Either way, state in your final report whether the plain install worked or the virtualenv fallback was needed, and quote the exact pip error if there was one.

STEP 2 — Fetch Tier-1 data (verified ATS listings) and search context:
Run `python3 job_scraper.py --data-only`. It prints ONE JSON object with: resume_content (Paul's resume text), keywords (titles/skills/domains), tier1_jobs (already-verified postings pulled directly from Greenhouse/Lever/Ashby/Workday APIs — do NOT re-verify or alter these, they're ground truth), seen_urls (a sample of recently-emailed job URLs — never re-include these), search_companies (companies with no usable public ATS API), title_search_clause (a boolean OR clause built from Paul's title keywords).

STEP 3 — Search for new postings:
Using your web_search tool, find NEW open job postings NOT already covered by tier1_jobs and NOT in seen_urls, matching resume_content and keywords:
  a) Named companies: search for open roles at each company in search_companies. Focus on titles matching keywords.titles / keywords.domains.
  b) Broad search: run something equivalent to `(site:greenhouse.io OR site:lever.co) AND (title_search_clause) AND "San Diego" 2026`, to catch companies not in the named list.
Rules — apply strictly:
  - Location: San Diego, CA area ONLY (hybrid OK; exclude remote-only and non-SD locations).
  - Every result MUST have a real, direct URL to the job posting page that your search actually returned — NEVER fabricate or guess a URL.
  - Prefer canonical ATS posting URLs (greenhouse.io, lever.co, myworkdayjobs.com, or the company's official careers page) — do not return generic search-result or aggregator links.
  - Skip anything whose URL is already in seen_urls or already present in tier1_jobs.
Build a JSON array of candidates, each: {"company": "...", "title": "...", "location": "...", "url": "...", "req_id": "..." (or ""), "source": "web_search"}.

STEP 4 — Validate the candidate URLs:
Write the JSON array from step 3 to /tmp/candidates.json, then run:
python3 job_scraper.py --validate-urls < /tmp/candidates.json
This link-checks each URL and prints only the confirmed-live subset as JSON. Use ONLY that filtered output going forward — this is the same "never email a dead posting" check the script has always used.

STEP 5 — Render the final list:
Combine tier1_jobs (from step 2, unchanged) with the validated candidates (from step 4) into one JSON array. Write it to /tmp/combined.json, then run:
python3 job_scraper.py --render < /tmp/combined.json
This dedups against the repo's jobs-seen.json, updates that file on disk, and prints {"subject": "...", "body": "...", "new_jobs": N}.

STEP 6 — If new_jobs is 0:
Stop here. Do not send an email and do not commit/push anything (jobs-seen.json is unchanged when there's nothing new). Report that in your final message and finish.

STEP 7 — Send the email (only if new_jobs > 0):
Write the "body" HTML from step 5 to /tmp/jobs.html, then run this exact command (it reuses the repo's existing, tested SMTP delivery function — do not write your own email-sending code):
python3 -c "from utils import send_html_email; send_html_email('<subject from step 5>', open('/tmp/jobs.html').read())"

STEP 8 — Persist the dedup state (only if new_jobs > 0):
Commit and push the updated jobs-seen.json so next week's run has this week's postings marked as seen:
git config user.email "routine@openclaw-automation.local"
git config user.name "Job Scraper Routine"
git add jobs-seen.json
git commit -m "Job Scraper: mark <new_jobs> new postings as seen"
git push
If the push fails (e.g. no write access), say so clearly in your final report — the email will still have been sent either way, but next week's run may re-surface the same postings until push access is fixed.

STEP 9 — Report:
State how many new roles were found, at which companies, and whether the email send and git push both succeeded. Be specific about any failure.
```
