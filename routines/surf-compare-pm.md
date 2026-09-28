# Routine: Surf Compare PM

Exported 2026-09-28 from `claude.ai/code/routines` via the remote-trigger API.
**This file is reference only — editing it changes nothing.** See `README.md` in this directory.

## Configuration

```json
{
  "routine_id": "trig_01GDMWRNhXs4Y8DvJMEHVXTS",
  "name": "Surf Compare PM",
  "enabled": true,
  "cron_expression": "0 22 * * *",
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
  "last_fired_at": "2026-09-27T22:05:07.946800Z",
  "next_run_at": "2026-09-28T22:03:24.902181335Z"
}
```

## Prompt (verbatim)

```text
You are running the 3PM "afternoon" Surf Compare report for Paul. The repo `openclaw-automation` is already checked out as your working directory.

STEP 1 — Install dependencies:
Run `python3 -m pip install -q -r requirements.txt`. Ignore failures on playwright and 2captcha-python specifically (unrelated archived script). Everything else must install successfully.

STEP 2 — Fetch and score conditions:
Run `python3 surf_compare.py --pm --data-only`.
- If it prints a JSON object with keys go, context, ranked_summary, verdict_instruction — continue to step 3. This step also writes a cache file (.surf_cache_pm.json) that a later step needs; do not delete or modify it.
- If it prints {"error": "..."} , or exits with no output (e.g. Stormglass's daily quota is exhausted, or no beaches could be scored), STOP HERE. The script already sends its own error notification email in that case via SMTP — you do not need to send anything. Just report what happened in your final message and finish.

STEP 3 — Write the verdict:
Using ONLY the data in ranked_summary (Python-computed scores — do not alter, reinterpret, or add to the ratings/rankings), follow verdict_instruction exactly to write a 2-3 sentence verdict paragraph. Output plain text only — no labels, no headers, no markdown, no formatting.

STEP 4 — Render the final report:
Write your verdict text to /tmp/verdict.txt (plain text, just the paragraph, no extra formatting), then run:
python3 surf_compare.py --pm --render < /tmp/verdict.txt
This reuses the cached data from step 2 (no new Stormglass API calls) and prints {"subject": "...", "body": "..."} — the exact final report.

STEP 5 — Send it:
Write the "body" text from step 4 to /tmp/surf.txt, then run this exact command (it reuses the repo's existing, tested SMTP delivery function — do not write your own email-sending code):
python3 -c "from utils import send_email; send_email('<subject from step 4>', open('/tmp/surf.txt').read())"

STEP 6 — Report:
State the GO/NO-GO result and the top-ranked beach in your final message. If anything failed, say exactly what failed.
```
