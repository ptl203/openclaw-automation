# Migration Plan: OpenClaw Jobs to Standalone Python Scripts (Email Edition)

## 1. Objective
Export core OpenClaw automated jobs into 5 standalone Python scripts that run independently on macOS. Every job will deliver its output or confirmation via EMAIL. All Telegram-related code and configuration have been removed.

## 2. Architecture & Setup
- **Directory Structure:** All files reside in the `migration/` folder.
- **Documentation:** `MIGRATION.md` (this file) and `README.md`.
- **Dependencies:** `requests`, `google-genai`, `python-dotenv`.
- **Models:** Uses `gemini-3.1-pro-preview` for complex synthesis (Ledger) and `gemini-2.5-flashLite` for extraction/formatting.
- **Environment Variables:** A single `.env` file in `migration/` to store:
  - `GEMINI_API_KEY`
  - `STORMGLASS_API_KEY`
  - `RACHIO_API_KEY`
  - `ECOWITT_APP_KEY`, `ECOWITT_API_KEY`, `ECOWITT_MAC`
  - `SMTP_SERVER`, `SMTP_PORT`, `EMAIL_ADDRESS`, `EMAIL_PASSWORD`, `TO_EMAIL`
- **Shared Utils:** `utils.py` handles console output and secure SMTP email delivery with the `[LobsterClaw]` subject prefix.

## 3. Script Breakdown

1. **`lithrop_ledger.py` (Daily @ 7:00 AM)**
   - **Logic:** Financials (yfinance) + News (NewsData.io) + Sports (ESPN/MLB APIs: PLL standings/schedule, Padres recap/standings, FIFA World Cup daily schedule).
   - **Email:** Sends the full informative newsletter.

2. **`job_scraper.py` (Wednesdays @ 4:00 PM)**
   - **Logic:** SD Tech/Defense job search via Google Search grounding. Matches against local `resume-summary.txt`.
   - **Email:** Consolidates all matches into a single clean report.

3. **`smart_irrigation.py` (Daily @ 5:00 AM)**
   - **Logic:** Ecowitt sensor check (< 30%). Rachio Zone 3 trigger (15 min).
   - **Email:** Sends a report of sensor readings and the action taken (Watered vs. Skipped).

4. **`surf_report.py` (Daily @ 5:00 AM & 3:00 PM)**
   - **Logic:** Stormglass API data conversion and verdict.
   - **Email:** Full surf report formatted for email readability.

5. **`golf_booking.py` (Saturdays @ 6:58 PM)**
   - **Logic:** Polls the SD Golf API for Torrey North twilight times.
   - **Email:** Immediate confirmation of success or detailed failure reason.

## 4. Automation: `launchd` + `pmset`
Native macOS scheduling via `launchd` plists, installed as **system LaunchDaemons**
(`/Library/LaunchDaemons/`, `sudo .venv/bin/python setup.py`) rather than per-user
LaunchAgents.
- **Why daemons, not agents (2026-07-02):** LaunchAgents only run while a GUI
  session is logged in. On 2026-07-01 an unattended macOS auto-update reboot left
  the Mac at the login window for ~11 hours, and every agent-based job scheduled
  in that window (surf PM, job scraper, timecard reminder) was silently dropped —
  launchd doesn't queue a missed calendar trigger, it just waits for the next one.
  LaunchDaemons run at boot independent of login state, which fixes this since
  none of the jobs need a browser/GUI and SMTP auth is already headless (`.env`
  creds via `utils.send_email`).
- **Wake Support:** Required for 5:00 AM jobs. Use `sudo pmset repeat wake MTWRFSU 04:58:00`.
- **Reliability:** Launchd ensures jobs run upon wake if they were missed during sleep.
  Daemons additionally survive being logged out entirely; they still won't fire
  during full system sleep, and a cold boot stuck at the FileVault unlock screen
  is out of scope (nothing runs pre-unlock, daemon or agent).

## 5. Migration to Claude Code Cloud Routines (2026-07 to 2026-08)

Everything above describes the original launchd-only design. Every job except
`log_maintenance.py` has since moved to a Claude Code cloud routine
(`claude.ai/code/routines`), which runs on Anthropic-managed infrastructure
instead of this Mac. Order of migration: Lithrop Ledger, Surf Compare AM/PM,
and Job Scraper first (commit `6ba85c8`); Timecard Reminder and Smart
Irrigation followed later once the pattern was proven out.

- **Why:** LaunchDaemons already fixed the GUI-login dependency (§4 above),
  but still require the Mac to be powered on and awake. Cloud routines remove
  that dependency entirely.
- **Pattern for jobs with an AI step** (Lithrop, Surf Compare, Job Scraper):
  each script gained `--data-only`/`--render` argparse modes so the
  deterministic fetch/scoring code stays identical and testable, while the
  routine's own model handles only the natural-language part (writing a
  newsletter, a verdict paragraph, matching a resume) between them. The
  no-flag path is left as a legacy fallback.
- **Pattern for jobs with no AI step** (Timecard Reminder, Smart Irrigation):
  no `--data-only`/`--render` split — there's no LLM step to isolate from the
  deterministic code, so the routine just runs the script directly and it
  sends its own email exactly as it did under launchd.
