from playwright.sync_api import sync_playwright
import time
import os
import json

def run():
    with sync_playwright() as p:
        print("Launching browser to sniff API parameters...")
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        
        # Load credentials from .env
        env = {}
        if os.path.exists(".env"):
            with open(".env") as f:
                for line in f:
                    if "=" in line:
                        k, v = line.strip().split("=", 1)
                        env[k] = v

        # Listen for API calls
        captured_params = []
        def handle_request(request):
            if "api/booking/times" in request.url:
                print(f"Captured API Call: {request.url}")
                captured_params.append(request.url)

        page.on("request", handle_request)

        # Login flow
        page.goto("https://foreupsoftware.com/index.php/booking/19347/1468#/teetimes")
        page.fill('input[placeholder="Username"]', env.get("FOREUP_EMAIL", ""))
        page.fill('input[placeholder="Password"]', env.get("FOREUP_PASSWORD", ""))
        page.click('input[type="submit"]')
        page.wait_for_load_state("networkidle")
        time.sleep(2)

        # Click the Resident button - this is what we need to sniff
        print("Clicking Resident button...")
        resident_btn = page.query_selector('button:has-text("Resident (0 - 7 Days)")')
        if resident_btn:
            resident_btn.click()
            page.wait_for_load_state("networkidle")
            time.sleep(3)

        print("\n--- Discovered API Parameters ---")
        for url in captured_params:
            if "booking_class_id=" in url:
                class_id = url.split("booking_class_id=")[1].split("&")[0]
                schedule_id = url.split("schedule_id=")[1].split("&")[0]
                print(f"Found booking_class_id: {class_id} for schedule_id: {schedule_id}")
        
        browser.close()

if __name__ == "__main__":
    run()
