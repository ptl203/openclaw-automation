# OpenClaw Automation

A personal macOS automation suite that runs scheduled jobs for daily news, surf reports, smart irrigation, job hunting, and golf booking — all delivered via email.

## Scripts

| Script | Schedule | Description |
|---|---|---|
| `lithrop_ledger.py` | Daily @ 7:00 AM | Generates and emails the Lithrop Ledger: a daily newsletter with market data, world/US/finance/tech news, and an uplifting story. Powered by Gemini AI. |
| `surf_report.py` | Daily @ 5:00 AM & 3:00 PM | Fetches Stormglass wave and wind data for a local break and emails a morning and afternoon surf verdict. |
| `smart_irrigation.py` | Daily @ 5:00 AM | Reads Ecowitt soil moisture sensor; waters Zone 3 via Rachio for 15 minutes if moisture is below 30%. |
| `job_scraper.py` | Wednesdays @ 4:00 PM | Searches San Diego tech/defense job listings via Google, matches against `resume-summary.txt`, and emails a consolidated report of new matches. |
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
python3 setup.py
```

This generates macOS `launchd` plist files, installs them into `~/Library/LaunchAgents/`, and loads them. Run the printed `pmset` command to ensure your Mac wakes before the 5:00 AM jobs.

## Architecture

- **Email delivery:** All jobs send output via SMTP using `utils.py`. Emails use the `[LobsterClaw]` subject prefix.
- **AI models:** `lithrop_ledger.py` uses `gemini-2.5-pro` for full newsletter synthesis. `job_scraper.py` uses `gemini-2.5-flash-lite` for extraction.
- **Scheduling:** Native macOS `launchd` — no cron, no third-party scheduler. Jobs are re-run on wake if they were missed during sleep.