- **Email delivery switched from SMTP to Resend's HTTPS API** (commit
  `964b116`): the routine sandbox's network egress is HTTP(S)-only, so
  `smtplib.SMTP()` fails there on every attempt regardless of credentials.
  `utils.send_email`/`send_html_email` now return whether the send actually
  succeeded (previously the return value was discarded, so a missing/invalid
  Resend config would log "email sent" right after logging the actual
  failure — this went unnoticed against the local `.env` for weeks).
- **Timezone handling:** the sandbox runs in UTC. `utils.now_local()` (see
  below) fixes weekday/date logic that would otherwise silently misfire —
  e.g. Timecard Reminder's Friday-6PM-Pacific reminder is already
  Saturday-in-UTC, so a naive `datetime.now().weekday() >= 5` would drop it
  every week.

### `utils.now_local()`

Added because every script that reasons about "is today a weekday" or "what's
today's date" was written assuming the local Mac clock. `now_local()` uses
`zoneinfo.ZoneInfo("America/Los_Angeles")`, falling back to a fixed
`UTC-7` offset if the sandbox image lacks a `tzdata` database. The fallback is
only safe for jobs whose day/weekday logic runs nowhere near midnight — both
Timecard (6 PM) and Irrigation (5 AM) qualify. `requirements.txt` includes
`tzdata` so the real zoneinfo path is used whenever possible.

### Smart Irrigation: deriving history from Ecowitt instead of git push

The first version of this migration gave `smart_irrigation.py` a
locally-maintained `irrigation-history.json`, un-gitignored so the routine
could `git commit` + `git push` it back after each run — mirroring how Job
Scraper persists `jobs-seen.json`. **This was abandoned before shipping.**

Investigating why every push attempt returned a 403 turned up two things
worth recording:

- **Claude Code routines genuinely can push to a repo** (see
  `code.claude.com/docs/en/routines`). Pushes to `claude/`-prefixed branches
  are always accepted; pushes to other branches (e.g. `main`) are rejected
  only if the branch is GitHub-protected, someone else has an open PR from
  it, or **the new commits are authored by someone other than the routine's
  own authenticated identity**. Every push probe run here set a fabricated
  git identity before committing (`user.email
  "routine@openclaw-automation.local"`, later `"Claude
  <noreply@anthropic.com>"`) — never the real identity behind the
  environment's GitHub proxy — which is almost certainly why GitHub returned
  a genuine 403 (confirmed via `X-Github-Request-Id`) rather than a 401. A
  `GH_PUSH_TOKEN` secret was added to the environment to try to work around
  this; it was never actually consulted (git only hands a token to a
  credential helper after a 401 challenge) and was later removed as a red
  herring.
- **This is very likely why Job Scraper's `jobs-seen.json` push has never
  landed either** — same environment, same pattern. Not yet fixed; the
  probable fix is to stop overriding `user.name`/`user.email` before
  committing and push straight to `main` with the routine's real identity.

Rather than chase the git-identity fix for irrigation too, `smart_irrigation.py`
now reconstructs the last 9 days of sensor readings directly from Ecowitt's
own `device/history` API (`fetch_moisture_history()`) each run, which
removes the need for any pushed-back state at all. Two details mattered,
verified against the previously-committed history file's real values before
removing it:

- The sample used for each past day must be the **latest one strictly
  before** the ~5 AM local run time, not the nearest one overall — Rachio's
  watering trigger fires immediately after the script's own reading, and
  Ecowitt's history shows moisture visibly spiking right at that hour on
  watering days. "Nearest" was landing on the post-watering spike instead of
  the pre-watering baseline the live check actually decided on.
- Monday and Thursday (the script's own no-watering days) must be excluded
  from the derived history, not just today — otherwise a dry Monday gets
  misclassified as "watered" purely because its moisture happened to read
  below the 60% threshold, which would distort the consecutive-waterings
  stall check.

The tradeoff: Ecowitt only sees soil moisture, not whether Rachio's watering
request actually succeeded, so a day's `action` is reconstructed
deterministically (`avg < 60%` ⇒ watered) rather than recorded from the real
outcome. This is wrong only on the rare day Rachio's own API call fails —
and that failure already sends its own immediate error email, so it isn't
silently lost, just not reflected with full fidelity in the derived trend.

### Cron reference (UTC — the platform schedules in UTC regardless of what the web UI displays)

| Routine | Intent (Pacific) | Cron now (PDT, UTC−7) | After 2026-11-01 (PST, UTC−8) |
|---|---|---|---|
| Lithrop Ledger | Daily 7:00 AM | `0 14 * * *` | `0 15 * * *` |
| Surf Compare AM | Daily 5:00 AM | `0 12 * * *` | `0 13 * * *` |
| Surf Compare PM | Daily 3:00 PM | `0 22 * * *` | `0 23 * * *` |
| Job Scraper | Wed 4:00 PM | `0 23 * * 3` | `0 0 * * 4` (day-of-week shifts!) |
| Timecard Reminder | Mon–Fri 6:00 PM | `0 1 * * 2-6` | `0 2 * * 2-6` |
| Smart Irrigation | Daily 5 AM, skip Mon/Thu | `0 12 * * 0,2,3,5,6` | `0 13 * * 0,2,3,5,6` |

Two DST traps: Timecard's day-of-week field (`2-6`, Tue–Sat UTC) stays the
same across the flip — only the hour changes, since 6 PM Pacific always
lands on the next UTC calendar day regardless of DST. Job Scraper's
day-of-week field *does* shift, because 4 PM Pacific crosses local midnight
into the next UTC day only during PST, not PDT.
