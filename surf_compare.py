import os
import json
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from google import genai
from utils import notify, log_event, compass_label, wind_ranges, filter_nearby_hours, fetch_stormglass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BEACHES_FILE = os.path.join(SCRIPT_DIR, "beaches.json")
# Each beach = 1 Stormglass API request per run. Mind the daily quota when adding beaches.
STORMGLASS_PARAMS = "waveHeight,wavePeriod,waveDirection,swellHeight,swellPeriod,swellDirection,secondarySwellHeight,secondarySwellPeriod,windWaveHeight,windSpeed,windDirection,waterTemperature"


def fetch_beach_data(beach):
    return fetch_stormglass(beach["lat"], beach["lng"], STORMGLASS_PARAMS)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--am", action="store_true", help="Dawn patrol report")
    parser.add_argument("--pm", action="store_true", help="Afternoon report")
    args = parser.parse_args()

    if not args.am and not args.pm:
        args.am = True

    log_event(f"Starting Surf Compare ({'AM' if args.am else 'PM'})...")

    with open(BEACHES_FILE) as f:
        beaches = json.load(f)

    results = {}
    errors = []
    with ThreadPoolExecutor(max_workers=len(beaches)) as executor:
        futures = {executor.submit(fetch_beach_data, b): b for b in beaches}
        for future in as_completed(futures):
            beach = futures[future]
            try:
                raw_data = future.result()
                results[beach["name"]] = (beach, filter_nearby_hours(raw_data))
            except Exception as e:
                errors.append(f"{beach['name']}: {e}")
                log_event(f"Surf Compare fetch failed for {beach['name']}: {e}")

    if not results:
        notify("Surf Compare Error", "All beach data fetches failed:\n" + "\n".join(errors))
        return

    now = datetime.now()
    context = "5AM dawn patrol" if args.am else "3PM afternoon"
    header_emoji = "🌅" if args.am else "🏄"
    session_label = "DAWN PATROL" if args.am else "AFTERNOON CHECK"

    beach_blocks = []
    for name, (beach, nearby_data) in results.items():
        on_start, on_end, off_start, off_end = wind_ranges(beach["facing_dir"])
        facing_label = compass_label(beach["facing_dir"])
        notes = beach.get("notes", "")
        block = f"""--- {name} ---
Faces: {facing_label} ({beach["facing_dir"]}°)
Onshore wind: FROM {on_start}°–{on_end}° (bad — choppy/blown out)
Offshore wind: FROM {off_start}°–{off_end}° (good — clean/groomed)
Notes: {notes}
Data:
{json.dumps(nearby_data, indent=2)}"""
        beach_blocks.append(block)

    beaches_text = "\n\n".join(beach_blocks)

    prompt = f"""You are executing a live multi-beach surf comparison for San Diego. This will be sent via EMAIL. Do all work silently.

SURFER PROFILE: Intermediate. Comfortable up to ~4–5 ft, prefers clean waves.

WAVE SIZE GUIDE (use these exact labels):
- Under 2 ft: small
- 2–4 ft: decent
- 4+ ft: big surf

SURF QUALITY RATING GUIDE (tuned for 2–4 ft as the ideal size):
- ★★★★★  Decent (2–4 ft) + offshore wind — perfect conditions
- ★★★★☆  Decent + light cross-shore, OR big surf (4+ ft) + offshore wind
- ★★★☆☆  Small surf + offshore wind, OR decent + moderate onshore, OR big surf + cross-shore
- ★★☆☆☆  Small + onshore, OR big surf + strong onshore (too big/messy for intermediate)
- ★☆☆☆☆  Essentially flat, OR blown out regardless of size

GO / NO GO RULE: If the best beach rates ★★★☆☆ or higher → GO. Otherwise → NO GO.

TIME CONTEXT: {context}. Current time: {now.strftime('%Y-%m-%d %H:%M:%S')}.

BEACH DATA (each beach's facing direction and nearest 3 hours of Stormglass data):

{beaches_text}

STEP 1 — For each beach, find the data point closest to the current time.

STEP 2 — Silently convert all units for each beach:
- Wave/swell heights: meters → feet
- Wind speed: m/s → knots

STEP 3 — For each beach, determine wind quality (offshore/onshore/cross-shore), wave size label, and star rating.

STEP 4 — Rank the beaches best to worst. Apply the GO/NO GO rule.

STEP 5 — Output ONLY this report, formatted for EMAIL. Follow the format exactly:

{header_emoji} SAN DIEGO SURF COMPARE — {session_label}
{now.strftime('%A, %B %d, %Y - %I:%M %p')}

[✅ GO SURF or ❌ NO GO]
[One sentence: what makes today worth it, or why to stay home]

🏆 HEAD TO: [Beach Name]
Rating: [★ out of ★★★★★]
Size: [small/decent/big surf] ([X.X ft])
Wind: [X.X kts from X°] ([offshore/onshore/cross-shore] — [clean/choppy/variable])
[2–3 sentences on what to expect and why this is the top pick today]

---------------------------------
[Beach Name 2]  [★ rating]
Size: [label] ([X.X ft]) | Wind: [offshore/onshore/cross-shore]
[One sentence verdict]

[Beach Name 3]  [★ rating]
Size: [label] ([X.X ft]) | Wind: [offshore/onshore/cross-shore]
[One sentence verdict]
"""

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        notify(f"Surf Compare: {'Morning' if args.am else 'Afternoon'}", response.text)
        log_event("Surf Compare finished successfully.")
    except Exception as e:
        log_event(f"Surf Compare Gemini failed: {e}")
        notify("Surf Compare Error", f"Gemini generation failed: {e}")


if __name__ == "__main__":
    main()
