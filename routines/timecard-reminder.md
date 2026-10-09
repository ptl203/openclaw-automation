# Routine: Timecard Reminder

Exported 2026-10-08 from `claude.ai/code/routines` via the remote-trigger API.
**This file is reference only — editing it changes nothing.** See `README.md` in this directory.

## Configuration

```json
{
  "routine_id": "trig_015MTNH35B8Rq7dpkrACg3wt",
  "name": "Timecard Reminder",
  "enabled": false,
  "cron_expression": "0 1 * * 2-6",
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
  "last_fired_at": "2026-10-09T01:16:00.398641Z",
  "next_run_at": "2026-10-10T01:15:10.581238962Z"
}
```

## Prompt (verbatim)

```text
You are running the weekday Timecard Reminder for Paul. The repo `openclaw-automation` is already checked out as your working directory. This job sends a fixed reminder email — there is no data to fetch and nothing for you to write or reword.

STEP 1 — Install dependencies:
Run `python3 -m pip install -q requests python-dotenv tzdata`.
Those are the only packages utils.py imports; do NOT install the full requirements.txt (it pulls playwright, which is slow and unrelated). All three must install successfully.

STEP 2 — Run the reminder:
Run `python3 timecard_reminder.py`.
The script decides for itself whether today is a weekday in America/Los_Angeles and sends the email via the repo's tested Resend delivery function. Do not write your own email-sending code and do not edit the script.

STEP 3 — Check the result. Read stdout for exactly one of these:
- "Sent Email: ACTION REQUIRED: Enter/Sign your Booz Allen Timecard" -> success.
- "Email configuration missing for: ..." -> RESEND_API_KEY, RESEND_FROM, or TO_EMAIL is missing from the environment. Report this as a FAILURE and name the likely missing var.
- "Email failed for ..." -> Resend rejected the send after two attempts. Report the error.
- "Skipping Timecard Reminder: weekend." -> the script saw a weekend in Pacific time. This should never happen on this schedule (it fires Tue-Sat UTC = Mon-Fri Pacific). If you see it, report it as a SCHEDULING BUG, not as normal operation.

STEP 4 — Report:
State in one line whether the reminder was sent, and the Pacific-time date the email body was addressed to. If anything failed, say exactly what failed.
```
