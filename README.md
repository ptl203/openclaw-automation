# OpenClaw Automation

A personal automation suite that runs scheduled jobs for daily news, surf reports, smart irrigation, job hunting, and timecard reminders — all delivered via email.

Jobs have migrated off local macOS `launchd` onto Claude Code cloud
routines (`claude.ai/code/routines`), which run on Anthropic-managed
infrastructure independent of whether this Mac is awake, logged in, or even
powered on. **Only `log_maintenance.py` still runs as a local launchd
LaunchDaemon** — everything else below is a cloud routine.

Reference copies of every routine's live configuration and prompt are kept in
[`routines/`](routines/), exported 2026-09-27. Those files are a snapshot, not
the source of truth — see `routines/README.md`.

**Current status (verified 2026-09-28 against the live routines):** 4 of the 6
cloud routines are firing. Two are intentionally off: **Job Scraper** (disabled
on purpose) and **Smart Irrigation** (paused until a new garden is planted).
See the Scripts table below.

## Scripts

| Script | Runs as | Schedule (Pacific) | Description |
|---|---|---|---|
| `lithrop_ledger.py` | Cloud routine | Daily @ 7:00 AM | Generates and emails the Lithrop Ledger: a daily newsletter with market data (Yahoo chart API), world/US/finance/tech news, a sports section (PLL standings/schedule + Redwoods news, Padres recap/standing/news), and an uplifting story. The routine's own model writes the newsletter; `--data-only` mode does the deterministic fetching. |
| `surf_compare.py` | Cloud routine | Daily @ 5:00 AM & 3:00 PM | Fetches Stormglass wave data for all beaches in `beaches.json` in parallel plus one regional tide call, scores every hour of the session window (size / wind / period / organization / per-beach swell exposure / tide fit), ranks beaches by their best hour, and emails a GO/NO-GO verdict with current tide and a next-24h outlook. GO requires **both** ≥4★ (score ≥75) **and** a top-beach height ≥ `MIN_GO_HEIGHT_FT` (2.5 ft) — the ideal size band is 3–5 ft, and because size is only 18% of the score the height floor is what actually keeps clean-but-small days out of GO. A gated day still shows its real star rating and the verdict paragraph says "clean but too small" rather than implying poor conditions. `--data-only`/`--render` split the deterministic scoring from the routine's written verdict paragraph. (`surf_report.py` archived to `surf_archive/`) |
| `smart_irrigation.py` | Cloud routine — ⏸️ **paused** | Daily @ 5:00 AM (skips Mon/Thu), not firing | Reads Ecowitt soil moisture sensors; waters Zone 3 via Rachio for 25 minutes if average moisture is below 60%. Reconstructs recent history directly from Ecowitt's own `device/history` API each run (no local file, no git push) to warn when waterings stop moving the sensors and to append a 7-day trend on Sundays. |
| `job_scraper.py` | Cloud routine — **disabled** ⚠️ | Wednesdays @ 4:00 PM (not firing) | Searches San Diego tech/defense job listings, matches against `resume-summary.txt`, and emails a consolidated report of new matches. Its dedup file (`jobs-seen.json`) is committed back to the repo after each run — this was broken for a while (no installed GitHub App, only a read-only OAuth authorization; see `MIGRATION.md`) and confirmed fixed 2026-08-06, which the two `jobs-seen.json` commits on 2026-08-06 and 2026-08-12 demonstrate. **The routine was disabled on purpose** after that last run (`trig_01LKJqozwmUKgK6DXasCg4ey`, `enabled: false`) — confirmed 2026-09-28, not a fault. |
| `timecard_reminder.py` | Cloud routine | Weekdays @ 6:00 PM | Emails a reminder to enter and sign the day's Booz Allen timecard (skips weekends). |
| `log_maintenance.py` | **Local launchd** | Sundays @ 6:00 AM | Rotates `automation.log` (keeps 2 old copies) and truncates oversized launchd stdout/stderr logs. The only job with local state worth rotating, since it's the only one still running locally. |
| `golf_archive/golf_booking.py` | Archived, not scheduled | — | Polls the SD Golf API for Torrey Pines North twilight tee times and attempts to book one. Moved to `golf_archive/` and not currently installed anywhere. |

## Setup

### 1. Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure environment variables

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

Required variables:

```
NEWSDATA_API_KEY        # NewsData.io API key (Lithrop Ledger)
STORMGLASS_API_KEY      # Stormglass API key (surf report)
RACHIO_API_KEY          # Rachio smart sprinkler API key
ECOWITT_APP_KEY         # Ecowitt app key (soil sensor)
ECOWITT_API_KEY         # Ecowitt API key
ECOWITT_MAC             # MAC address of your Ecowitt gateway
RESEND_API_KEY          # Resend API key (https://resend.com) — email delivery
RESEND_FROM             # Send-from address on a Resend-verified domain
TO_EMAIL                # Recipient email address
FOREUP_EMAIL            # ForeUp golf account email (golf_archive/golf_booking.py only — archived, not scheduled)
FOREUP_PASSWORD         # ForeUp golf account password (same)
TWOCAPTCHA_API_KEY      # 2Captcha API key (same)
```

