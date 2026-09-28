# Routine: Smart Irrigation

Exported 2026-09-28 from `claude.ai/code/routines` via the remote-trigger API.
**This file is reference only — editing it changes nothing.** See `README.md` in this directory.

## Configuration

```json
{
  "routine_id": "trig_01WGPQDZ7mtzbqYAYs7GV2us",
  "name": "Smart Irrigation",
  "enabled": false,
  "cron_expression": "0 12 * * 0,2,3,5,6",
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
  "mcp_connections": [
    "Gmail",
    "Google_Calendar",
    "Monarch",
    "Claude_Code_Remote",
    "Google_Drive"
  ],
  "last_fired_at": "2026-09-27T12:15:49.537204Z",
  "next_run_at": "2026-09-29T12:07:11.525570262Z"
}
```

## Prompt (verbatim)

```text
You are running the Smart Irrigation check for Paul. The repo `openclaw-automation` is already checked out as your working directory.

CRITICAL: `smart_irrigation.py` is NOT idempotent. If moisture is below threshold it triggers a real 25-minute watering cycle on a physical sprinkler zone (Rachio zone 3). Run it EXACTLY ONCE. If it fails partway, report the failure — do NOT retry or re-run it.

STEP 1 — Install dependencies:
Run `python3 -m pip install -q requests python-dotenv tzdata`.
Those are the only packages utils.py and smart_irrigation.py import; do NOT install the full requirements.txt (it pulls playwright and other unrelated packages). All three must install successfully. If any fails, STOP and report — do not run the irrigation script with missing dependencies.

STEP 2 — Run the check (ONCE):
Run `python3 smart_irrigation.py`.
It reads the Ecowitt soil sensors, waters Rachio zone 3 for 25 minutes if average moisture is under 60%, reconstructs recent history directly from Ecowitt's own API (no local file, no git operations — do not attempt any git commit or push for this job), and emails the report itself via the repo's tested Resend delivery function. Do not write your own email-sending code and do not edit the script.

STEP 3 — Check the result. Read stdout for one of these:
- Ends with "Sent Email: Smart Irrigation ..." -> success.
- "Email configuration missing for: ..." -> RESEND_API_KEY, RESEND_FROM, or TO_EMAIL missing. Report as a FAILURE.
- "Email failed for ..." -> Resend rejected the send. Report the error, but note whether watering may have already happened (check the log output above the email line for "Irrigation Triggered" or "Skipped").
- No output / an exception -> report the full error. Do not retry.

STEP 4 — Report:
State: the average soil moisture reading, whether watering was triggered, and whether the report email sent. If the printed report contained a line starting with "⚠️ SENSOR CHECK", quote that line in full — that's the one thing Paul actually acts on. Be specific about any failure.
```
