import os
import requests
from datetime import datetime
from utils import notify, log_event

def main():
    log_event("Starting Irrigation Check...")
    
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
        resp = requests.get(ecowitt_url, params=params)
        resp.raise_for_status()
        data = resp.json()
        # print(f"DEBUG: Ecowitt Data: {data}")
        
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
    threshold = 25
    avg_moisture = round(sum(readings.values()) / len(readings)) if readings else 0

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report_lines = [f"Irrigation Check - {now}"]
    for ch, val in readings.items():
        report_lines.append(f"{ch}: {val}%")

    if avg_moisture >= threshold:
        report_lines.append(f"\nAverage moisture: {avg_moisture}%")
        report_lines.append(f"Status: Skipped. Average moisture ({avg_moisture}%) is at or above 25% threshold.")
        log_event(f"Irrigation Check finished: Skipped (average {avg_moisture}% is at or above 25%). Readings: {readings}")
        notify("Smart Irrigation Status: No Watering Needed", "\n".join(report_lines))
        return

    # Step 3: Trigger Rachio
    rachio_url = "https://api.rach.io/1/public/zone/start"
    headers = {
        "Authorization": f"Bearer {os.getenv('RACHIO_API_KEY')}",
        "Content-Type": "application/json"
    }

    if not os.getenv("RACHIO_API_KEY"):
         log_event(f"Irrigation failed: Missing RACHIO_API_KEY in .env. Readings: {readings}")
         notify("Irrigation Error", f"Missing Rachio configuration in .env. Readings: {readings}")
         return

    payload = {
        "id": "18401f7e-b1c4-49b5-a1e4-4c3fdd24c8dc",
        "duration": 900
    }
    try:
        rachio_resp = requests.put(rachio_url, headers=headers, json=payload)
        status_code = rachio_resp.status_code
        report_lines.append(f"\nAverage moisture: {avg_moisture}%")
        report_lines.append(f"Status: Triggered. Average moisture ({avg_moisture}%) is below 25% threshold.")
        report_lines.append(f"Rachio API Status: HTTP {status_code}")
        log_event(f"Irrigation Triggered: average {avg_moisture}% below 25%. HTTP {status_code}. Readings: {readings}")
    except Exception as e:
        log_event(f"Rachio trigger failed: {e}. Readings: {readings}")
        report_lines.append(f"\nStatus: Failed to trigger Rachio: {e}")

    notify("Smart Irrigation Action: Watering Triggered", "\n".join(report_lines))
    log_event("Irrigation Check finished.")

if __name__ == "__main__":
    main()
