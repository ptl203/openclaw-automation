#!/usr/bin/env python3
"""
ForeUP Golf Booking API Client — San Diego Municipal Courses
Used by LobsterClaw's sd-golf skill for tee time queries and booking.

Usage:
  python3 foreup.py login
  python3 foreup.py times --schedule_id 1468 --date 04-08-2026 [--players 4]
  python3 foreup.py book --schedule_id 1468 --date 04-06-2026 --time "2026-04-06 17:42" --players 1

Environment variables (from ~/.openclaw/.env):
  FOREUP_EMAIL, FOREUP_PASSWORD
"""

import requests, json, os, sys, argparse
from datetime import datetime, timedelta

# Course/schedule mapping
COURSES = {
    "torrey-north":  {"schedule_id": 1468, "course_id": 19347, "name": "Torrey Pines North"},
    "torrey-south":  {"schedule_id": 1487, "course_id": 19347, "name": "Torrey Pines South"},
    "mission-bay":   {"schedule_id": 1469, "course_id": 19346, "name": "Mission Bay"},
    "balboa-18":     {"schedule_id": 1470, "course_id": 19348, "name": "Balboa Park 18-Hole"},
    "balboa-9":      {"schedule_id": 1490, "course_id": 19348, "name": "Balboa Park 9-Hole"},
}

# Booking class buttons (for browser automation)
BOOKING_CLASSES = {
    "resident-0-7":      "Resident (0 - 7 Days)",
    "resident-8-90":     "Resident (8 - 90 Days)",
    "resident-back9-8-90": "Resident Back 9 (8-90 Days Sat/Sun/Holi.)",
    "resident-back9-0-7":  "Resident Back 9 (0-7 Days Sat/Sun/Holi.)",
    "non-resident":      "Non Resident (0 - 90 Days)",
}

BASE_URL = "https://foreupsoftware.com"
API_URL = f"{BASE_URL}/index.php/api/booking"
BOOKING_URL = f"{BASE_URL}/index.php/booking/19347/1468"

def load_env():
    """Load credentials from environment or local .env"""
    env = {}
    for key in ["FOREUP_EMAIL", "FOREUP_PASSWORD"]:
        val = os.getenv(key)
        if val:
            env[key] = val
    if len(env) == 2:
        return env

    # Fallback to .env in the migration root
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k] = v
    return env

def create_session():
    """Create a requests session with proper headers"""
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
        "Origin": BASE_URL,
        "Referer": BOOKING_URL,
    })
    return s

def login(session=None):
    """Login to ForeUP, return (session, login_data)"""
    env = load_env()
    s = session or create_session()
    
    # Get session cookie first
    s.get(BOOKING_URL)
    
    r = s.post(f"{API_URL}/users/login", data={
        "username": env["FOREUP_EMAIL"],
        "password": env["FOREUP_PASSWORD"],
        "booking_class_id": 1468,
        "course_id": 19347,
    })
    
    if r.status_code != 200 or "jwt" not in r.json():
        data = r.json() if r.status_code == 200 else {}
        print(json.dumps({"success": False, "error": "Login failed", "detail": data.get("msg", str(r.text)[:200])}))
        sys.exit(1)
    
    data = r.json()
    s.headers["Authorization"] = f"Bearer {data['jwt']}"
    return s, data

def get_times(session, schedule_id, date, players=1, holes="all"):
    """Query available tee times. Date format: MM-DD-YYYY"""
    r = session.get(f"{API_URL}/times", params={
        "time": "all",
        "date": date,
        "holes": holes,
        "players": str(players),
        "schedule_id": str(schedule_id),
        "specials_only": 0,
        "api_key": "",
    })
    return r.json()

def find_closest_time(times, target_time=None, min_spots=1):
    """Find the tee time closest to target_time with enough spots.
    target_time: "HH:MM" in 24h format, or None for earliest.
    """
    available = [t for t in times if t.get("available_spots", 0) >= min_spots]
    if not available:
        return None
    
    if target_time is None:
        return available[0]  # earliest
    
    target_h, target_m = map(int, target_time.split(":"))
    target_minutes = target_h * 60 + target_m
    
    def time_diff(t):
        time_str = t["time"]  # "2026-04-06 17:42"
        h, m = int(time_str[11:13]), int(time_str[14:16])
        return abs((h * 60 + m) - target_minutes)
    
    return min(available, key=time_diff)

