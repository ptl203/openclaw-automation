import os
import sys
import json
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from google import genai
from utils import (
    notify, log_event, wait_for_network,
    filter_nearby_hours, fetch_stormglass, fetch_tide_extremes,
    score_conditions, tide_state, GO_THRESHOLD,
)

_PT = ZoneInfo("America/Los_Angeles")

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


def _fmt_hour_pt(iso_time):
    """Render a UTC ISO time as a short Pacific hour label like '7 AM'."""
    try:
        t = datetime.fromisoformat(iso_time)
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return t.astimezone(_PT).strftime("%-I %p")
    except Exception:
        return "now"


def best_session_hour(session_hours, beach, tide_extremes):
    """Score every hour in the session window and return the best (score, hour_iso)."""
    best = None
    for h in session_hours:
        sc = score_conditions(
            h, beach["facing_dir"],
            swell_exposure=beach.get("swell_exposure"),
            tide_extremes=tide_extremes,
            tide_pref=beach.get("tide_pref"),
        )
        if best is None or sc["total"] > best["total"]:
            best = sc
    return best


def tide_line(tide_extremes):
    """One-line current-tide summary for the email header, or None."""
    if not tide_extremes:
        return None
    state = tide_state(tide_extremes, datetime.now(timezone.utc))
    if not state:
        return None
    next_time_pt = state["next_time"].astimezone(_PT).strftime("%-I:%M %p")
    return (
        f"Tide: {state['height_ft']} ft {state['direction']} — "
        f"next {state['next_type']} {next_time_pt} ({state['next_height_ft']} ft)"
    )


def outlook_line(raw, beach, tide_extremes):
    """Best-scoring hour over the next 24h, for a one-line outlook."""
    try:
        day = filter_nearby_hours(raw, back_hours=0, forward_hours=24)
        best = best_session_hour(day["hours"], beach, tide_extremes)
        if not best:
            return None
        when = _fmt_hour_pt(best["hour_time"])
        return (
            f"Next 24h best at {beach['name'].split(',')[0]}: ~{when} — "
            f"{best['height_ft']} ft @ {best['primary_period_s']} s, "
            f"{best['wind_label']} (score {best['total']})"
        )
    except Exception as e:
        log_event(f"Outlook computation failed: {e}")
        return None


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

    tide = sc.get("tide")
    if tide:
        tide_str = (
            f"\nTide: {tide['height_ft']} ft {tide['direction']} "
            f"(fit {sc['sub_scores']['tide']:.2f} for this spot)"
        )
    else:
        tide_str = ""

    return (
        f"{rank_label} — {beach_name}\n"
        f"Rating: {star_str(sc['stars'])} (score {sc['total']}/100)\n"
        f"Size: {sc['size_label']} ({sc['height_ft']} ft)\n"
        f"Best window: ~{_fmt_hour_pt(sc.get('hour_time'))}\n"
        f"\nWAVES\n"
        f"---------------------------------\n"
        f"Height: {sc['height_ft']} ft\n"
        f"Period: {sc['wave_period_s']} sec\n"
        f"Direction: {wave_dir_str}\n"
        f"\nSWELL\n"
        f"---------------------------------\n"
        + "\n".join(swell_lines) +
        f"\nSea state: {sc['org_label']} ({sc['clean_pct']}% clean energy)\n"
        f"Exposure fit: {sc['sub_scores']['swell_fit']:.2f} for this spot\n"
        f"\nWIND & WATER\n"
        f"---------------------------------\n"
        f"Wind: {sc['wind_kts']} kts from {sc['wind_dir']}° ({sc['wind_label']})\n"
        f"Water temp: {water_str}"
        + tide_str
    )


def fetch_all(beaches):
    """Network fetch only: tide extremes + per-beach Stormglass data. No scoring, no AI."""
    # One tide call covers the region — tide is effectively uniform across SD.
    tide_extremes = None
    try:
        tide_extremes = fetch_tide_extremes(beaches[0]["lat"], beaches[0]["lng"])
    except Exception as e:
        log_event(f"Tide fetch failed — scoring tide as neutral: {e}")

    # Fetch all beaches in parallel, keeping the raw multi-day response for the outlook
    results = {}
    errors = []
    with ThreadPoolExecutor(max_workers=len(beaches)) as executor:
        futures = {executor.submit(fetch_beach_data, b): b for b in beaches}
        for future in as_completed(futures):
            beach = futures[future]
            try:
                raw = future.result()
                results[beach["name"]] = (beach, raw)
            except Exception as e:
                errors.append(f"{beach['name']}: {e}")
                log_event(f"Surf Compare fetch failed for {beach['name']}: {e}")
    return results, errors, tide_extremes


