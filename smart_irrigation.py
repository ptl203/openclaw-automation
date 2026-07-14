import os
import json
import requests
from datetime import datetime
from utils import notify, log_event, wait_for_network

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
    if datetime.now().weekday() in [0, 3]:
        log_event(f"Skipping Irrigation Check: {datetime.now().strftime('%A')} is a non-watering day.")
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

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    today = datetime.now().strftime("%Y-%m-%d")
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

    history.append(entry)
    save_history(history)

    warning = sensor_response_warning(history)
    if warning:
        report_lines.insert(1, "")
        report_lines.insert(1, warning)

    # Sunday: append the weekly trend table
    if datetime.now().weekday() == 6:
        report_lines.extend(weekly_trend_lines(history))

    notify(subject, "\n".join(report_lines))
    log_event("Irrigation Check finished.")


if __name__ == "__main__":
    main()
