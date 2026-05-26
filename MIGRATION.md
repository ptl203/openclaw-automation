# Migration Plan: OpenClaw Jobs to Standalone Python Scripts (Email Edition)

## 1. Objective
Export core OpenClaw automated jobs into 5 standalone Python scripts that run independently on macOS. Every job will deliver its output or confirmation via EMAIL. All Telegram-related code and configuration have been removed.

## 2. Architecture & Setup
- **Directory Structure:** All files reside in the `migration/` folder.
- **Documentation:** `MIGRATION.md` (this file) and `README.md`.
- **Dependencies:** `requests`, `google-genai`, `python-dotenv`.
- **Models:** Uses `gemini-2.5-pro` for complex synthesis (Ledger) and `gemini-2.5-flashLite` for extraction/formatting.
- **Environment Variables:** A single `.env` file in `migration/` to store:
  - `GEMINI_API_KEY`
  - `STORMGLASS_API_KEY`
  - `RACHIO_API_KEY`
  - `ECOWITT_APP_KEY`, `ECOWITT_API_KEY`, `ECOWITT_MAC`
  - `SMTP_SERVER`, `SMTP_PORT`, `EMAIL_ADDRESS`, `EMAIL_PASSWORD`, `TO_EMAIL`
- **Shared Utils:** `utils.py` handles console output and secure SMTP email delivery with the `[LobsterClaw]` subject prefix.

## 3. Script Breakdown

1. **`lithrop_ledger.py` (Daily @ 7:00 AM)**
   - **Logic:** Financials (Google Search) + Sports (ESPN API).
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
Native macOS scheduling via `launchd` plists.
- **Wake Support:** Required for 5:00 AM jobs. Use `sudo pmset repeat wake MTWRFSU 04:58:00`.
- **Reliability:** Launchd ensures jobs run upon wake if they were missed during sleep.
