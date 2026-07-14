# OpenClaw Automation

A personal macOS automation suite that runs scheduled jobs for daily news, surf reports, smart irrigation, job hunting, and golf booking — all delivered via email.

## Scripts

| Script | Schedule | Description |
|---|---|---|
| `lithrop_ledger.py` | Daily @ 7:00 AM | Generates and emails the Lithrop Ledger: a daily newsletter with market data, world/US/finance/tech news, a sports section (PLL standings/schedule + Redwoods news, Padres recap/standing/news, FIFA World Cup daily schedule), and an uplifting story. Powered by Gemini AI. |
| `surf_compare.py` | Daily @ 5:00 AM & 3:00 PM | Fetches Stormglass wave data for all beaches in `beaches.json` in parallel plus one regional tide call, scores every hour of the session window (size / wind / period / organization / per-beach swell exposure / tide fit), ranks beaches by their best hour, and emails a GO/NO-GO verdict (GO = ≥4★) with current tide and a next-24h outlook. (`surf_report.py` archived to `surf_archive/`) |
| `smart_irrigation.py` | Daily @ 5:00 AM | Reads Ecowitt soil moisture sensors; waters Zone 3 via Rachio for 25 minutes if average moisture is below 60%. Tracks history in `irrigation-history.json`, warns when waterings stop moving the sensors, and appends a 7-day trend on Sundays. |
| `job_scraper.py` | Wednesdays @ 4:00 PM | Searches San Diego tech/defense job listings via Google, matches against `resume-summary.txt`, and emails a consolidated report of new matches. |
| `timecard_reminder.py` | Weekdays @ 6:00 PM | Emails a reminder to enter and sign the day's Booz Allen timecard (skips weekends). |
| `golf_archive/golf_booking.py` | Sundays @ 6:58 PM | Polls the SD Golf API for Torrey Pines North twilight tee times and attempts to book one. |
| `log_maintenance.py` | Sundays @ 6:00 AM | Rotates `automation.log` (keeps 2 old copies) and truncates oversized launchd stdout/stderr logs. |

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
GEMINI_API_KEY          # Google Gemini API key
NEWSDATA_API_KEY        # NewsData.io API key (Lithrop Ledger)
STORMGLASS_API_KEY      # Stormglass API key (surf report)
RACHIO_API_KEY          # Rachio smart sprinkler API key
ECOWITT_APP_KEY         # Ecowitt app key (soil sensor)
ECOWITT_API_KEY         # Ecowitt API key
ECOWITT_MAC             # MAC address of your Ecowitt gateway
SMTP_SERVER             # SMTP host (e.g. smtp.gmail.com)
SMTP_PORT               # SMTP port (e.g. 587)
EMAIL_ADDRESS           # Sending email address
EMAIL_PASSWORD          # App password for SMTP
TO_EMAIL                # Recipient email address
FOREUP_EMAIL            # ForeUp golf account email
FOREUP_PASSWORD         # ForeUp golf account password
TWOCAPTCHA_API_KEY      # 2Captcha API key (golf booking CAPTCHA)
```

### 3. Schedule jobs with launchd

```bash
sudo .venv/bin/python setup.py
```

This generates macOS `launchd` **LaunchDaemon** plists, installs them into `/Library/LaunchDaemons/` (owned `root:wheel`, running as your user via `UserName`), and loads them with `launchctl bootstrap system`. `sudo` is required because system daemons live outside your home directory. Run the printed `pmset` command to ensure your Mac wakes before the 5:00 AM jobs.

Jobs run as **system LaunchDaemons**, not per-user LaunchAgents. This matters: LaunchAgents only fire while you're logged into a GUI session, so any trigger that falls while the Mac is sitting at the login window (e.g. after an unattended macOS update reboot) is silently dropped, not queued. LaunchDaemons run at boot regardless of login state. To re-run the installer after changing a schedule, just `sudo .venv/bin/python setup.py` again — it's idempotent (bootout + re-bootstrap per job).

## Architecture

- **Email delivery:** All jobs send output via SMTP using `utils.py`. Emails use the `[LobsterClaw]` subject prefix. Credentials come from `.env`, with no Keychain/session dependency, so jobs send mail fine with nobody logged in.
- **AI models:** `lithrop_ledger.py` uses `gemini-3.1-pro-preview` for full newsletter synthesis. `job_scraper.py` uses `gemini-2.5-flash` for resume keyword extraction and `gemini-2.5-pro` (with Google Search tool) for the broad job search.
- **Job board sources:** Verified tiers — Greenhouse, Lever, Ashby, and Workday CXS APIs. AI-assisted tier (Gemini + Google Search) for companies without a usable public board; AI-tier links are validated before emailing.
- **Scheduling:** Native macOS `launchd`, as system LaunchDaemons — no cron, no third-party scheduler, no GUI-login dependency. Jobs still won't fire while the Mac is fully asleep (only while logged out at the login window, which daemons now handle); `pmset repeat wake` covers that case.
- **Reliability:** Every job calls `wait_for_network()` at startup (post-wake runs used to hit DNS failures before Wi-Fi reassociated). `log_event()` redacts API keys from anything written to `automation.log`. Email sends retry once and log their failures.
- **Stormglass quota:** free tier is 10 calls/day (UTC); the surf jobs use 8 (3 beaches + 1 tide, twice daily). A 402 day emails a short quota notice instead of a raw error.
