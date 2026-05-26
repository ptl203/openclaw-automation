#!/usr/bin/env python3
"""
Torrey Pines North Booker — Browser-First Version
Navigates directly via browser to discover and book tee times.
"""

import json, os, sys, time, argparse, re
from datetime import datetime
from playwright.sync_api import sync_playwright
from twocaptcha import TwoCaptcha

# ─── Config ────────────────────────────────────────────────────────────────────

COURSE_INFO = {
    "name": "Torrey Pines North",
    "booking_url": "https://foreupsoftware.com/index.php/booking/19347/1468",
    "facility_option": "Torrey Pines North"
}

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

# ─── Helpers ───────────────────────────────────────────────────────────────────

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", file=sys.stderr)

def load_env():
    env = {}
    base_dir = os.path.dirname(os.path.abspath(__file__))
    local_env = os.path.join(base_dir, "..", ".env")
    if os.path.exists(local_env):
        with open(local_env) as f:
            for line in f:
                if "=" in line and not line.startswith("#"):
                    k, v = line.strip().split("=", 1)
                    env[k] = v
    return env

def get_twilight_start(date_str):
    md = date_str[:5]
    for start, end, tw_time in TWILIGHT_SCHEDULE:
        if start <= md <= end:
            return tw_time
    return "14:00"

def get_chrome_path():
    base_path = os.path.expanduser("~/Library/Caches/ms-playwright")
    if os.path.exists(base_path):
        for d in os.listdir(base_path):
            if d.startswith("chromium-") and not d.startswith("chromium_headless_shell"):
                possible_path = os.path.join(base_path, d, "chrome-mac-arm64", "Google Chrome for Testing.app", "Contents", "MacOS", "Google Chrome for Testing")
                if os.path.exists(possible_path):
                    return possible_path
    return None

# ─── Browser Logic ─────────────────────────────────────────────────────────────

def navigate_and_login(page, env):
    log(f"Navigating to {COURSE_INFO['booking_url']}...")
    page.goto(f"{COURSE_INFO['booking_url']}#/teetimes", wait_until="networkidle")
    
    login_field = page.wait_for_selector('input[placeholder="Username"], input[placeholder="Email"]', timeout=10000)
    if login_field:
        log("Logging in...")
        login_field.fill(env["FOREUP_EMAIL"])
        page.fill('input[placeholder="Password"]', env["FOREUP_PASSWORD"])
        page.click('input[type="submit"], button:has-text("SIGN IN")')
        page.wait_for_load_state("networkidle")
        time.sleep(2)
        if "#/teetimes" not in page.url:
            page.goto(f"{COURSE_INFO['booking_url']}#/teetimes", wait_until="networkidle")

def select_resident_and_date(page, date_str):
    log("Selecting Resident class...")
    try:
        page.wait_for_selector('button:has-text("Resident (0 - 7 Days)")', timeout=10000)
        page.click('button:has-text("Resident (0 - 7 Days)")')
        page.wait_for_load_state("networkidle")
        time.sleep(1)
    except:
        log("Resident button not found, checking if already selected...")

    log(f"Selecting date {date_str}...")
    try:
        dt = datetime.strptime(date_str, "%m-%d-%Y")
    except:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    
    month_name = dt.strftime("%B") # "May"
    day_num = dt.day
    # Try multiple patterns: "May 1", "May 1st", "1st"
    patterns = [f"{month_name} {day_num}", f"{day_num}th", f"{day_num}st", f"{day_num}rd", f"{day_num}nd"]
    
    success = page.evaluate(f'''(patterns) => {{
        const selects = document.querySelectorAll('select');
        for (let sel of selects) {{
            const options = Array.from(sel.options);
            for (let pattern of patterns) {{
                for (let opt of options) {{
                    if (opt.text.includes(pattern)) {{
                        sel.value = opt.value;
                        sel.dispatchEvent(new Event('change', {{bubbles: true}}));
                        return opt.text;
                    }}
                }}
            }}
        }}
        return null;
    }}''', patterns)
    
    if success:
        log(f"Date Match Found: {success}")
        time.sleep(2)
        page.wait_for_load_state("networkidle")
    else:
        log("CRITICAL: Failed to find date in dropdown!")

def find_available_times(page, players, twilight_start=None):
    tiles = page.query_selector_all(".time-tile, .time, [class*='time-tile']")
    log(f"Found {len(tiles)} potential tiles.")
    
    valid_times = []
    tw_min = 0
    if twilight_start:
        h, m = map(int, twilight_start.split(":"))
        tw_min = h * 60 + m

    time_regex = re.compile(r"(\d{1,2}:\d{2}\s*(?:AM|PM|am|pm))", re.IGNORECASE)

    for tile in tiles:
        full_text = tile.inner_text().strip()
        if not full_text: continue
        
        match = time_regex.search(full_text)
        if not match: continue
            
        time_part = match.group(1).strip()
        
        try:
            clean_time = time_part.upper().replace(" ", "")
            if "PM" in clean_time:
                clean_time = clean_time.replace("PM", " PM")
            else:
                clean_time = clean_time.replace("AM", " AM")
                
            t_obj = datetime.strptime(clean_time, "%I:%M %p")
            t_min = t_obj.hour * 60 + t_obj.minute
            
            if t_min >= tw_min:
                valid_times.append({
                    "element": tile,
                    "display": time_part,
                    "minutes": t_min,
                    "full_text": full_text.replace("\n", " ")
                })
        except:
            continue
            
    unique_times = []
    seen = set()
    for t in valid_times:
        if t["display"] not in seen:
            unique_times.append(t)
            seen.add(t["display"])
            log(f"  Valid Match: {t['display']} | Details: {t['full_text']}")
            
    return sorted(unique_times, key=lambda x: x["minutes"])

