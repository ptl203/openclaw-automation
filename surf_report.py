import os
import sys
import requests
import argparse
from datetime import datetime
from google import genai
from google.genai import types
from utils import notify, log_event

def get_surf_data():
    api_key = os.getenv("STORMGLASS_API_KEY")
    if not api_key:
        raise ValueError("STORMGLASS_API_KEY not set")
    lat = 32.8662
    lng = -117.2537
    params = "waveHeight,wavePeriod,waveDirection,swellHeight,swellPeriod,swellDirection,secondarySwellHeight,secondarySwellPeriod,windWaveHeight,windSpeed,windDirection,waterTemperature"
    url = f"https://api.stormglass.io/v2/weather/point?lat={lat}&lng={lng}&params={params}"
    headers = {"Authorization": api_key}
    
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    return resp.json()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--am", action="store_true", help="Dawn patrol report")
    parser.add_argument("--pm", action="store_true", help="Afternoon report")
    args = parser.parse_args()

    if not args.am and not args.pm:
         args.am = True # default
         
    log_event(f"Starting Surf Report ({'AM' if args.am else 'PM'})...")
    
    try:
        raw_data = get_surf_data()
    except Exception as e:
        log_event(f"Surf Report data fetch failed: {e}")
        notify("Surf Report Error", f"Failed to get Stormglass data: {e}")
        return

    now = datetime.now()
    
    # Prompt
    context = "5AM dawn patrol report" if args.am else "3PM afternoon report"
    verdict_type = "Dawn patrol verdict" if args.am else "Afternoon verdict"
    prompt = f"""You are executing a live surf conditions check for Scripps Beach, La Jolla, CA. This will be sent via EMAIL. Do all work silently.

GEOGRAPHY NOTE: Scripps Beach faces due west (approximately 270°). The shoreline runs roughly north-south.
- ONSHORE wind: coming FROM the west, roughly 225°–315°
- OFFSHORE wind: coming FROM the east, roughly 45°–135°
- CROSS-SHORE wind: coming FROM the north or south, roughly 315°–45° or 135°–225°

TIME CONTEXT: This is the {context}. Current time: {now.strftime('%Y-%m-%d %H:%M:%S')}.
RAW DATA from Stormglass API (find the closest hour):
{raw_data}

STEP 3 — Silently convert all units:
- Wave/swell heights: meters → feet
- Wind speed: m/s → knots
- Water temperature: °C → °F

STEP 4 — Output ONLY this report, formatted for EMAIL:

{'🌅 SCRIPPS BEACH DAWN PATROL' if args.am else '🏄 SCRIPPS BEACH AFTERNOON CHECK'}
{now.strftime('%A, %B %d, %Y - %I:%M %p')}

WAVES
---------------------------------
Height: [waveHeight in ft]
Period: [wavePeriod] sec
Direction: [waveDirection]°

SWELL
---------------------------------
Primary: [swellHeight in ft] @ [swellPeriod] sec from [swellDirection]°
Secondary: [secondarySwellHeight in ft] @ [secondarySwellPeriod] sec (if present)
Wind swell: [windWaveHeight in ft] (if present)

WIND & WATER
---------------------------------
Wind: [windSpeed in knots] kts from [windDirection]° ([onshore/offshore/cross-shore])
Water temp: [waterTemperature in °F]°F

VERDICT
---------------------------------
[2-3 sentence summary]
"""

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        notify(f"Surf Report: {'Morning' if args.am else 'Afternoon'}", response.text)
        log_event("Surf Report finished successfully.")
    except Exception as e:
        log_event(f"Surf Report Gemini failed: {e}")
        notify("Surf Report Error", f"Gemini generation failed: {e}")

if __name__ == "__main__":
    main()
