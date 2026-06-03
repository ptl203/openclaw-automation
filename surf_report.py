import os
import json
import argparse
from datetime import datetime, timezone
from google import genai
from utils import notify, log_event, compass_label, wind_ranges, filter_nearby_hours, fetch_stormglass

LOCATION_NAME = os.getenv("SURF_LOCATION_NAME", "Scripps Beach, La Jolla, CA")
LAT = float(os.getenv("SURF_LAT", "32.8662"))
LNG = float(os.getenv("SURF_LNG", "-117.2537"))
FACING_DIR = int(os.getenv("SURF_FACING_DIR", "270"))


def get_surf_data():
    params = "waveHeight,wavePeriod,waveDirection,swellHeight,swellPeriod,swellDirection,secondarySwellHeight,secondarySwellPeriod,windWaveHeight,windSpeed,windDirection,waterTemperature"
    return fetch_stormglass(LAT, LNG, params)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--am", action="store_true", help="Dawn patrol report")
    parser.add_argument("--pm", action="store_true", help="Afternoon report")
    args = parser.parse_args()

    if not args.am and not args.pm:
        args.am = True

    log_event(f"Starting Surf Report ({'AM' if args.am else 'PM'})...")

    try:
        raw_data = get_surf_data()
    except Exception as e:
        log_event(f"Surf Report data fetch failed: {e}")
        notify("Surf Report Error", f"Failed to get Stormglass data: {e}")
        return

    now = datetime.now()
    nearby_data = filter_nearby_hours(raw_data)

    context = "5AM dawn patrol report" if args.am else "3PM afternoon report"
    facing_label = compass_label(FACING_DIR)
    on_start, on_end, off_start, off_end = wind_ranges(FACING_DIR)
    header_emoji = "🌅" if args.am else "🏄"
    header_label = "DAWN PATROL" if args.am else "AFTERNOON CHECK"

    prompt = f"""You are executing a live surf conditions check for {LOCATION_NAME}. This will be sent via EMAIL. Do all work silently.

GEOGRAPHY NOTE: {LOCATION_NAME} faces {facing_label} (approximately {FACING_DIR}°).
- ONSHORE wind: coming FROM roughly {on_start}°–{on_end}° — choppy, blown-out conditions (bad for surfing)
- OFFSHORE wind: coming FROM roughly {off_start}°–{off_end}° — clean, groomed waves (good for surfing)
- CROSS-SHORE wind: anything else — often acceptable depending on strength

WAVE SIZE GUIDE (use these exact labels in the report):
- Under 2 ft: small
- 2–4 ft: decent
- 4+ ft: big surf

SURF QUALITY RATING GUIDE (tuned for preference for 2–4 ft waves as ideal size):
- ★★★★★  Decent (2–4 ft) + offshore wind — perfect conditions
- ★★★★☆  Decent + light cross-shore, OR big surf (4+ ft) + offshore wind
- ★★★☆☆  Small surf + offshore wind, OR decent + moderate onshore, OR big surf + cross-shore
- ★★☆☆☆  Small + onshore, OR big surf + strong onshore (too big/messy)
- ★☆☆☆☆  Essentially flat, OR blown out regardless of size

TIME CONTEXT: This is the {context}. Current time: {now.strftime('%Y-%m-%d %H:%M:%S')}.
RAW DATA from Stormglass API (nearest 3 hours only):
{json.dumps(nearby_data, indent=2)}

STEP 1 — Find the data point closest to the current time.

STEP 2 — Silently convert all units:
- Wave/swell heights: meters → feet
- Wind speed: m/s → knots
- Water temperature: °C → °F

STEP 3 — Determine wind quality (offshore/onshore/cross-shore), wave size label, and star rating.

STEP 4 — Output ONLY this report, formatted for EMAIL:

{header_emoji} {LOCATION_NAME.upper()} {header_label}
{now.strftime('%A, %B %d, %Y - %I:%M %p')}

Rating: [★ out of ★★★★★]
Size: [small/decent/big surf] ([X.X ft])

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
[2-3 sentence summary using the size label and quality assessment]
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
