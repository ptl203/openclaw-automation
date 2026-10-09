# Routine: Push Credential Probe

Exported 2026-10-08 from `claude.ai/code/routines` via the remote-trigger API.
**This file is reference only — editing it changes nothing.** See `README.md` in this directory.

## Configuration

```json
{
  "routine_id": "trig_013dBLXDQx525BWcSQr4xyeF",
  "name": "Push Credential Probe",
  "enabled": false,
  "cron_expression": "0 0 1 1 *",
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
  "last_fired_at": "2026-08-07T00:58:27.707731Z",
  "next_run_at": "2027-01-01T00:01:24.646173304Z"
}
```

## Prompt (verbatim)

```text
Diagnostic only. Do not send any email. The repo openclaw-automation is your working directory.

Background: the Claude GitHub App was just installed (previously only an OAuth App authorization existed, which is read-only-by-design for cloning). This test checks whether a plain, ambient git push now works with no custom credentials or credential helpers — just whatever identity/token this session already has via the newly-installed App.

STEP 1 — Report the checkout state, verbatim:
  git remote -v
  git status -sb
  git config user.name
  git config user.email
  git symbolic-ref -q HEAD || echo "DETACHED HEAD"
  git rev-parse --short HEAD

STEP 2 — Try an ambient push, with NO credential.helper override and WITHOUT changing git config user.name/user.email from whatever is already set (if nothing is set and commit fails, THEN set user.email to whatever your authenticated GitHub identity actually is — check `git config --get-all user.email` and any hints from `gh api user` if available — do not invent a placeholder identity):
  git checkout -B main
  date -u > .push-probe && git add .push-probe && git commit -m "push probe (post GitHub App install)"
  git push origin HEAD:main
Report the exact exit code and the complete stderr.

STEP 3 — Verify independently, do not trust the exit code alone:
  git fetch origin main
  git rev-parse HEAD
  git rev-parse origin/main
Say PUSH_VERIFIED only if those two hashes are identical.

STEP 4 — Report clearly: did it work (PUSH_VERIFIED) or not? If not, quote the exact error text.
```
