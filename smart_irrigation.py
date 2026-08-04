import os
import json
import requests
from datetime import date
from utils import notify, log_event, wait_for_network, now_local

THRESHOLD = 60          # water when average soil moisture is below this
WATER_SECONDS = 1500    # 25 minutes
RACHIO_ZONE_ID = "18401f7e-b1c4-49b5-a1e4-4c3fdd24c8dc"  # Zone 3
HTTP_TIMEOUT = 15

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = os.path.join(BASE_DIR, "irrigation-history.json")
HISTORY_MAX_ENTRIES = 90

# Warn when this many consecutive waterings produce less than MIN_RISE
# points of average-moisture improvement — the sensors aren't seeing the water.
STALL_WATERINGS = 4
STALL_MIN_RISE = 3

# Warn when the newest history entry is older than this many days. The max
# legitimate gap under the Mon/Thu skip schedule is 2 days (Sun->Tue, Wed->Fri);
# this tolerates one missed run before flagging. Exists to catch a broken
# git push (this job runs as a cloud routine and commits its own history file
# back to the repo) — the exact silent-failure mode jobs-seen.json has had
# since it was migrated: if the push stops working, every run since starts
# from a stale seed instead of announcing the problem.
HISTORY_STALE_DAYS = 4


def load_history():
    try:
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE) as f:
                return json.load(f)
    except Exception as e:
        log_event(f"Error loading irrigation history: {e}")
    return []


def save_history(history):
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(history[-HISTORY_MAX_ENTRIES:], f, indent=2)
    except Exception as e:
        log_event(f"Error saving irrigation history: {e}")


def sensor_response_warning(history):
    """Return a warning string if recent waterings aren't moving the sensors.

    Looks at the last STALL_WATERINGS+1 sensor-reading entries: if every one of
    the earlier entries triggered watering and average moisture still hasn't
    risen by STALL_MIN_RISE points, the threshold is effectively unreachable
    (sensor placement/coverage issue, or the zone isn't reaching the sensors).
    """
    entries = [h for h in history if "avg" in h]
    if len(entries) < STALL_WATERINGS + 1:
        return None
    window = entries[-(STALL_WATERINGS + 1):]
    if not all(e.get("action") == "watered" for e in window[:-1]):
        return None
    rise = window[-1]["avg"] - window[0]["avg"]
    if rise >= STALL_MIN_RISE:
        return None
    return (
        f"⚠️ SENSOR CHECK: the last {STALL_WATERINGS} waterings raised average "
        f"moisture by only {rise:+d} points ({window[0]['avg']}% → {window[-1]['avg']}%). "
        f"The {THRESHOLD}% threshold looks unreachable — the sensors may not be in "
        f"the watered zone's coverage, or readings have plateaued for this soil. "
        f"Watering is effectively running every eligible day."
    )


def history_staleness_warning(history, today):
    """Return a warning string if irrigation-history.json hasn't been updated recently.

    Called on the loaded history BEFORE today's entry is appended. A stale
    newest-entry date means the last N runs' history writes never made it back
    into the file the next run reads — most likely a broken `git push` from
    the cloud routine. Surfacing this in the email itself means a broken push
    announces itself within days instead of quietly rotting the stall check
    and weekly trend below.
    """
    entries = [h for h in history if h.get("date")]
    if not entries:
        return None
    last = date.fromisoformat(entries[-1]["date"])
    gap = (today - last).days
    if gap <= HISTORY_STALE_DAYS:
        return None
    return (
        f"⚠️ HISTORY STALE: newest recorded entry is {last} ({gap} days ago). "
        f"irrigation-history.json isn't being updated between runs — the "
        f"sensor-stall check above and any weekly trend below are computed "
        f"from stale data. If this job runs as a cloud routine, its git push "
        f"of irrigation-history.json is likely failing; check that first."
    )


def weekly_trend_lines(history):
    """A 7-day readings/action table, appended to Sunday's email."""
    entries = [h for h in history if "avg" in h][-7:]
    if not entries:
        return []
    lines = ["", "LAST 7 CHECKS", "-----------------------------"]
    for e in entries:
        lines.append(f"{e['date']}: avg {e['avg']}%  — {e['action']}")
    return lines