These same values also need to live in the Claude Code cloud environment
(`claude.ai/code` → environment settings → **Environment variables**) that
the routines below use, since cloud sessions don't inherit anything from
this local `.env` file. There's no dedicated secrets store for cloud
environments — anyone using the environment can read these values.

### 3. Cloud routines (Lithrop Ledger, Surf Compare, Job Scraper, Timecard, Irrigation)

These jobs run as Claude Code routines, not launchd. Manage them at
[claude.ai/code/routines](https://claude.ai/code/routines) or with
`/schedule` in the CLI — see `MIGRATION.md` for the full history of how each
one was cut over, its cron expression (in UTC — routines schedule in your
local time on the web UI, but any expression set via the API is UTC), and
the DST-flip table for when Pacific time crosses standard/daylight boundaries.

A routine's config has two halves: the **schedule** (cron, environment, model,
allowed tools, MCP connections) and the **prompt** that drives the run. The
prompt is where the real orchestration lives — it decides which script flags run
in what order, and for the AI-assisted jobs it carries the editorial spec too
(the Lithrop Ledger prompt is ~9.7 KB, larger than most modules in this repo).
None of that is version-controlled by the platform and there is no diff or
history on it, so reference copies live in [`routines/`](routines/). Re-export
them after editing a routine on the web; `routines/README.md` explains how and
lists the known stale wording in the live prompts.

### 4. Schedule the one remaining local job with launchd

```bash
sudo .venv/bin/python setup.py
```

This now only generates the `com.openclaw.log_maintenance` LaunchDaemon —
every other job listed in `setup.py` has been migrated to a cloud routine
and intentionally removed from it. The script installs into
`/Library/LaunchDaemons/` (owned `root:wheel`, running as your user via
`UserName`) and loads it with `launchctl bootstrap system`. `sudo` is
required because system daemons live outside your home directory.

**`setup.py` is additive-only — it never removes a plist for a job that's
been migrated away.** If you ever see more than `com.openclaw.log_maintenance.plist`
under `/Library/LaunchDaemons/`, it's leftover from before a migration and
should be removed manually (`sudo launchctl bootout system/<label>` then
`sudo rm /Library/LaunchDaemons/<label>.plist`), not left running alongside
its cloud-routine replacement — running both fires the job twice.

## Architecture

- **Email delivery:** All jobs send via Resend's HTTPS API (`utils.py`), not SMTP — including the AI-assisted ones, whose routine prompts call `utils.send_email`/`send_html_email` through a `python3 -c` one-liner rather than any MCP mail tool — the Claude Code cloud routine sandbox's network egress is HTTP(S)-only, so raw SMTP sockets fail there regardless of credentials. Emails use the `[LobsterClaw]` subject prefix. `send_email`/`send_html_email`/`notify` all return whether the send actually succeeded, so callers can detect and log a failure instead of assuming success.
- **Scheduling:** Lithrop Ledger, Surf Compare AM/PM, Job Scraper, Timecard Reminder, and Smart Irrigation run as Claude Code cloud routines on Anthropic-managed infrastructure — no GUI-login dependency, no reliance on this Mac being awake. Only `log_maintenance.py` remains a local `launchd` LaunchDaemon, since it's the only job with local state (log files) to manage.
- **Timezone handling:** cloud routines run in a UTC sandbox. `utils.now_local()` (zoneinfo `America/Los_Angeles`, with a fixed-offset fallback for slim images lacking a tzdata database) is used anywhere a script needs to reason about a weekday or calendar date, so a naive `datetime.now()` doesn't silently misfire around the UTC/Pacific day boundary.
- **Persisting state without git push:** a cloud routine gets a fresh repo clone every run with no disk carried over between invocations. `smart_irrigation.py` avoids needing pushed-back state entirely by re-deriving recent sensor history from Ecowitt's own `device/history` API each run (see `MIGRATION.md`). Job Scraper still uses the older pattern: it commits `jobs-seen.json` back after each run. That push is **working** — it was blocked until the Claude GitHub App was installed on 2026-08-06 (root cause and the disproven git-identity theory are both in `MIGRATION.md`), and the commits on 2026-08-06 and 2026-08-12 landed on `origin/main` afterward.
- **Script/model split:** three invocation shapes. *Model in the middle* (Surf Compare, Job Scraper): `--data-only` prints JSON → the routine's model writes the prose or runs its own web search → `--render` reads it back on stdin and prints the final `{subject, body}`. *Model at the end* (Lithrop Ledger): `--data-only` only; the model writes the newsletter HTML from the fetched JSON. *No model* (Timecard, Irrigation): the routine runs the script bare and it emails itself. In every shape the deterministic fetching, scoring, and dedup stay in Python where they're testable, and the model only does the natural-language part. Running any of these scripts with no flags takes a **legacy Gemini fallback path** that is dead in production but still present (`surf_compare.py:386`, `lithrop_ledger.py:879`).
- **Reliability:** Every job calls `wait_for_network()` at startup (a holdover from the launchd/`pmset`-wake days, still harmless in the cloud). `log_event()` redacts API keys from anything written to `automation.log` and timestamps with `now_local()`. Email sends retry once and log their failures.
- **Stormglass quota:** free tier is 10 calls/day (UTC); the surf jobs use 8 (3 beaches + 1 tide, twice daily). A 402 day emails a short quota notice instead of a raw error.
