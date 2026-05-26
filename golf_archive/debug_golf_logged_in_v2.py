from playwright.sync_api import sync_playwright
import time
import os
from dotenv import load_dotenv

load_dotenv()

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1280, 'height': 900})
        page = context.new_page()
        
        email = os.getenv("FOREUP_EMAIL")
        password = os.getenv("FOREUP_PASSWORD")
        
        # Go to Torrey North booking page
        print("Navigating to Torrey North booking page...")
        page.goto("https://foreupsoftware.com/index.php/booking/19347/1468#/teetimes", wait_until="networkidle")
        time.sleep(2)
        
        # Login
        print("Logging in...")
        # Try different selectors for login fields
        try:
            page.wait_for_selector('input[name="email"]', timeout=5000)
            page.fill('input[name="email"]', email)
            page.fill('input[name="password"]', password)
        except:
            try:
                page.wait_for_selector('input[placeholder="Email"]', timeout=5000)
                page.fill('input[placeholder="Email"]', email)
                page.fill('input[placeholder="Password"]', password)
            except:
                print("Could not find email/password fields by name or placeholder. Attempting general inputs...")
                inputs = page.query_selector_all('input[type="text"], input[type="email"]')
                if inputs:
                    inputs[0].fill(email)
                passwords = page.query_selector_all('input[type="password"]')
                if passwords:
                    passwords[0].fill(password)
        
        page.keyboard.press("Enter")
        time.sleep(5)
        
        # Go back to tee times
        print("Navigating back to tee times...")
        page.goto("https://foreupsoftware.com/index.php/booking/19347/1468#/teetimes", wait_until="networkidle")
        time.sleep(2)
        
        # Select "Resident (0 - 7 Days)"
        print("Clicking Resident (0 - 7 Days)...")
        # Try both variants of space/dash
        resident_btn = page.query_selector('button:has-text("Resident (0 - 7 Days)")') or \
                       page.query_selector('button:has-text("Resident (0-7 Days)")')
        if resident_btn:
            resident_btn.click()
            page.wait_for_load_state("networkidle")
            time.sleep(2)
        
        # Try to select the date 04-18-2026
        print("Selecting date 04-18-2026...")
        page.evaluate('''() => {
            const selects = document.querySelectorAll('select');
            for (let sel of selects) {
                for (let opt of sel.options) {
                    if (opt.text.includes("18th")) {
                        sel.value = opt.value;
                        sel.dispatchEvent(new Event('change', {bubbles: true}));
                        return;
                    }
                }
            }
        }''')
        time.sleep(3)
        
        # Check for times
        times = page.query_selector_all('.time.time-tile')
        print(f"Found {len(times)} tee times on page.")
        
        for i, t in enumerate(times[:10]):
            text = t.inner_text().strip().replace('\n', ' ')
            print(f"Time {i+1}: {text}")
            
        page.screenshot(path="debug_golf_logged_in_v2.png")
        browser.close()

if __name__ == "__main__":
    run()