def score_and_summarize(results, tide_extremes, args, now=None):
    """Pure scoring/ranking/formatting — no network, no AI. Python owns all numbers
    and ratings; this is reused unchanged by --data-only and --render so the two
    invocations always agree, without re-fetching from Stormglass."""
    now = now or datetime.now()

    # Score every hour in the session window; each beach is ranked by its best hour
    scored = []
    for name, (beach, raw) in results.items():
        session = filter_nearby_hours(raw)
        sc = best_session_hour(session["hours"], beach, tide_extremes)
        if sc is None:
            log_event(f"No data point in session window for {name} — skipping")
            continue
        scored.append({"name": name, "beach": beach, "score": sc})

    if not scored:
        return None

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

    # Ranked summary passed to the verdict writer for context, including sub-scores
    # so it can name the real differentiators instead of inventing them.
    ranked_summary = "\n".join(
        f"  {rank_labels[i]} {r['name']}: {r['score']['stars']}★ "
        f"(score {r['score']['total']}/100) — "
        f"{r['score']['height_ft']} ft {r['score']['size_label']}, "
        f"{r['score']['wind_kts']} kts {r['score']['wind_label']}, "
        f"primary swell {r['score']['primary_period_s']} sec, "
        f"sea state {r['score']['org_label']} ({r['score']['clean_pct']}% clean energy), "
        f"best window ~{_fmt_hour_pt(r['score'].get('hour_time'))}, "
        f"sub-scores {r['score']['sub_scores']}"
        for i, r in enumerate(ranked)
    )

    top_gap = (
        ranked[0]["score"]["total"] - ranked[1]["score"]["total"]
        if len(ranked) > 1 else 99
    )

    if go:
        verdict_instruction = (
            f"Explain in 2–3 sentences why {ranked[0]['name']} is the top pick today. "
            f"Only cite factors where its sub-scores actually differ from the other beaches "
            f"(exposure fit, tide fit, wind, size, period). "
            f"IMPORTANT: the top two scores differ by {top_gap} points. If that gap is 3 or "
            f"less, say the beaches are effectively tied today and to pick by convenience — "
            f"do NOT invent a differentiator."
        )
    else:
        verdict_instruction = (
            "Explain in 2–3 sentences why none of the beaches are worth surfing today. "
            "Name the main problem (e.g. flat, blown-out onshore winds, short-period wind swell, "
            "wrong tide) and advise staying home. If conditions are decent but short of a "
            "notably good day, say so plainly — 'surfable but not special' beats overselling."
        )

    # Region-wide extras: current tide and next-24h outlook (computed from the
    # top-ranked beach's raw data — no extra API calls)
    tide_str_line = tide_line(tide_extremes)
    top_raw = results[ranked[0]["name"]][1]
    outlook = outlook_line(top_raw, ranked[0]["beach"], tide_extremes)
    extras = "\n".join(s for s in (tide_str_line, outlook) if s)
    extras_block = f"{extras}\n\n" if extras else ""

    return {
        "now": now,
        "go": go,
        "go_str": go_str,
        "context": context,
        "header_emoji": header_emoji,
        "session_label": session_label,
        "blocks": blocks,
        "ranked_summary": ranked_summary,
        "verdict_instruction": verdict_instruction,
        "extras_block": extras_block,
    }


def assemble_email(summary, verdict_text, args):
    """Final assembly — verdict on top, ranked beach blocks below. Identical shape
    whether the verdict came from Gemini (legacy) or a routine (current)."""
    sep = "\n═════════════════════════════════\n"
    email_body = (
        f"{summary['header_emoji']} SAN DIEGO SURF COMPARE — {summary['session_label']}\n"
        f"{summary['now'].strftime('%A, %B %d, %Y - %I:%M %p')}\n\n"
        f"{summary['go_str']}\n\n"
        f"{summary['extras_block']}"
        f"VERDICT\n"
        f"---------------------------------\n"
        f"{verdict_text}\n\n"
        + sep.join(summary["blocks"])
    )
    subject = f"Surf Compare: {'Morning' if args.am else 'Afternoon'}"
    return subject, email_body


