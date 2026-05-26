import requests
import json
import os
import sys
from datetime import datetime

# Mimic the main script's configuration
BASE_URL = "https://foreupsoftware.com"
API_URL = f"{BASE_URL}/index.php/api/booking"

def load_env():
    env = {}
    # Check project root for .env
    local_env = os.path.join(os.getcwd(), ".env")
    if os.path.exists(local_env):
        with open(local_env) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k] = v
    return env

def test_api():
    env = load_env()
    if not env.get("FOREUP_EMAIL") or not env.get("FOREUP_PASSWORD"):
        print("Error: Missing FOREUP_EMAIL or FOREUP_PASSWORD in .env")
        return

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Origin": BASE_URL,
        "Referer": f"{BASE_URL}/index.php/booking/19347/1468",
    })

    print("1. Establishing session cookies...")
    session.get(f"{BASE_URL}/index.php/booking/19347/1468")

    print(f"2. Attempting login for {env['FOREUP_EMAIL']}...")
    login_payload = {
        "username": env["FOREUP_EMAIL"],
        "password": env["FOREUP_PASSWORD"],
        "booking_class_id": 1468, # Torrey North/South class
        "course_id": 19347,      # Torrey Pines course ID
    }
    
    r = session.post(f"{API_URL}/users/login", data=login_payload)
    login_data = r.json()
    
    if "jwt" not in login_data:
        print(f"FAILED: Login failed. Response: {login_data}")
        return

    session.headers["Authorization"] = f"Bearer {login_data['jwt']}"
    print(f"SUCCESS: Logged in as {login_data.get('first_name')} {login_data.get('last_name')}")
    print(f"Assigned Booking Class ID: {login_data.get('booking_class_id')}")

    # The date for the check
    target_date = "05-01-2026"
    schedule_id = 1468 # Torrey
    players = 2

    print(f"3. Fetching times for {target_date}...")
    # These params are exactly what the main script uses
    params = {
        "time": "all", 
        "date": target_date, 
        "holes": "all", 
        "players": str(players),
        "schedule_id": str(schedule_id), 
        "specials_only": 0, 
        "api_key": "no_api_key",
        "booking_class_id": login_data.get('booking_class_id', "1468")
    }
    
    r = session.get(f"{API_URL}/times", params=params)
    
    if r.status_code != 200:
        print(f"FAILED: API Status {r.status_code}")
        print(r.text)
        return

    times = r.json()
    print(f"SUCCESS: Received {len(times)} times.")

    if len(times) > 0:
        print("\nFirst 5 available times:")
        for t in times[:5]:
            # Clean up the output to see relevant info
            print(f" - {t.get('time')} | Course: {t.get('course_name')} | Spots: {t.get('available_spots')} | Fee: {t.get('green_fee')}")
    else:
        print("\nNo times found. This is the issue we are troubleshooting.")
        # If no times, let's try a broader search without a specific booking_class_id to see if that's the filter
        print("\nAttempting broad search (no booking_class_id)...")
        del params["booking_class_id"]
        r2 = session.get(f"{API_URL}/times", params=params)
        times2 = r2.json()
        print(f"Broad search found {len(times2)} times.")

if __name__ == "__main__":
    test_api()
