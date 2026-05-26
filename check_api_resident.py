import requests
import os
import json
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://foreupsoftware.com"
API_URL = f"{BASE_URL}/index.php/api/booking"

def run():
    email = os.getenv("FOREUP_EMAIL")
    password = os.getenv("FOREUP_PASSWORD")
    
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    })
    
    # 1. Login to get JWT
    print("Logging in...")
    r = s.post(f"{API_URL}/users/login", data={
        "username": email,
        "password": password,
        "booking_class_id": 1468,
        "course_id": 19347,
    })
    login_data = r.json()
    if "jwt" not in login_data:
        print("Login failed:", login_data)
        return
    
    token = login_data["jwt"]
    s.headers["Authorization"] = f"Bearer {token}"
    print(f"Logged in as {login_data['first_name']}. Token acquired.")

    # 2. Query times for April 18th with Resident filter
    date = "04-18-2026"
    print(f"Querying times for {date} with booking_class_id=1468...")
    
    params = {
        "time": "all",
        "date": date,
        "holes": "all",
        "players": "2",
        "schedule_id": "1468",
        "booking_class_id": "1468", # Resident (0-7 Days)
        "specials_only": "0",
        "api_key": ""
    }
    
    r = s.get(f"{API_URL}/times", params=params)
    times = r.json()
    
    if not isinstance(times, list):
        print("Error fetching times:", times)
        return

    print(f"Found {len(times)} times.")
    for t in times[:10]:
        print(f"Time: {t['time']} | Spots: {t['available_spots']} | Fee: {t['green_fee']}")

if __name__ == "__main__":
    run()
