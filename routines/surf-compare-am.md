# Routine: Surf Compare AM

Exported 2026-10-08 from `claude.ai/code/routines` via the remote-trigger API.
**This file is reference only — editing it changes nothing.** See `README.md` in this directory.

## Configuration

```json
{
  "routine_id": "trig_01HyLkz3idKtaYPKDQfPbXJb",
  "name": "Surf Compare AM",
  "enabled": true,
  "cron_expression": "0 12 * * *",
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
    "Grep"
  ],
  "mcp_connections": [],
  "last_fired_at": "2026-10-08T12:16:03.586138Z",
  "next_run_at": "2026-10-09T12:14:47.855482137Z"
}
```

## Prompt (verbatim)

```text
You are running the 5AM "dawn patrol" Surf Compare report for Paul. The repo `openclaw-automation` is already checked out as your working directory.

STEP 1 — Install dependencies:
Run `python3 -m pip install -q -r requirements.txt`. Every package in that file ships a prebuilt wheel, so this is expected to succeed as a whole — including playwright and 2captcha-python, which belong to an unrelated archived script but still install cleanly.

pip resolves and builds the whole set before installing any of it, so the outcome is all-or-nothing: if the command reports an error, assume NOTHING was installed, including packages unrelated to the error and packages whose own download succeeded. Do not assume only the named package failed. Confirm the install actually worked before continuing:
python3 -c "import requests, dotenv, google.genai; print('deps ok')"

If that import check fails, do not install packages one at a time, do not edit requirements.txt, and do not `pip install --upgrade pip setuptools wheel` (the sandbox's wheel is Debian-managed and cannot be uninstalled). Instead retry once in a clean virtualenv:
python3 -m venv /tmp/surf-venv && /tmp/surf-venv/bin/python -m pip install -q -r requirements.txt
If that succeeds, use `/tmp/surf-venv/bin/python` in place of `python3` for EVERY later python3 command in this run. If it also fails, stop and report the exact pip error.

Either way, state in your final report whether the plain install worked or the virtualenv fallback was needed, and quote the exact pip error if there was one.

STEP 2 — Fetch and score conditions:
Run `python3 surf_compare.py --am --data-only`.
- If it prints a JSON object with keys go, context, ranked_summary, verdict_instruction — continue to step 3. This step also writes a cache file (.surf_cache_am.json) that a later step needs; do not delete or modify it.
- If it prints {"error": "..."} , or exits with no output (e.g. Stormglass's daily quota is exhausted, or no beaches could be scored), STOP HERE. The script already sends its own error notification email in that case via SMTP — you do not need to send anything. Just report what happened in your final message and finish.

STEP 3 — Write the verdict:
Using ONLY the data in ranked_summary (Python-computed scores — do not alter, reinterpret, or add to the ratings/rankings), follow verdict_instruction exactly to write a 2-3 sentence verdict paragraph. Output plain text only — no labels, no headers, no markdown, no formatting.

STEP 4 — Render the final report:
Write your verdict text to /tmp/verdict.txt (plain text, just the paragraph, no extra formatting), then run:
python3 surf_compare.py --am --render < /tmp/verdict.txt
This reuses the cached data from step 2 (no new Stormglass API calls) and prints {"subject": "...", "body": "..."} — the exact final report.

STEP 5 — Send it:
Write the "body" text from step 4 to /tmp/surf.txt, then run this exact command (it reuses the repo's existing, tested SMTP delivery function — do not write your own email-sending code):
python3 -c "from utils import send_email; send_email('<subject from step 4>', open('/tmp/surf.txt').read())"

STEP 6 — Report:
State the GO/NO-GO result and the top-ranked beach in your final message. If anything failed, say exactly what failed.
```
