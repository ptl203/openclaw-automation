import os
import requests
from datetime import datetime, timedelta
from utils import notify, log_event, wait_for_network, now_local, LOCAL_TZ

THRESHOLD = 60          # water when average soil moisture is below this
WATER_SECONDS = 1500    # 25 minutes
RACHIO_ZONE_ID = "18401f7e-b1c4-49b5-a1e4-4c3fdd24c8dc"  # Zone 3
HTTP_TIMEOUT = 15

CHANNELS = ["soil_ch1", "soil_ch2", "soil_ch3", "soil_ch4"]

# Monday (0) and Thursday (3): the script never runs a real check on these
# days regardless of moisture level. fetch_moisture_history() uses this same
# rule to skip those dates entirely, matching what a real history file would
# contain (main() returns before recording anything on these days) — without
# it, a dry Monday would get misclassified as "watered" purely because its
# moisture happened to read below THRESHOLD, distorting the stall check.
NON_WATERING_WEEKDAYS = [0, 3]

# How many past days of trend/stall data to reconstruct from Ecowitt's own
# history, instead of a locally maintained file. This job runs as a cloud
# routine with no persistent disk between runs, so it can't keep its own
# history file the way Job Scraper keeps jobs-seen.json (whose git push has,
# in practice, never once landed since being migrated). Ecowitt already
# retains historical soil-moisture readings server-side, so each run just
# asks for them again rather than depending on state surviving a push.
HISTORY_DAYS = 9
# The latest sample strictly before this local hour is picked per day (see
# fetch_moisture_history for why "nearest" would be wrong), matching this
# script's own ~5 AM run time so derived history reflects the same moment of
# day as a live check.
HISTORY_TARGET_HOUR = 5

# Warn when this many consecutive waterings produce less than MIN_RISE
# points of average-moisture improvement — the sensors aren't seeing the water.
STALL_WATERINGS = 4
STALL_MIN_RISE = 3


def fetch_moisture_history(now_dt, days=HISTORY_DAYS):
    """Reconstruct the last `days` days of average soil moisture from Ecowitt's
    device/history endpoint, in place of a locally maintained history file.

    Returns a list of {"date": "YYYY-MM-DD", "avg": int, "action": "watered"|
    "skipped"} for each of the past `days` days — today is excluded, since
    main() supplies today's entry from the real-time reading it already
    fetched. `action` is derived deterministically from the same threshold
    rule this script uses to trigger watering (avg < THRESHOLD), because
    Ecowitt only knows soil moisture, not whether Rachio's watering request
    actually succeeded that day. On any fetch error, returns [] rather than
    raising — the stall check and weekly trend simply have less to work with
    that run rather than blocking the irrigation check itself.
    """
    start = now_dt - timedelta(days=days)
    params = {
        "application_key": os.getenv("ECOWITT_APP_KEY"),
        "api_key": os.getenv("ECOWITT_API_KEY"),
        "mac": os.getenv("ECOWITT_MAC"),
        "start_date": start.strftime("%Y-%m-%d %H:%M:%S"),
        "end_date": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "call_back": ",".join(CHANNELS),
        "cycle_type": "auto",
    }
    try:
        resp = requests.get("https://api.ecowitt.net/api/v3/device/history", params=params, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            log_event(f"Ecowitt history API error: {data.get('msg')} (code {data.get('code')})")
            return []
        payload = data.get("data") or {}
    except Exception as e:
        log_event(f"Ecowitt history fetch failed: {e}")
        return []

    # Collect every channel's reading at each timestamp: {ts: [val, val, ...]}
    by_timestamp = {}
    for ch in CHANNELS:
        series = (payload.get(ch) or {}).get("soilmoisture", {}).get("list", {})
        for ts_str, val in series.items():
            try:
                ts = int(ts_str)
                v = int(val)
            except (TypeError, ValueError):
                continue
            by_timestamp.setdefault(ts, []).append(v)

    if not by_timestamp:
        return []

    # Bucket by local calendar date, keeping only the LATEST sample strictly
    # before HISTORY_TARGET_HOUR local time for each date — not the nearest
    # overall. Rachio's watering trigger fires immediately after this script's
    # own real-time reading each run, and Ecowitt's history shows moisture
    # spiking right at the target hour (confirmed empirically: value jumps
    # ~2x at exactly 05:00 local on watering days). Picking "nearest" would
    # land on that post-watering spike instead of the pre-watering baseline
    # the live check actually decides on.
    by_date = {}
    for ts, values in by_timestamp.items():
        local_dt = datetime.fromtimestamp(ts, tz=LOCAL_TZ)
        d = local_dt.date()
        target = local_dt.replace(hour=HISTORY_TARGET_HOUR, minute=0, second=0, microsecond=0)
        if local_dt >= target:
            continue
        avg = round(sum(values) / len(values))
        best = by_date.get(d)
        if best is None or local_dt > best[0]:
            by_date[d] = (local_dt, avg)

    today = now_dt.date()
    history = []
    for d in sorted(by_date):
        if d >= today:
            continue  # today's entry comes from the live real-time reading in main()
        if d.weekday() in NON_WATERING_WEEKDAYS:
            continue  # the real script never ran a check on this date at all
        _, avg = by_date[d]
        action = "watered" if avg < THRESHOLD else "skipped"
        history.append({"date": d.isoformat(), "avg": avg, "action": action})
    return history


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
    if now_local().weekday() in NON_WATERING_WEEKDAYS:
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

    now_dt = now_local()
    now = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    today = now_dt.strftime("%Y-%m-%d")
    past_history = fetch_moisture_history(now_dt)
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

    # past_history (from Ecowitt) never includes today, so no dedupe is needed.
    history = past_history + [entry]

    warning = sensor_response_warning(history)
    if warning:
        report_lines.insert(1, "")
        report_lines.insert(1, warning)

    # Sunday: append the weekly trend table
    if now_dt.weekday() == 6:
        report_lines.extend(weekly_trend_lines(history))

    if notify(subject, "\n".join(report_lines)):
        log_event("Irrigation Check finished.")
    else:
        log_event("Irrigation Check finished: email FAILED (see prior log line).")


if __name__ == "__main__":
    main()
