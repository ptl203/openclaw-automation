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
