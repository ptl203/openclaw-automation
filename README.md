# OpenClaw Automation

A personal macOS automation suite that runs scheduled jobs for daily news, surf reports, smart irrigation, job hunting, and golf booking — all delivered via email.

## Scripts

| Script | Schedule | Description |
|---|---|---|
| `lithrop_ledger.py` | Daily @ 7:00 AM | Generates and emails the Lithrop Ledger: a daily newsletter with market data, world/US/finance/tech news, a sports section (PLL standings/schedule + Redwoods news, Padres recap/standing/news, FIFA World Cup daily schedule), and an uplifting story. Powered by Gemini AI. |
| `surf_compare.py` | Daily @ 5:00 AM & 3:00 PM | Fetches Stormglass data for all beaches in `beaches.json` in parallel, ranks them using a deterministic size/wind/period score, and emails a GO/NO-GO verdict with full detail for each beach. (`surf_report.py` archived to `surf_archive/`) |
| `smart_irrigation.py` | Daily @ 5:00 AM | Reads Ecowitt soil moisture sensor; waters Zone 3 via Rachio for 15 minutes if moisture is below 30%. |
| `job_scraper.py` | Wednesdays @ 4:00 PM | Searches San Diego tech/defense job listings via Google, matches against `resume-summary.txt`, and emails a consolidated report of new matches. |
| `timecard_reminder.py` | Daily @ 6:00 PM | Emails a reminder to enter and sign the day's Booz Allen timecard. |
| `golf_archive/golf_booking.py` | Sundays @ 6:58 PM | Polls the SD Golf API for Torrey Pines North twilight tee times and attempts to book one. |
| `log_maintenance.py` | Scheduled | Cleans up old log files. |

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
