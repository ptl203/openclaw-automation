import requests
import os
import json

BASE_URL = "https://foreupsoftware.com"
API_URL = f"{BASE_URL}/index.php/api/booking"

def load_env():
    env = {}
    if os.path.exists(".env"):
        with open(".env") as f:
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    env[k] = v
    return env

def test_discovery():
    env = load_env()
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Origin": BASE_URL,
        "Referer": f"{BASE_URL}/index.php/booking/19347/1468",
    })

    # Login
    print("Logging in...")
    session.get(f"{BASE_URL}/index.php/booking/19347/1468")
    r = session.post(f"{API_URL}/users/login", data={
        "username": env["FOREUP_EMAIL"],
        "password": env["FOREUP_PASSWORD"],
        "booking_class_id": 1468,
        "course_id": 19347,
    })
    login_data = r.json()
    session.headers["Authorization"] = f"Bearer {login_data['jwt']}"

    target_date = "05-01-2026"
    schedule_id = 1468
    
    # Brute force common SD booking class IDs
    potential_classes = [None, 411, 412, 413, 414, 415, 416, 431, 1468, 1469, 1470, 1490]
    
    print(f"\nBrute forcing booking_class_id for {target_date}...")
    print(f"{'Class ID':<10} | {'Times Found':<12} | {'Notes'}")
    print("-" * 40)

    for class_id in potential_classes:
        params = {
            "time": "all",
            "date": target_date,
            "holes": "all",
            "players": "2",
            "schedule_id": str(schedule_id),
            "specials_only": 0,
            "api_key": "no_api_key",
        }
        if class_id:
            params["booking_class_id"] = str(class_id)
        
        try:
            r = session.get(f"{API_URL}/times", params=params)
            times = r.json()
            count = len(times)
            notes = ""
            if count > 0:
                notes = f"Example: {times[0].get('time')} - {times[0].get('course_name')}"
            print(f"{str(class_id):<10} | {count:<12} | {notes}")
        except:
            print(f"{str(class_id):<10} | ERROR")

if __name__ == "__main__":
    test_discovery()
