import os
import json
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from google import genai
from utils import (
    notify, log_event,
    filter_nearby_hours, fetch_stormglass,
    param_value, closest_hour, score_conditions, GO_THRESHOLD,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BEACHES_FILE = os.path.join(SCRIPT_DIR, "beaches.json")
# Each beach = 1 Stormglass API request per run. Mind the daily quota when adding beaches.
STORMGLASS_PARAMS = (
    "waveHeight,wavePeriod,waveDirection,"
    "swellHeight,swellPeriod,swellDirection,"
    "secondarySwellHeight,secondarySwellPeriod,"
    "windWaveHeight,windSpeed,windDirection,waterTemperature"
)


def fetch_beach_data(beach):
    return fetch_stormglass(beach["lat"], beach["lng"], STORMGLASS_PARAMS)


def star_str(n):
    return "★" * n + "☆" * (5 - n)


def build_beach_block(rank_label, beach_name, sc):
    """Format the full detail block for one beach from pre-computed score dict."""
    swell_lines = [
        f"Primary: {sc['swell_h_ft']} ft @ {sc['swell_period_s']} sec"
        + (f" from {sc['swell_dir']}°" if sc["swell_dir"] is not None else "")
    ]
    if sc["sec_h_ft"] is not None:
        swell_lines.append(f"Secondary: {sc['sec_h_ft']} ft @ {sc['sec_period_s']} sec")
    if sc["ww_h_ft"] is not None:
        swell_lines.append(f"Wind swell: {sc['ww_h_ft']} ft")

    wave_dir_str = f"{sc['wave_dir']}°" if sc["wave_dir"] is not None else "N/A"
    water_str    = f"{sc['water_temp_f']}°F" if sc["water_temp_f"] is not None else "N/A"

    return (
        f"{rank_label} — {beach_name}\n"
        f"Rating: {star_str(sc['stars'])}\n"
        f"Size: {sc['size_label']} ({sc['height_ft']} ft)\n"
        f"\nWAVES\n"
        f"---------------------------------\n"
        f"Height: {sc['height_ft']} ft\n"
        f"Period: {sc['wave_period_s']} sec\n"
        f"Direction: {wave_dir_str}\n"
        f"\nSWELL\n"
        f"---------------------------------\n"
        + "\n".join(swell_lines) +
        f"\nSea state: {sc['org_label']} ({sc['clean_pct']}% clean energy)\n"
        f"\nWIND & WATER\n"
        f"---------------------------------\n"
        f"Wind: {sc['wind_kts']} kts from {sc['wind_dir']}° ({sc['wind_label']})\n"
        f"Water temp: {water_str}"
    )


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

    # Fetch all beaches in parallel
    results = {}
    errors = []
    with ThreadPoolExecutor(max_workers=len(beaches)) as executor:
        futures = {executor.submit(fetch_beach_data, b): b for b in beaches}
        for future in as_completed(futures):
            beach = futures[future]
            try:
                raw = future.result()
                results[beach["name"]] = (beach, filter_nearby_hours(raw))
            except Exception as e:
                errors.append(f"{beach['name']}: {e}")
                log_event(f"Surf Compare fetch failed for {beach['name']}: {e}")

    if not results:
        notify("Surf Compare Error", "All beach data fetches failed:\n" + "\n".join(errors))
        return

    # Score each beach using the deterministic Python scorer
    now = datetime.now()
    scored = []
    for name, (beach, nearby) in results.items():
        hour = closest_hour(nearby)
        if hour is None:
            log_event(f"No data point near current time for {name} — skipping")
            continue
        sc = score_conditions(hour, beach["facing_dir"])
        scored.append({"name": name, "beach": beach, "score": sc})

    if not scored:
        notify("Surf Compare Error", "Could not score any beaches (no data near current time).")
        return

    # Rank best → worst by total score
    ranked = sorted(scored, key=lambda x: x["score"]["total"], reverse=True)

    go         = ranked[0]["score"]["total"] >= GO_THRESHOLD
    go_str     = "✅ GO SURF" if go else "❌ NO GO"
    context    = "5AM dawn patrol" if args.am else "3PM afternoon"
    header_emoji  = "🌅" if args.am else "🏄"
    session_label = "DAWN PATROL" if args.am else "AFTERNOON CHECK"

    # Build the factual blocks — Python owns all numbers and ratings
    rank_labels = ["🏆 #1", "#2", "#3"]
    blocks = [
        build_beach_block(rank_labels[i], r["name"], r["score"])
        for i, r in enumerate(ranked)
    ]

    # Ranked summary passed to Gemini for context
    ranked_summary = "\n".join(
        f"  {rank_labels[i]} {r['name']}: {r['score']['stars']}★ "
        f"(score {r['score']['total']}/100) — "
        f"{r['score']['height_ft']} ft {r['score']['size_label']}, "
        f"{r['score']['wind_kts']} kts {r['score']['wind_label']}, "
        f"primary swell {r['score']['primary_period_s']} sec, "
        f"sea state {r['score']['org_label']} ({r['score']['clean_pct']}% clean energy)"
        for i, r in enumerate(ranked)
    )

    if go:
        verdict_instruction = (
            f"Explain in 2–3 sentences why {ranked[0]['name']} is the top pick today. "
            f"Specifically name its strongest factors (wave size / period / wind quality) "
            f"and briefly contrast what holds the other beaches back."
        )
    else:
        verdict_instruction = (
            "Explain in 2–3 sentences why none of the beaches are worth surfing today. "
            "Name the main problem (e.g. flat, blown-out onshore winds, short-period wind swell) "
            "and advise staying home."
        )

    verdict_prompt = f"""You are writing one paragraph of a surf report email. Output ONLY the verdict text — no labels, no headers, no formatting, no markdown.

Session: {context}
Current time: {now.strftime('%Y-%m-%d %H:%M:%S')}
GO/NO-GO: {"GO" if go else "NO GO"}

Ranked beaches (Python-computed scores — do not alter ratings or rankings):
{ranked_summary}

{verdict_instruction}"""

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=verdict_prompt,
        )
        verdict_text = response.text.strip()
        log_event("Surf Compare Gemini verdict generated.")
    except Exception as e:
        log_event(f"Surf Compare Gemini verdict failed: {e}")
        verdict_text = f"[Verdict unavailable: {e}]"

    # Assemble the final email — verdict on top, ranked beach blocks below
    sep = "\n═════════════════════════════════\n"
    email_body = (
        f"{header_emoji} SAN DIEGO SURF COMPARE — {session_label}\n"
        f"{now.strftime('%A, %B %d, %Y - %I:%M %p')}\n\n"
        f"{go_str}\n\n"
        f"VERDICT\n"
        f"---------------------------------\n"
        f"{verdict_text}\n\n"
        + sep.join(blocks)
    )

    notify(f"Surf Compare: {'Morning' if args.am else 'Afternoon'}", email_body)
    log_event("Surf Compare finished successfully.")


if __name__ == "__main__":
    main()