def _cache_path(args):
    """Where --data-only stashes its raw fetch so --render can reuse it without
    hitting Stormglass again (the free tier is a 10/day quota)."""
    tag = "am" if args.am else "pm"
    return os.path.join(SCRIPT_DIR, f".surf_cache_{tag}.json")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--am", action="store_true", help="Dawn patrol report")
    parser.add_argument("--pm", action="store_true", help="Afternoon report")
    parser.add_argument(
        "--data-only", action="store_true",
        help="Fetch + score conditions, print verdict-writing context as JSON, and "
             "cache the raw fetch to disk. Used by the Claude Code routine that "
             "replaced the Gemini verdict step.",
    )
    parser.add_argument(
        "--render", action="store_true",
        help="Read a verdict paragraph from stdin, reuse the cached --data-only fetch "
             "(no new Stormglass calls), and print the final {subject, body} as JSON "
             "instead of sending email — the routine sends it via Gmail MCP.",
    )
    args = parser.parse_args()
    if not args.am and not args.pm:
        args.am = True

    log_event(f"Starting Surf Compare ({'AM' if args.am else 'PM'})...")
    wait_for_network()

    with open(BEACHES_FILE) as f:
        beaches = json.load(f)

    cache_path = _cache_path(args)

    if args.render:
        with open(cache_path) as f:
            cache = json.load(f)
        results = {
            name: (entry["beach"], entry["raw"])
            for name, entry in cache["results"].items()
        }
        tide_extremes = cache["tide_extremes"]
        now = datetime.fromisoformat(cache["now"])
        summary = score_and_summarize(results, tide_extremes, args, now=now)
        if summary is None:
            print(json.dumps({"error": "Could not score any beaches from cached data."}))
            return
        verdict_text = sys.stdin.read().strip()
        subject, body = assemble_email(summary, verdict_text, args)
        print(json.dumps({"subject": subject, "body": body}))
        log_event("Surf Compare --render finished.")
        return

    results, errors, tide_extremes = fetch_all(beaches)

    if not results:
        if any("402" in e for e in errors):
            notify(
                "Surf Compare: Stormglass quota exhausted",
                "Stormglass returned 402 Payment Required — the daily free-tier "
                "request quota (10/day) is used up, so there is no surf report "
                "for this session. The quota resets at 00:00 UTC (~5 PM PT).",
            )
        else:
            notify("Surf Compare Error", "All beach data fetches failed:\n" + "\n".join(errors))
        return

    now = datetime.now()

    if args.data_only:
        with open(cache_path, "w") as f:
            json.dump({
                "results": {
                    name: {"beach": beach, "raw": raw}
                    for name, (beach, raw) in results.items()
                },
                "tide_extremes": tide_extremes,
                "now": now.isoformat(),
            }, f)
        summary = score_and_summarize(results, tide_extremes, args, now=now)
        if summary is None:
            print(json.dumps({"error": "Could not score any beaches (no data near current time)."}))
            return
        print(json.dumps({
            "go": summary["go"],
            "context": summary["context"],
            "ranked_summary": summary["ranked_summary"],
            "verdict_instruction": summary["verdict_instruction"],
        }))
        log_event("Surf Compare --data-only run finished.")
        return

    # Legacy path (kept until Gemini cleanup): score, get verdict from Gemini, send email
    summary = score_and_summarize(results, tide_extremes, args, now=now)
    if summary is None:
        notify("Surf Compare Error", "Could not score any beaches (no data near current time).")
        return

    verdict_prompt = f"""You are writing one paragraph of a surf report email. Output ONLY the verdict text — no labels, no headers, no formatting, no markdown.

Session: {summary['context']}
Current time: {now.strftime('%Y-%m-%d %H:%M:%S')}
GO/NO-GO: {"GO" if summary['go'] else "NO GO"}

Ranked beaches (Python-computed scores — do not alter ratings or rankings):
{summary['ranked_summary']}

{summary['verdict_instruction']}"""

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

    subject, email_body = assemble_email(summary, verdict_text, args)
    notify(subject, email_body)
    log_event("Surf Compare finished successfully.")


if __name__ == "__main__":
    main()