# Twilight start times for 2026
TWILIGHT_SCHEDULE = [
    ("01-01", "02-01", "12:00"),
    ("02-03", "03-07", "13:00"),
    ("03-08", "04-06", "14:00"),
    ("04-07", "05-03", "14:30"),
    ("05-04", "05-31", "15:00"),
    ("06-01", "07-26", "15:30"),
    ("07-27", "08-09", "15:00"),
    ("08-10", "09-06", "14:30"),
    ("09-07", "09-27", "14:00"),
    ("09-28", "11-01", "13:30"),
    ("11-02", "12-31", "12:00"),
]

def get_twilight_start(date_str):
    """Get twilight start time for a given date (MM-DD format or MM-DD-YYYY)."""
    md = date_str[:5]  # MM-DD
    for start, end, twilight_time in TWILIGHT_SCHEDULE:
        if start <= md <= end:
            return twilight_time
    return "14:00"  # default

def find_earliest_twilight(times, date_str, min_spots=1):
    """Find earliest tee time at or after twilight start."""
    twilight_start = get_twilight_start(date_str)
    tw_h, tw_m = map(int, twilight_start.split(":"))
    tw_minutes = tw_h * 60 + tw_m
    
    for t in times:
        if t.get("available_spots", 0) < min_spots:
            continue
        time_str = t["time"]
        h, m = int(time_str[11:13]), int(time_str[14:16])
        if (h * 60 + m) >= tw_minutes:
            return t
    return None

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ForeUP Golf Booking CLI")
    parser.add_argument("action", choices=["login", "times", "courses", "twilight-start"])
    parser.add_argument("--schedule_id", type=int)
    parser.add_argument("--course", type=str, help="Course name shorthand")
    parser.add_argument("--date", type=str, help="Date in MM-DD-YYYY format")
    parser.add_argument("--players", type=int, default=1)
    parser.add_argument("--target_time", type=str, help="Target time HH:MM for closest match")
    parser.add_argument("--twilight", action="store_true", help="Find earliest twilight time")
    parser.add_argument("--min_spots", type=int, default=1)
    args = parser.parse_args()

    if args.action == "courses":
        for key, info in COURSES.items():
            print(f"  {key}: {info['name']} (schedule_id={info['schedule_id']})")
        sys.exit(0)

    if args.action == "twilight-start":
        date = args.date or datetime.now().strftime("%m-%d-%Y")
        print(get_twilight_start(date))
        sys.exit(0)

    if args.action == "login":
        s, data = login()
        print(json.dumps({
            "success": True,
            "name": f"{data['first_name']} {data['last_name']}",
            "person_id": data["person_id"],
            "resident_pass": bool(data.get("passes")),
            "reservations": len(data.get("reservations", [])),
        }, indent=2))

    elif args.action == "times":
        schedule_id = args.schedule_id
        if args.course:
            course = COURSES.get(args.course)
            if not course:
                print(f"Unknown course: {args.course}. Options: {', '.join(COURSES.keys())}")
                sys.exit(1)
            schedule_id = course["schedule_id"]
        
        if not schedule_id:
            print("--schedule_id or --course required")
            sys.exit(1)
        
        if not args.date:
            print("--date required (MM-DD-YYYY)")
            sys.exit(1)
        
        s, _ = login()
        times = get_times(s, schedule_id, args.date, args.players)
        
        if args.twilight:
            best = find_earliest_twilight(times, args.date, args.min_spots)
            if best:
                print(json.dumps({"found": True, "time": best["time"], "spots": best["available_spots"], 
                                  "fee": best["green_fee"], "course": best.get("schedule_name", "")}, indent=2))
            else:
                print(json.dumps({"found": False, "total_times": len(times)}))
        elif args.target_time:
            best = find_closest_time(times, args.target_time, args.min_spots)
            if best:
                print(json.dumps({"found": True, "time": best["time"], "spots": best["available_spots"],
                                  "fee": best["green_fee"], "course": best.get("schedule_name", "")}, indent=2))
            else:
                print(json.dumps({"found": False, "total_times": len(times)}))
        else:
            print(json.dumps({
                "total": len(times),
                "times": [{"time": t["time"], "spots": t["available_spots"], "fee": t["green_fee"],
                           "holes": t["holes"], "course": t.get("schedule_name", "")} for t in times]
            }, indent=2))
