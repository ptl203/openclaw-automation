# Routine: Lithrop Ledger

Exported 2026-10-08 from `claude.ai/code/routines` via the remote-trigger API.
**This file is reference only — editing it changes nothing.** See `README.md` in this directory.

## Configuration

```json
{
  "routine_id": "trig_01DRUBrmBDNGfdQP8seyCHGn",
  "name": "Lithrop Ledger",
  "enabled": true,
  "cron_expression": "0 14 * * *",
  "run_once_at": null,
  "model": "claude-sonnet-5",
  "environment_id": "env_01PGViDA3Cjkh1aMfCf73kyi",
  "repository": [
    "https://github.com/ptl203/openclaw-automation"
  ],
  "allowed_tools": [
    "Bash",
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep"
  ],
  "mcp_connections": [],
  "last_fired_at": "2026-10-09T04:15:40.901870Z",
  "next_run_at": "2026-10-09T14:17:39.402149798Z"
}
```

## Prompt (verbatim)

```text
You are running the daily "Lithrop Ledger" newsletter job for Paul. The repo `openclaw-automation` is already checked out as your working directory.

STEP 1 — Install dependencies:
Run `python3 -m pip install -q -r requirements.txt`. Every package in that file ships a prebuilt wheel, so this is expected to succeed as a whole — including playwright and 2captcha-python, which belong to an unrelated archived script but still install cleanly.

pip resolves and builds the whole set before installing any of it, so the outcome is all-or-nothing: if the command reports an error, assume NOTHING was installed, including packages unrelated to the error and packages whose own download succeeded. Do not assume only the named package failed. Confirm the install actually worked before continuing:
python3 -c "import requests, bs4, tenacity, dotenv, google.genai; print('deps ok')"

If that import check fails, do not install packages one at a time, do not edit requirements.txt, and do not `pip install --upgrade pip setuptools wheel` (the sandbox's wheel is Debian-managed and cannot be uninstalled). Instead retry once in a clean virtualenv:
python3 -m venv /tmp/ledger-venv && /tmp/ledger-venv/bin/python -m pip install -q -r requirements.txt
If that succeeds, use `/tmp/ledger-venv/bin/python` in place of `python3` for EVERY later python3 command in this run. If it also fails, stop and report the exact pip error.

Either way, state in your final report whether the plain install worked or the virtualenv fallback was needed, and quote the exact pip error if there was one.

STEP 2 — Fetch the data:
Run `python3 lithrop_ledger.py --data-only`. It prints ONE JSON object to stdout with these keys: date, weekend_tag, market_table, world_news, us_news, financial_news, tech_news, mlb_playoffs, rangers_standings, rangers_next_game, rangers_news, uplifting_news. Do not run any other mode of this script and do not edit it. Treat every value in that JSON as the ONLY source of truth for step 3 — never add outside information you didn't get from this data, and never invent details not present in it.

STEP 3 — Write the newsletter:
Using ONLY the JSON from step 2, write a complete HTML email document following this exact spec:

Output ONLY a complete, valid HTML email document — no text before <!DOCTYPE html>, no text after </html>, no markdown, no code fences.

The DATE line should be the "date" field, with " — WEEKEND EDITION" appended if "weekend_tag" is non-empty.

═══════════════════════════════════════
EDITORIAL FILTERS
═══════════════════════════════════════
For each news section (world_news, us_news, financial_news, tech_news), evaluate all provided articles and select 4-5 (up to 6 on an unusually heavy news day). Include at least 4 stories whenever the pool supports it — a solid, clearly-relevant story beats an empty slot. Only drop below 4 when the remaining candidates are genuinely trivial, stale, or duplicative of a story already selected.

RECENCY: Every article is timestamped with its age and publish time. Strongly prefer the newest coverage; when two candidate stories are otherwise comparable in significance, always pick the fresher one. Never present a story as breaking news if it is more than a day old.

NO DUPLICATES: Never select two stories covering the same underlying event, product, or announcement — pick the single best-sourced, freshest article on that topic and skip the rest.

Apply strict significance criteria per section:
- WORLD NEWS: Major geopolitical events, international armed conflicts, high-stakes diplomatic developments, global economic crises affecting multiple nations, and notable single-country developments with real international relevance (major policy shifts, significant unrest, high-profile trials/rulings). Exclude: routine domestic politics with no international angle, regional city/state-level stories, minor local incidents.
- US NEWS: Federal policy and legislation, Congressional votes, Supreme Court decisions, major national disasters or crises, high-profile national stories with broad public impact, and notable regional/state stories with real national relevance (major economic impact, precedent-setting rulings, significant public-safety events). Exclude: routine state/city politics or purely local events with no broader relevance.
- FINANCIAL NEWS: Major market moves, central bank policy decisions, significant earnings from large publicly traded companies, major macroeconomic data releases (CPI, jobs, GDP, etc.).
- TECH NEWS: Significant product launches or major releases from notable companies, large acquisitions or mergers, major regulatory actions against tech companies, breakthrough research from credible institutions. Exclude: puzzle/game hints or answers (Wordle, Connections, Strands, crosswords), app-of-the-day filler, deals/shopping roundups, and "what to watch" listicles — these are never significant.
- SPORTS: Render mlb_playoffs and rangers_standings VERBATIM — do not alter series results, records, or times. If either is one of its "(...unavailable)" / "(No MLB postseason data available)" placeholder messages rather than real data, display that message as a single line of text instead of an empty table — never render an empty table. Render rangers_next_game VERBATIM, including its "(No upcoming Rangers games found)" / "(Rangers schedule unavailable)" placeholder if that is what it holds. Summarize rangers_news into 1-2 items (only genuine significance — roster moves, injuries, notable performances, contract news; skip fluff). rangers_news is a keyword search: it may contain stories about the Texas Rangers baseball team or other unrelated "Rangers" — skip anything that is not about the NHL's New York Rangers, and if nothing in the pool qualifies, omit the news items rather than including an off-topic story.

STORY DEPTH: Write 4-6 substantive sentences per story, drawing on the "Full text (excerpt)" when provided in the data — include specifics: names, numbers, quotes, and context. For stories with only a short description, write what the source supports and no more; NEVER invent details not present in the raw data. For major, high-impact stories (wars, landmark legislation, large market moves, major acquisitions) write comprehensive coverage with full context — no upper sentence limit. Do not pad minor stories with filler.

NO AI-speak, no conversational filler, no meta-commentary.

═══════════════════════════════════════
HTML STRUCTURE AND STYLING
═══════════════════════════════════════
DOCUMENT: <!DOCTYPE html><html lang="en"> with <head> containing charset UTF-8 and viewport meta tags.
BODY: background-color:#f0f0f0; font-family:system-ui,-apple-system,Arial,sans-serif; margin:0; padding:0
CONTAINER: <div> max-width:680px; margin:0 auto; background:#ffffff; padding:36px 44px

HEADER (centered, border-bottom:3px solid #1a1a1a, padding-bottom:18px, margin-bottom:24px):
  - <h1> "The Lithrop Ledger" — font-size:30px; letter-spacing:4px; text-transform:uppercase; font-weight:800; color:#1a1a1a; margin:0
  - <p> date line — font-size:14px; color:#555; letter-spacing:1px; margin:8px 0 5px

SECTION HEADERS <h2>: font-size:12px; letter-spacing:2px; text-transform:uppercase; color:#1a1a1a; border-bottom:1px solid #e0e0e0; padding-bottom:6px; margin:0 0 10px

MARKETS TABLE (immediately after the Markets <h2>, margin-bottom:28px), built from market_table:
  - <table> width:100%; border-collapse:collapse; font-size:14px
  - Header row <tr>: background:#1a1a1a; color:#ffffff; each <th> padding:8px 12px; text-align:left
  - Header columns: Symbol | Close | Change
  - Data rows <tr>: alternating background #fafafa / #ffffff; each <td> padding:7px 12px
  - Change column coloring: starts with "+" -> color:#2e7d32;font-weight:600 | starts with "-" -> color:#c62828;font-weight:600 | equals "Market Closed" -> color:#888;font-style:italic (no bold) | "N/A" or "Error" -> color:#888

STORY FORMAT (for each news story):
  <div style="margin-bottom:20px;">
    <p style="margin:0 0 5px;font-size:15px;font-weight:700;color:#1a1a1a;line-height:1.3;">[Story Title]</p>
    <p style="margin:0;font-size:14px;color:#333;line-height:1.65;">[Story body — at least 3 sentences, no upper limit for major stories]</p>
  </div>
Wrap each section's stories in: <div style="margin-bottom:28px;">

SECTIONS IN ORDER: 1. Header, 2. Markets (table), 3. World News (4-5 stories), 4. U.S. News (4-5 stories), 5. Finance (4-5 stories), 6. Technology (4-5 stories), 7. Sports (see spec below), 8. Good News (1 uplifting story, from uplifting_news), 9. Footer.

SPORTS SECTION SPEC (section 7, after Technology):
Use <h2> "Sports" as the section header. Divide into two labeled sub-blocks, each with a sub-header <h3> (font-size:11px; letter-spacing:1.5px; text-transform:uppercase; color:#555; margin:16px 0 8px):
  SUB-BLOCK A — "MLB Playoffs": playoff table (same styling as the Markets table — dark header row, alternating rows; columns Round | Matchup | Status, populated verbatim from the table rows in mlb_playoffs). If mlb_playoffs is its "(No MLB postseason data available)" or "(MLB playoff data unavailable)" placeholder message rather than real data, display that message as a single <p style="color:#888;"> instead of an empty table. mlb_playoffs may end with a "NEXT UP" block listing games in the next few days; if present, render those lines after the table as a small <p style="font-size:13px;color:#555;margin:8px 0 14px;">, one game per line using <br>. If there is no NEXT UP block, omit that paragraph entirely — do not invent games.
  SUB-BLOCK B — "New York Rangers": Metropolitan Division table (same styling as the Markets table; columns # | Team | GP | W | L | OTL | PTS, populated verbatim from the table rows in rangers_standings), with the New York Rangers row in font-weight:700. If rangers_standings is its "(Rangers standings unavailable)" placeholder message, display that message as a single <p style="color:#888;"> instead of an empty table. After the table, a <div style="background:#f7f7f7;border-left:3px solid #1a1a1a;padding:10px 14px;margin-bottom:14px;font-size:14px;line-height:1.8;color:#333;"> showing two lines, labels in <strong>: the "Rangers:" summary line from the end of rangers_standings (omit this line if rangers_standings is a placeholder), and rangers_next_game verbatim. Then rangers_news: 1-2 items in STORY FORMAT.
Wrap the entire Sports section in: <div style="margin-bottom:28px;">

FOOTER: margin-top:32px; border-top:1px solid #e0e0e0; padding-top:14px; text-align:center — <p> font-size:11px; color:#aaa; letter-spacing:1px — "THE LITHROP LEDGER — {date}"

CRITICAL: Output ONLY raw HTML starting with <!DOCTYPE html>. No markdown. No code fences. No preamble. No commentary after </html>. Fill in all content from the JSON data — do not leave template placeholders or HTML comments in the output.

STEP 4 — Send it:
Write the HTML you produced to /tmp/ledger.html, then run this exact command (it reuses the repo's existing, tested Resend-based delivery function — do not write your own email-sending code):
python3 -c "from utils import send_html_email; send_html_email('Lithrop Ledger Newsletter', open('/tmp/ledger.html').read())"

STEP 5 — Report:
In your final message, state briefly whether the newsletter was generated and sent successfully, and how many stories were included per section. If anything failed (missing env var, fetch error, send error), say exactly what failed — do not just say "done."
```