def book_time(page, time_data, players, env, dry_run=False):
    log(f"Attempting to book {time_data['display']}...")
    
    # Target the actual time text element inside the tile for the click
    # This avoids clicking informational links like '9 Holes' or 'Booking Rules'
    try:
        click_target = time_data["element"].query_selector("span, a, .time-text") or time_data["element"]
        click_target.click(force=True)
    except:
        time_data["element"].click(force=True)
    
    # Check if a 'Booking Rules' modal popped up by mistake and close it
    time.sleep(1)
    rules_modal = page.query_selector('.modal-title:has-text("Booking Rules"), .modal-header:has-text("Rules")')
    if rules_modal:
        log("Accidental Rules modal detected. Closing and retrying click...")
        page.keyboard.press("Escape")
        time.sleep(1)
        # Try a more central click on the tile
        time_data["element"].click(position={"x": 5, "y": 5})
    
    try:
        page.wait_for_selector('.modal.show, .modal.in', timeout=5000)
        player_btn = page.query_selector(f'.modal .btn:has-text("{players}")')
        if player_btn:
            player_btn.click()
        else:
            page.click('.modal .btn-primary')
    except:
        pass
    
    if dry_run:
        log("DRY RUN: Stopping before final booking.")
        return True

    try:
        log("Waiting for review screen and Book button...")
        time.sleep(3)
        
        # Click the book button. Try multiple ways.
        page.evaluate('''() => {
            const btns = Array.from(document.querySelectorAll('button, a, input[type="button"]'));
            const target = btns.find(b => 
                b.innerText.includes('Book') || 
                b.innerText.includes('Reserve') || 
                b.classList.contains('js-book-button')
            );
            if (target) target.click();
            return !!target;
        }''')
        
        log("Clicked Book button. Waiting for confirmation...")
        for _ in range(30):
            if "/confirmation/" in page.url:
                log("SUCCESS: Confirmation received!")
                return True
            error = page.query_selector('.bootstrap-growl')
            if error and error.is_visible():
                log(f"Booking Error: {error.inner_text()}")
                return False
            time.sleep(1)
    except Exception as e:
        log(f"Failed during final booking step: {e}")
        return False
    
    return False

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--players", type=int, default=2)
    parser.add_argument("--twilight", action="store_true")
    parser.add_argument("--poll", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--headless", action="store_true", default=False)
    args = parser.parse_args()

    env = load_env()
    twilight_start = get_twilight_start(args.date) if args.twilight else None

    with sync_playwright() as p:
        log("Launching Browser...")
        browser = p.chromium.launch(headless=args.headless, executable_path=get_chrome_path())
        context = browser.new_context(
            viewport={'width': 1280, 'height': 2000},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        try:
            navigate_and_login(page, env)
            select_resident_and_date(page, args.date)

            # Ensure we are at the top of the page before scanning for times
            log("Scrolling to top of page...")
            page.evaluate("window.scrollTo(0, 0)")
            time.sleep(1)

            max_attempts = 10 if args.poll else 1
            for attempt in range(max_attempts):
                if attempt > 0:
                    log(f"Polling attempt {attempt+1}...")
                    page.reload(wait_until="networkidle")
                    select_resident_and_date(page, args.date)
                    page.evaluate("window.scrollTo(0, 0)")
                    time.sleep(1)

                times = find_available_times(page, args.players, twilight_start)
                if times:
                    # find_available_times already returns them sorted by minutes (earliest first)
                    log(f"Found {len(times)} valid times. Earliest is {times[0]['display']}")
                    if book_time(page, times[0], args.players, env, args.dry_run):
                        print(json.dumps({
                            "success": True, "course": COURSE_INFO["name"], 
                            "date": args.date, "time": times[0]["display"]
                        }))
                        browser.close()
                        sys.exit(0)
                
                if args.poll:
                    time.sleep(10)

            log("No times found or booking failed. Saving full screenshot...")
            page.screenshot(path="debug_final.png", full_page=True)
            print(json.dumps({"success": False, "error": "no_times_available"}))
        except Exception as e:
            log(f"CRITICAL ERROR: {e}")
            page.screenshot(path="debug_error.png", full_page=True)
            print(json.dumps({"success": False, "error": "exception", "detail": str(e)}))
        
        browser.close()
        sys.exit(1)

if __name__ == "__main__":
    main()
