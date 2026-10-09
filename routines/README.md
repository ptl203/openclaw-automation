# Cloud Routine Definitions (reference copies)

Every job in this repo except `log_maintenance.py` runs as a **Claude Code cloud
routine** at [claude.ai/code/routines](https://claude.ai/code/routines). The
scripts are version-controlled here; the *orchestration* — which flags get run in
what order, and the instructions telling the routine's model how to write a
newsletter or a surf verdict — lives only in the routine config on the platform.

**This directory is a reference snapshot of that config, exported 2026-10-08.**

Three of the seven are intentionally not running: Job Scraper (switched off on
purpose), Smart Irrigation (paused until a new garden is planted), and the Push
Credential Probe (spent diagnostic). A fourth, **Timecard Reminder, is also
disabled** — noticed during the 2026-10-08 export, cause unknown; it still has a
`next_run_at`, so it is scheduled but switched off.

> ⚠️ **These files are not the source of truth and are not wired to anything.**
> Editing a file here changes nothing. The live definition lives on the platform.
> A routine edited on the web will silently drift from this snapshot.

## Why keep a copy

The routine prompts are substantial — the Lithrop Ledger prompt is ~9.7 KB of
editorial rules and HTML spec, longer than most modules in this repo — and there
is no platform-side history or diff. If a routine is edited badly or deleted,
this snapshot is the only record of what it said.

## Contents

| File | Routine | Enabled | Cron (UTC) | Pacific intent |
|---|---|---|---|---|
| `lithrop-ledger.md` | Lithrop Ledger | ✅ | `0 14 * * *` | daily 7:00 AM |
| `surf-compare-am.md` | Surf Compare AM | ✅ | `0 12 * * *` | daily 5:00 AM |
| `surf-compare-pm.md` | Surf Compare PM | ✅ | `0 22 * * *` | daily 3:00 PM |
| `smart-irrigation.md` | Smart Irrigation | ⏸️ **paused** | `0 12 * * 0,2,3,5,6` | daily 5:00 AM, skip Mon/Thu |
| `timecard-reminder.md` | Timecard Reminder | ❌ disabled (cause unknown) | `0 1 * * 2-6` | weekdays 6:00 PM |
| `job-scraper.md` | Job Scraper | ❌ disabled (deliberate) | `0 23 * * 3` | Wed 4:00 PM |
| `push-credential-probe.md` | Push Credential Probe | ❌ disabled | `0 0 1 1 *` | diagnostic, one-off |

`routines.json` holds the same data machine-readably (config + verbatim prompt
per routine), for diffing a future export against this one.

All seven share: environment `env_01PGViDA3Cjkh1aMfCf73kyi` ("Default"), model
`claude-sonnet-5`, and source repo `github.com/ptl203/openclaw-automation`.

## Re-exporting after a change

There is no CLI export. In a Claude Code session:

1. `ToolSearch` → load `RemoteTrigger`
2. `RemoteTrigger {action: "list"}` — or `{action: "get", trigger_id: "trig_..."}`
   for one routine
3. Rewrite the file(s) here from the `job_config.ccr.events[0].data.message.content`
   field and commit

Ask Claude to "re-export the routine definitions to `routines/`" and it will do
the above. Worth doing after any routine edit on the web.

## Debugging a run

`RemoteTrigger {action: "list_runs", trigger_id: "..."}` lists recent run
sessions; `{action: "get_run_log", session_id: "..."}` returns a condensed log
of one run. A fire that was refused before a session existed (a disabled
routine, a repo-access failure) leaves no row at all, so an empty list does not
prove the routine never fired — check `enabled` and `next_run_at` with
`{action: "get"}`.

## Known drift between these prompts and the code

Documented rather than silently fixed, since changing a prompt means editing the
live routine, not this file:

- **"SMTP delivery function"** — `surf-compare-am`, `surf-compare-pm`, and
  `job-scraper` all describe `utils.send_email`/`send_html_email` as SMTP.
  Delivery moved to Resend's HTTPS API on 2026-07-24 (commit `964b116`). The
  commands they run are correct; only the wording is stale.
- **Surf AM/PM error path** says the script "sends its own error notification
  email … via SMTP" — same stale wording, same working behavior.
- **`job-scraper` STEP 8** sets a fabricated git identity
  (`routine@openclaw-automation.local`). `MIGRATION.md` records that commit
  identity was *disproven* as the cause of the 403s — the real cause was a
  missing GitHub App installation — so these two lines are leftover from a
  dead theory and are no longer load-bearing.
- **Dependency install (resolved 2026-10-08):** all four prompts that install
  `requirements.txt` used to say failures on playwright and 2captcha-python could
  be ignored while "everything else must install successfully". pip builds the
  whole resolution set before installing any of it, so that was never possible —
  one failed build installs nothing. `feedparser` (the one package needing a
  source build) was removed and all four prompts were rewritten; they now state
  the all-or-nothing behavior, verify imports, and specify a virtualenv retry.
- **Unnecessary MCP connections:** Smart Irrigation and Timecard Reminder each
  have five connectors attached (Gmail, Google Calendar, Monarch, Google Drive,
  Claude Code Remote). Neither uses any of them — both send email in-process via
  Resend. The three routines that do the most work have none attached.
