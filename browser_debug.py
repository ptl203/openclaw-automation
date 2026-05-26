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
        
        print("Navigating to booking page...")
        page.goto("https://foreupsoftware.com/index.php/booking/19347/1468#/teetimes", wait_until="networkidle")
        time.sleep(2)
        
        # Login if needed
        if page.query_selector('input[placeholder="Username"]') or page.query_selector('input[placeholder="Email"]'):
            print("Logging in...")
            user_field = page.query_selector('input[placeholder="Username"]') or page.query_selector('input[placeholder="Email"]')
            user_field.fill(email)
            page.fill('input[placeholder="Password"]', password)
            page.click('button:has-text("SIGN IN"), button:has-text("Sign In"), input[type="submit"]')
            page.wait_for_load_state("networkidle")
            time.sleep(2)
            page.goto("https://foreupsoftware.com/index.php/booking/19347/1468#/teetimes", wait_until="networkidle")
            time.sleep(2)

        # Select Resident (0-7 Days)
        print("Selecting Resident (0 - 7 Days)...")
        res_btn = page.query_selector('button:has-text("Resident (0 - 7 Days)")')
        if res_btn:
            res_btn.click()
            page.wait_for_load_state("networkidle")
            time.sleep(2)

        # Select Date 18th
        print("Selecting date 18th...")
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
        time.sleep(5) # Wait longer for times to load
        
        # Check for times
        times = page.query_selector_all('.time.time-tile')
        print(f"Found {len(times)} tee times.")
        
        for i, t in enumerate(times):
            print(f"Time {i+1}: {t.inner_text().strip().replace('\\n', ' ')}")
            
        if len(times) == 0:
            print("PAGE CONTENT PREVIEW:")
            print(page.inner_text('body')[:500])
            
        page.screenshot(path="final_debug.png")
        browser.close()

if __name__ == "__main__":
    run()