def main():
    log_event("Starting Irrigation Check...")
    wait_for_network()

    # Skip Monday (0) and Thursday (3)
    if now_local().weekday() in [0, 3]:
        log_event(f"Skipping Irrigation Check: {now_local().strftime('%A')} is a non-watering day.")
        return

    ecowitt_url = "https://api.ecowitt.net/api/v3/device/real_time"
    params = {
        "application_key": os.getenv("ECOWITT_APP_KEY"),
        "api_key": os.getenv("ECOWITT_API_KEY"),
        "mac": os.getenv("ECOWITT_MAC"),
        "call_back": "all"
    }

    if not all([params["application_key"], params["api_key"], params["mac"]]):
        log_event("Irrigation failed: Missing one or more Ecowitt config variables in .env")
        notify("Irrigation Error", "Missing Ecowitt configuration in .env")
        return

    try:
        resp = requests.get(ecowitt_url, params=params, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 0:
            log_event(f"Ecowitt API Error: {data.get('msg')} (Code: {data.get('code')})")
            notify("Irrigation Error", f"Ecowitt API Error: {data.get('msg', 'Unknown error')} (Code: {data.get('code')})")
            return

        payload = data.get("data")
        if not payload:
            log_event("Ecowitt API returned no data in the 'data' field.")
            notify("Irrigation Error", "Ecowitt API returned no data in the 'data' field.")
            return

        readings = {}
        for ch in ["soil_ch1", "soil_ch2", "soil_ch3", "soil_ch4"]:
            if ch in payload and "soilmoisture" in payload[ch]:
                val = payload[ch]["soilmoisture"].get("value")
                try:
                    readings[ch] = int(val.strip('%')) if isinstance(val, str) else int(val)
                except (ValueError, AttributeError):
                    readings[ch] = 0
    except Exception as e:
        log_event(f"Unexpected error during Ecowitt check: {e}")
        notify("Irrigation Error", f"Unexpected error during Ecowitt check: {e}")
        return

    # Step 2: Evaluate
    avg_moisture = round(sum(readings.values()) / len(readings)) if readings else 0
    history = load_history()

    now_dt = now_local()
    now = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    today = now_dt.strftime("%Y-%m-%d")
    staleness_warning = history_staleness_warning(history, now_dt.date())
    report_lines = [f"Irrigation Check - {now}"]
    for ch, val in readings.items():
        report_lines.append(f"{ch}: {val}%")

    entry = {"date": today, "readings": readings, "avg": avg_moisture}

    if avg_moisture >= THRESHOLD:
        entry["action"] = "skipped"
        report_lines.append(f"\nAverage moisture: {avg_moisture}%")
        report_lines.append(f"Status: Skipped. Average moisture ({avg_moisture}%) is at or above {THRESHOLD}% threshold.")
        log_event(f"Irrigation Check finished: Skipped (average {avg_moisture}% is at or above {THRESHOLD}%). Readings: {readings}")
        subject = "Smart Irrigation Status: No Watering Needed"
    else:
        # Step 3: Trigger Rachio
        if not os.getenv("RACHIO_API_KEY"):
            log_event(f"Irrigation failed: Missing RACHIO_API_KEY in .env. Readings: {readings}")
            notify("Irrigation Error", f"Missing Rachio configuration in .env. Readings: {readings}")
            return

        rachio_url = "https://api.rach.io/1/public/zone/start"
        headers = {
            "Authorization": f"Bearer {os.getenv('RACHIO_API_KEY')}",
            "Content-Type": "application/json"
        }
        payload = {"id": RACHIO_ZONE_ID, "duration": WATER_SECONDS}
        report_lines.append(f"\nAverage moisture: {avg_moisture}%")
        try:
            rachio_resp = requests.put(rachio_url, headers=headers, json=payload, timeout=HTTP_TIMEOUT)
            if 200 <= rachio_resp.status_code < 300:
                entry["action"] = "watered"
                report_lines.append(f"Status: Triggered. Average moisture ({avg_moisture}%) is below {THRESHOLD}% threshold.")
                report_lines.append(f"Rachio API Status: HTTP {rachio_resp.status_code}")
                log_event(f"Irrigation Triggered: average {avg_moisture}% below {THRESHOLD}%. HTTP {rachio_resp.status_code}. Readings: {readings}")
                subject = "Smart Irrigation Action: Watering Triggered"
            else:
                entry["action"] = "rachio_error"
                report_lines.append(f"Status: FAILED — Rachio rejected the start request (HTTP {rachio_resp.status_code}).")
                log_event(f"Rachio start rejected: HTTP {rachio_resp.status_code} {rachio_resp.text[:200]}. Readings: {readings}")
                subject = "Irrigation Error: Rachio rejected watering request"
        except Exception as e:
            entry["action"] = "rachio_error"
            log_event(f"Rachio trigger failed: {e}. Readings: {readings}")
            report_lines.append(f"Status: FAILED to trigger Rachio: {e}")
            subject = "Irrigation Error: Rachio trigger failed"

    # Drop any existing entry for today before appending, so a manual re-run
    # (or an overlapping launchd + cloud-routine run) can't double-count a
    # date in the stall check or the 7-day trend table.
    history = [h for h in history if h.get("date") != entry["date"]]
    history.append(entry)
    save_history(history)

    warning = sensor_response_warning(history)
    if warning:
        report_lines.insert(1, "")
        report_lines.insert(1, warning)

    if staleness_warning:
        report_lines.insert(1, "")
        report_lines.insert(1, staleness_warning)

    # Sunday: append the weekly trend table
    if now_dt.weekday() == 6:
        report_lines.extend(weekly_trend_lines(history))

    # Machine-readable footer: durably archives today's data point in the
    # inbox regardless of whether irrigation-history.json's git push
    # succeeds. If the push breaks for a while, the file is reconstructable
    # by grepping these lines out of the sent emails.
    report_lines += ["", "---", "HISTORY ENTRY: " + json.dumps(entry, separators=(",", ":"))]

    if notify(subject, "\n".join(report_lines)):
        log_event("Irrigation Check finished.")
    else:
        log_event("Irrigation Check finished: email FAILED (see prior log line).")


if __name__ == "__main__":
    main()
