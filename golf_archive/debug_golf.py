from playwright.sync_api import sync_playwright
import time
import os

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1280, 'height': 900})
        page = context.new_page()
        
        # Go to Torrey North booking page
        print("Navigating to Torrey North booking page...")
        page.goto("https://foreupsoftware.com/index.php/booking/19347/1468#/teetimes", wait_until="networkidle")
        time.sleep(2)
        
        # Select "Resident (0-7 Days)" if needed
        resident_btn = page.query_selector('button:has-text("Resident (0 - 7 Days)")')
        if resident_btn:
            print("Clicking Resident (0 - 7 Days)...")
            resident_btn.click()
            page.wait_for_load_state("networkidle")
            time.sleep(2)
        
        # Try to select the date 04-18-2026
        # Date selector is usually a dropdown or a datepicker
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
        
        # Log some text from the page to see what's going on
        body_text = page.inner_text('body')
        if "no tee times" in body_text.lower() or "no times" in body_text.lower():
             print("Page says no times available.")
        
        for i, t in enumerate(times[:5]):
            print(f"Time {i+1}: {t.inner_text().strip()}")
            
        page.screenshot(path="debug_golf.png")
        browser.close()

if __name__ == "__main__":
    run()
