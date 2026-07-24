import os
import re
import json
import argparse
import requests
import feedparser
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from zoneinfo import ZoneInfo
from google import genai
from google.genai import types
from utils import notify, log_event, send_html_email, wait_for_network
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=4, max=60),
    before_sleep=lambda retry_state: log_event(f"Retrying Gemini generation (attempt {retry_state.attempt_number})...")
)
def generate_content_with_retry(client, model, contents, config=None):
    if config:
        return client.models.generate_content(model=model, contents=contents, config=config)
    else:
        return client.models.generate_content(model=model, contents=contents)

import time

_PT = ZoneInfo("America/Los_Angeles")

def _fmt_pt(utc_str):
    """Parse an ISO-8601 UTC datetime string and return a Pacific-time display string."""
    try:
        dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        dt_pt = dt.astimezone(_PT)
        return dt_pt.strftime("%-m/%-d %-I:%M %p PT")
    except Exception:
        return utc_str


_BROWSER_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


def _parse_pubdate(article):
    """Return the article's pubDate as an aware UTC datetime, or None. NewsData sends naive UTC ('YYYY-MM-DD HH:MM:SS')."""
    raw = (article.get("pubDate") or "").strip()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _article_age_hours(article):
    """Age in hours; articles with a missing/unparseable pubDate count as infinitely old so they get dropped, not trusted."""
    dt = _parse_pubdate(article)
    if dt is None:
        return float("inf")
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600


_JUNK_PATTERNS = re.compile(
    r"wordle|connections\s+(hints?|answers?)|strands\s+(hints?|answers?)|crossword"
    r"|mini\s+answers|nyt\b.*(hints?|answers?|clues?)|\bpips\b|horoscope"
    r"|daily\s+deals|coupon|promo\s+codes?|best\s+deals|what\s+to\s+watch"
    r"|tv\s+listings|streaming\s+this\s+week",
    re.IGNORECASE,
)


def _is_junk(article):
    """Recurring filler (puzzle hints, deals roundups, horoscopes...) matched on title; description only if title is empty."""
    title = (article.get("title") or "").strip()
    if title:
        return bool(_JUNK_PATTERNS.search(title))
    return bool(_JUNK_PATTERNS.search(article.get("description") or ""))


def _normalize_title(title):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", (title or "").lower())).strip()


def _dedupe(articles):
    """Drop articles whose title is a near-duplicate of one already kept (call with newest first so the freshest survives)."""
    kept = []
    kept_sigs = []
    for article in articles:
        norm = _normalize_title(article.get("title", ""))
        tokens = {t for t in norm.split() if len(t) > 3}
        is_dup = False
        for kept_norm, kept_tokens in kept_sigs:
            if SequenceMatcher(None, norm, kept_norm).ratio() >= 0.6:
                is_dup = True
                break
            if tokens and kept_tokens and len(tokens & kept_tokens) / len(tokens) >= 0.6:
                is_dup = True
                break
        if is_dup:
            log_event(f"Dropped near-duplicate article: {article.get('title', '')!r}")
            continue
        kept.append(article)
        kept_sigs.append((norm, tokens))
    return kept


def _filter_articles(articles, max_age_hours):
    """Freshness filter -> junk blocklist -> near-dup removal, newest first."""
    fresh = [a for a in articles if _article_age_hours(a) <= max_age_hours]
    clean = []
    for a in fresh:
        if _is_junk(a):
            log_event(f"Dropped junk article: {a.get('title', '')!r}")
        else:
            clean.append(a)
    clean.sort(key=_article_age_hours)
    return _dedupe(clean)


def fetch_article_text(url, max_chars=2500):
    """Fetch an article page and return its main body text, or None on any failure (paywall, timeout, non-HTML...)."""
    try:
        resp = requests.get(url, timeout=10, headers=_BROWSER_HEADERS)
        if resp.status_code != 200 or "html" not in resp.headers.get("Content-Type", ""):
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
            tag.decompose()
        # Paragraphs under ~60 chars are almost always boilerplate (bylines, captions, cookie notices)
        paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
        text = " ".join(p for p in paragraphs if len(p) > 60)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < 200:
            return None
        return text[:max_chars]
    except Exception:
        return None


def _enrich_with_fulltext(articles, cap=8):
    """Fetch full text for the first `cap` articles in parallel, storing it on each article as '_fulltext'.
    Articles beyond the cap stay description-only; the whole list is returned."""
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(fetch_article_text, a["link"]): a for a in articles[:cap] if a.get("link")}
        for future, article in futures.items():
            try:
                article["_fulltext"] = future.result()
            except Exception:
                article["_fulltext"] = None
    return articles


def _format_articles(articles):
    """Render articles for the Gemini prompt, each with its age, Pacific publish time, and full text when available."""
    blocks = []
    for i, article in enumerate(articles):
        age = _article_age_hours(article)
        age_str = f"{age:.0f}h ago" if age < 48 else f"{age / 24:.0f}d ago"
        pub_dt = _parse_pubdate(article)
        pub_str = pub_dt.astimezone(_PT).strftime("%-m/%-d %-I:%M %p PT") if pub_dt else "time unknown"
        title = article.get("title", "No Title")
        desc = article.get("description") or "No detailed description available."
        block = f"Article {i + 1} (published {age_str} — {pub_str}):\nTitle: {title}\nDescription: {desc}\n"
        fulltext = article.get("_fulltext")
        if fulltext:
            block += f"Full text (excerpt): {fulltext}\n"
        blocks.append(block)
    return "\n".join(blocks)


# ── NYSE calendar ──────────────────────────────────────────────────────────
# Whether the market was open on a date is decided from the calendar, never
# inferred from whether Yahoo has published a daily bar — their chart API
# often lags the prior session's settled bar in the early morning, which is
# exactly when this script runs.

def _easter(year):
    """Gregorian Easter Sunday (anonymous/Meeus algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return datetime(year, month, day).date()


def _observed(d):
    """Shift a fixed-date holiday to its observed weekday (Sat→Fri, Sun→Mon)."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def _nth_weekday(year, month, weekday, n):
    first = datetime(year, month, 1).date()
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _nyse_holidays(year):
    memorial_day = datetime(year, 5, 31).date()
    memorial_day -= timedelta(days=memorial_day.weekday())  # back to last Monday of May
    return {
        _observed(datetime(year, 1, 1).date()),        # New Year's Day
        _nth_weekday(year, 1, 0, 3),                   # MLK Day
        _nth_weekday(year, 2, 0, 3),                   # Washington's Birthday
        _easter(year) - timedelta(days=2),             # Good Friday
        memorial_day,
        _observed(datetime(year, 6, 19).date()),       # Juneteenth
        _observed(datetime(year, 7, 4).date()),        # Independence Day
        _nth_weekday(year, 9, 0, 1),                   # Labor Day
        _nth_weekday(year, 11, 3, 4),                  # Thanksgiving
        _observed(datetime(year, 12, 25).date()),      # Christmas
    }


def market_was_open(d):
    return d.weekday() < 5 and d not in _nyse_holidays(d.year)


def fetch_fred_series_latest(series_id):
    """Return the two most recent (date, value) observations for a FRED series, skipping blank/missing values."""
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()

    observations = []
    for line in resp.text.strip().splitlines()[1:]:  # skip header row
        parts = line.split(",")
        if len(parts) != 2:
            continue
        date_str, value_str = parts
        if not value_str or value_str == ".":
            continue
        try:
            observations.append((date_str, float(value_str)))
        except ValueError:
            continue

    if len(observations) < 2:
        return None
    return observations[-1], observations[-2]


def fetch_yahoo_daily_bars(symbol):
    """Return [(date, close), ...] daily bars, oldest first, via Yahoo's chart JSON.

    Fetched with plain requests instead of yfinance/curl_cffi: curl_cffi's Chrome-TLS
    impersonation (used to clear Yahoo's bot check) is broken by TLS-terminating egress
    proxies like the Claude Code routine sandbox, which reset the connection. Plain
    requests to the same endpoint works fine. Dates use meta.gmtoffset to get the
    exchange-local trading day, matching the localized index yfinance used to provide.
    """
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=1d"
    resp = requests.get(url, timeout=15, headers=_BROWSER_HEADERS)
    resp.raise_for_status()
    result = resp.json()["chart"]["result"][0]
    offset = result.get("meta", {}).get("gmtoffset", 0) or 0
    timestamps = result.get("timestamp", []) or []
    closes = result.get("indicators", {}).get("quote", [{}])[0].get("close", []) or []
    bars = []
    for ts, close in zip(timestamps, closes):
        if close is None:      # skip the live partial bar's null / mid-series gaps
            continue
        bars.append((datetime.utcfromtimestamp(ts + offset).date(), float(close)))
    return bars


def get_10y_treasury_row():
    """Fetch the 10Y Treasury yield (FRED series DGS10) with retries, returning a formatted table row."""
    retries = 3
    for attempt in range(retries):
        try:
            result = fetch_fred_series_latest("DGS10")
            if result is None:
                return "| 10Y Treasury | N/A | N/A |"

            (_, latest_val), (_, prev_val) = result
            change = latest_val - prev_val
            return f"| 10Y Treasury | {latest_val:.3f}% | {change:+.3f} |"
        except Exception as e:
            log_event(f"Error fetching 10Y Treasury from FRED (attempt {attempt+1}/{retries}): {e}")
            if attempt == retries - 1:
                return "| 10Y Treasury | Error | Error |"
            time.sleep(2)


def get_market_data():
    symbols = {
        "S&P 500": "^GSPC",
        "NASDAQ": "^IXIC",
        "DOW": "^DJI",
        "Bitcoin": "BTC-USD"
    }

    lines = [
        "MARKETS",
        "----------",
        "| Symbol | Close | Change |",
        "|---|---|---|"
    ]

    today_date = datetime.now().date()
    yesterday = (datetime.now() - timedelta(days=1)).date()

    for name, ticker in symbols.items():
        # Added retry logic for each ticker
        retries = 3
        for attempt in range(retries):
            try:
                bars = fetch_yahoo_daily_bars(ticker)

                # Empty bars means Yahoo returned no usable data — retrying immediately
                # just repeats the failure, so surface it clearly and move on.
                if not bars:
                    log_event(f"{name}: Yahoo chart returned no bars")
                    lines.append(f"| {name} | N/A | N/A |")
                    break

                # Indices: use settled bars only (drop today's live partial bar).
                # BTC trades continuously, so its latest bar is always current.
                if ticker == "BTC-USD":
                    settled = bars
                else:
                    settled = [b for b in bars if b[0] < today_date]

                if len(settled) < 2:
                    lines.append(f"| {name} | N/A | N/A |")
                    break # Success but not enough data

                current_close = settled[-1][1]
                prev_close = settled[-2][1]
                last_close_date = settled[-1][0]
                change_pct = ((current_close - prev_close) / prev_close) * 100

                if ticker == "BTC-USD":
                    lines.append(f"| {name} | ${current_close:,.2f} | {change_pct:+.2f}% |")
                elif not market_was_open(yesterday):
                    # Genuine weekend/holiday — show the last close without a change
                    lines.append(f"| {name} | {current_close:,.2f} | Market Closed |")
                else:
                    # Yesterday was a trading day. If Yahoo hasn't published its
                    # settled bar yet (early-morning lag), still show the change
                    # between the two most recent closes, dated so it's honest.
                    if last_close_date < yesterday:
                        date_note = f" (as of {last_close_date.strftime('%b %-d')})"
                        log_event(f"{name}: settled bar for {yesterday} not yet published — showing {last_close_date} close")
                    else:
                        date_note = ""
                    lines.append(f"| {name} | {current_close:,.2f}{date_note} | {change_pct:+.2f}% |")

                break # Success

            except Exception as e:
                log_event(f"Error fetching {name} (attempt {attempt+1}/{retries}): {e}")
                if attempt == retries - 1:
                    lines.append(f"| {name} | Error | Error |")
                else:
                    time.sleep(2) # Wait before retry

        # 10Y Treasury slots in right after DOW, before Bitcoin, to preserve table order
        if name == "DOW":
            lines.append(get_10y_treasury_row())

        # Add a small delay between different symbols to avoid rate limits
        time.sleep(1)

    return "\n".join(lines)


# timeframe/removeduplicate are paid-tier NewsData params. The free plan rejects
# them on every call, so after one rejection in a run we stop sending them and
# save the wasted API call per remaining category.
_newsdata_enhanced_ok = True


def fetch_news_category(category, country="us,gb", prioritydomain="top", max_age_hours=24):
    global _newsdata_enhanced_ok
    api_key = os.getenv("NEWSDATA_API_KEY")
    if not api_key or api_key == "your_api_key_here":
        return f"({category} news skipped - NEWSDATA_API_KEY not configured)"

    base_url = f"https://newsdata.io/api/1/news?apikey={api_key}&category={category}&language=en&country={country}&prioritydomain={prioritydomain}"
    try:
        if _newsdata_enhanced_ok:
            resp = requests.get(base_url + "&timeframe=24&removeduplicate=1", timeout=30).json()
            if resp.get('status') == 'error':
                msg = str(resp.get('results', {}).get('message', 'Unknown'))
                log_event(f"NewsData rejected enhanced params for {category} ({msg}); using basic params this run")
                _newsdata_enhanced_ok = False
                resp = requests.get(base_url, timeout=30).json()
        else:
            resp = requests.get(base_url, timeout=30).json()
        if resp.get('status') == 'error':
            log_event(f"NewsData API Error: {resp.get('results', {}).get('message', 'Unknown')}")
            return f"(Error fetching {category} news: API limit reached or key invalid)"

        # Filter to a fresh, junk-free, deduped pool (max 15) for Gemini to choose from
        articles = _filter_articles(resp.get('results', []), max_age_hours)[:15]

        if not articles:
            return f"(No sufficiently fresh {category} news found)"

        articles = _enrich_with_fulltext(articles, cap=8)
        return _format_articles(articles)
    except Exception as e:
        log_event(f"Error fetching {category} news: {e}")
        return f"(Error fetching {category} news: {e})"


def fetch_news_query(query, limit=10, max_age_hours=72):
    """Like fetch_news_category but searches by keyword query instead of category.
    Sports queries are sparse, so the freshness window is wider and no full-text enrichment
    is done (the prompt summarizes these pools down to 1-2 items anyway)."""
    api_key = os.getenv("NEWSDATA_API_KEY")
    if not api_key or api_key == "your_api_key_here":
        return f"({query} news skipped - NEWSDATA_API_KEY not configured)"

    url = f"https://newsdata.io/api/1/news?apikey={api_key}&q={requests.utils.quote(query)}&language=en&prioritydomain=top"
    try:
        resp = requests.get(url, timeout=30).json()
        if resp.get('status') == 'error':
            log_event(f"NewsData query error for '{query}': {resp.get('results', {}).get('message', 'Unknown')}")
            return f"(Error fetching news for '{query}': API limit reached or key invalid)"

        articles = _filter_articles(resp.get('results', []), max_age_hours)[:limit]

        if not articles:
            return f"(No sufficiently fresh news found for '{query}')"

        return _format_articles(articles)
    except Exception as e:
        log_event(f"Error fetching news query '{query}': {e}")
        return f"(Error fetching news for '{query}': {e})"


def fetch_rss_feed(url, limit=1):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
        resp = requests.get(url, timeout=30, headers=headers)
        if resp.status_code != 200:
            log_event(f"RSS feed fetch got HTTP {resp.status_code} for {url}")
            return f"(Error fetching RSS feed: HTTP {resp.status_code})"

        feed = feedparser.parse(resp.content)
        entries = feed.entries[:limit]

        if not entries:
            log_event(f"RSS feed returned 200 but no parseable entries for {url}")
            return "(No RSS entries found)"

        news_text = ""
        for i, entry in enumerate(entries):
            title = entry.get('title', 'No Title')
            desc = entry.get('summary', 'No Description')
            news_text += f"Story {i+1}:\nTitle: {title}\nDescription: {desc}\n\n"
        return news_text
    except Exception as e:
        log_event(f"Error fetching RSS feed: {e}")
        return f"(Error fetching RSS feed: {e})"


# ── Sports fetchers ────────────────────────────────────────────────────────────

def get_pll_standings():
    """Return a formatted standings table for the Premier Lacrosse League.

    Retries on transient fetch errors — a single ESPN blip used to fall straight to the
    "(PLL standings unavailable)" placeholder, which the newsletter prompt then had no
    table rows to render, leaving the Sports sub-block looking empty instead of showing
    an explanatory message.
    """
    retries = 3
    for attempt in range(retries):
        try:
            resp = requests.get(
                "https://site.api.espn.com/apis/v2/sports/lacrosse/pll/standings",
                timeout=10
            ).json()
            entries = resp.get("standings", {}).get("entries", [])
            if not entries:
                return "(PLL standings unavailable)"

            # Sort by wins desc, then losses asc
            def sort_key(e):
                stats = {s["name"]: s["value"] for s in e.get("stats", [])}
                return (-stats.get("wins", 0), stats.get("losses", 99))

            entries_sorted = sorted(entries, key=sort_key)

            lines = ["PLL STANDINGS", "| # | Team | W | L |", "|---|---|---|---|"]
            for rank, e in enumerate(entries_sorted, 1):
                stats = {s["name"]: s["displayValue"] for s in e.get("stats", [])}
                name = e.get("team", {}).get("displayName", "Unknown")
                w = stats.get("wins", "?")
                l = stats.get("losses", "?")
                lines.append(f"| {rank} | {name} | {w} | {l} |")
            return "\n".join(lines)
        except Exception as e:
            log_event(f"Error fetching PLL standings (attempt {attempt+1}/{retries}): {e}")
            if attempt == retries - 1:
                return "(PLL standings unavailable)"
            time.sleep(2)


def get_pll_next_event():
    """Return the matchups and times for the next PLL event weekend.

    Retries on transient fetch errors — see get_pll_standings() for why this matters.
    """
    retries = 3
    for attempt in range(retries):
        try:
            resp = requests.get(
                "https://site.api.espn.com/apis/site/v2/sports/lacrosse/pll/scoreboard",
                timeout=10
            ).json()
            events = resp.get("events", [])
            if not events:
                return "(No upcoming PLL games found)"

            # Find the earliest upcoming event date, then list all games on that date cluster
            future_events = []
            now_utc = datetime.now(timezone.utc)
            for e in events:
                raw_date = e.get("date", "")
                try:
                    dt = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                    if dt > now_utc:
                        future_events.append((dt, e))
                except Exception:
                    pass

            if not future_events:
                return "(No upcoming PLL games found)"

            # Group by the date of the nearest event weekend (within 3 days of the first game)
            future_events.sort(key=lambda x: x[0])
            first_dt = future_events[0][0]
            weekend_events = [(dt, e) for dt, e in future_events if (dt - first_dt).days <= 3]

            lines = [f"PLL NEXT EVENT — {first_dt.astimezone(_PT).strftime('%B %-d, %Y')}"]
            for dt, e in weekend_events:
                name = e.get("name", "TBD")
                time_str = _fmt_pt(e.get("date", ""))
                lines.append(f"  {name} — {time_str}")
            return "\n".join(lines)
        except Exception as e:
            log_event(f"Error fetching PLL schedule (attempt {attempt+1}/{retries}): {e}")
            if attempt == retries - 1:
                return "(PLL schedule unavailable)"
            time.sleep(2)


def get_padres_summary():
    """Return last game result and next game for the San Diego Padres."""
    try:
        today = datetime.now().date()
        start = (today - timedelta(days=7)).strftime("%Y-%m-%d")
        end = (today + timedelta(days=10)).strftime("%Y-%m-%d")
        url = (
            f"https://statsapi.mlb.com/api/v1/schedule"
            f"?sportId=1&teamId=135&startDate={start}&endDate={end}&hydrate=team,linescore"
        )
        resp = requests.get(url, timeout=10).json()

        last_game = None
        next_game = None
        today_utc = datetime.now(timezone.utc).date()

        for day in resp.get("dates", []):
            for game in day.get("games", []):
                state = game.get("status", {}).get("abstractGameState", "")
                game_date_str = game.get("officialDate", "")
                teams = game.get("teams", {})
                home = teams.get("home", {})
                away = teams.get("away", {})

                if state == "Final":
                    last_game = game
                elif state in ("Preview", "Scheduled") and next_game is None:
                    next_game = game

        parts = []

        if last_game:
            teams = last_game.get("teams", {})
            home = teams.get("home", {})
            away = teams.get("away", {})
            home_name = home.get("team", {}).get("abbreviation", "?")
            away_name = away.get("team", {}).get("abbreviation", "?")
            home_score = home.get("score", "?")
            away_score = away.get("score", "?")
            game_date = last_game.get("officialDate", "")

            # Determine if Padres (team 135) were home or away
            padres_home = home.get("team", {}).get("id") == 135
            if padres_home:
                opp = away_name
                padres_score = home_score
                opp_score = away_score
                location = "vs"
            else:
                opp = home_name
                padres_score = away_score
                opp_score = home_score
                location = "@"

            try:
                outcome = "beat" if int(padres_score) > int(opp_score) else "lost to"
            except Exception:
                outcome = "vs"
            parts.append(f"Last game ({game_date}): Padres {outcome} {opp} {location} {opp_score}, {padres_score}-{opp_score}")
        else:
            parts.append("Last game: N/A")

        if next_game:
            teams = next_game.get("teams", {})
            home = teams.get("home", {})
            away = teams.get("away", {})
            home_name = home.get("team", {}).get("abbreviation", "?")
            away_name = away.get("team", {}).get("abbreviation", "?")
            game_date = next_game.get("officialDate", "")
            game_datetime = next_game.get("gameDate", "")
            padres_home = home.get("team", {}).get("id") == 135
            if padres_home:
                opp = away_name
                location = "vs"
            else:
                opp = home_name
                location = "@"
            time_str = _fmt_pt(game_datetime) if game_datetime else game_date
            parts.append(f"Next game: Padres {location} {opp} — {time_str}")
        else:
            parts.append("Next game: N/A")

        return "\n".join(parts)
    except Exception as e:
        log_event(f"Error fetching Padres summary: {e}")
        return "(Padres game data unavailable)"


def get_padres_standings():
    """Return the Padres' current NL West division standing."""
    try:
        year = datetime.now().year
        url = f"https://statsapi.mlb.com/api/v1/standings?leagueId=104&season={year}&standingsTypes=regularSeason"
        resp = requests.get(url, timeout=10).json()

        for rec in resp.get("records", []):
            if rec.get("division", {}).get("id") != 203:  # 203 = NL West
                continue
            for tr in rec.get("teamRecords", []):
                if tr.get("team", {}).get("id") == 135:  # 135 = Padres
                    rank = tr.get("divisionRank", "?")
                    wins = tr.get("wins", "?")
                    losses = tr.get("losses", "?")
                    gb = tr.get("gamesBack", "—")
                    gb_str = f"{gb} GB" if gb and gb != "-" else "— (1st)"
                    return f"Padres standing: {rank} in NL West, {wins}-{losses}, {gb_str}"

        return "(Padres standings unavailable)"
    except Exception as e:
        log_event(f"Error fetching Padres standings: {e}")
        return "(Padres standings unavailable)"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-only", action="store_true",
        help="Fetch all raw data (market/news/sports/story) and print it as JSON to "
             "stdout instead of calling Gemini and sending email. Used by the Claude "
             "Code routine that replaced the Gemini synthesis step.",
    )
    args = parser.parse_args()

    log_event("Starting Lithrop Ledger generation (Hybrid Architecture)...")
    wait_for_network()

    market_table = get_market_data()

    # 1. Fetch all raw data first
    world_news     = fetch_news_category("world")
    us_news        = fetch_news_category("domestic")
    financial_news = fetch_news_category("business")
    tech_news      = fetch_news_category("technology")

    uplifting_rss_url = "https://www.goodnewsnetwork.org/category/news/feed/"
    uplifting_news = fetch_rss_feed(uplifting_rss_url, limit=1)

    # 2. Fetch sports data
    pll_standings   = get_pll_standings()
    pll_next_event  = get_pll_next_event()
    redwoods_news   = fetch_news_query("California Redwoods lacrosse")
    padres_summary  = get_padres_summary()
    padres_standing = get_padres_standings()
    padres_news     = fetch_news_query("San Diego Padres")

    weekend_tag = "  —  WEEKEND EDITION" if datetime.now().weekday() >= 5 else ""

    if args.data_only:
        payload = {
            "date": datetime.now().strftime('%A, %B %d, %Y'),
            "weekend_tag": weekend_tag,
            "market_table": market_table,
            "world_news": world_news,
            "us_news": us_news,
            "financial_news": financial_news,
            "tech_news": tech_news,
            "pll_standings": pll_standings,
            "pll_next_event": pll_next_event,
            "redwoods_news": redwoods_news,
            "padres_summary": padres_summary,
            "padres_standing": padres_standing,
            "padres_news": padres_news,
            "uplifting_news": uplifting_news,
        }
        print(json.dumps(payload))
        log_event("Lithrop Ledger --data-only run finished.")
        return

    # 3. Construct prompt for Gemini
    prompt = f"""You are generating the daily Lithrop Ledger newsletter for Paul. Output ONLY a complete, valid HTML email document — no text before <!DOCTYPE html>, no text after </html>, no markdown, no code fences.

DATE: {datetime.now().strftime('%A, %B %d, %Y')}{weekend_tag}

═══════════════════════════════════════
RAW DATA — use ONLY this. Do NOT add outside information.
═══════════════════════════════════════

--- MARKET DATA (previous trading day close) ---
{market_table}

--- WORLD NEWS (last 24 hours) ---
{world_news}

--- US NEWS (last 24 hours) ---
{us_news}

--- FINANCIAL NEWS (last 24 hours) ---
{financial_news}

--- TECH NEWS (last 24 hours) ---
{tech_news}

--- SPORTS DATA ---

[PLL STANDINGS]
{pll_standings}

[PLL NEXT EVENT]
{pll_next_event}

[CALIFORNIA REDWOODS NEWS]
{redwoods_news}

[PADRES GAME DATA]
{padres_summary}
{padres_standing}

[PADRES NEWS]
{padres_news}

--- UPLIFTING STORY ---
{uplifting_news}

═══════════════════════════════════════
EDITORIAL FILTERS
═══════════════════════════════════════

For each news section, evaluate all provided articles and select the 3-5 most significant. Quality over quantity: if fewer than 5 articles meet the significance and recency bar, include fewer — never pad a section with marginal or older stories.

RECENCY: Every article is timestamped with its age and publish time. Strongly prefer the newest coverage; when two candidate stories are otherwise comparable in significance, always pick the fresher one. Never present a story as breaking news if it is more than a day old.

NO DUPLICATES: Never select two stories covering the same underlying event, product, or announcement — pick the single best-sourced, freshest article on that topic and skip the rest.

Apply strict significance criteria per section:

WORLD NEWS: Major geopolitical events, international armed conflicts, high-stakes diplomatic developments, global economic crises affecting multiple nations. Exclude: single-country domestic politics (unless globally significant), regional city/state-level stories, minor local incidents.

US NEWS: Federal policy and legislation, Congressional votes, Supreme Court decisions, major national disasters or crises, high-profile national stories with broad public impact. Exclude: state politics, city politics, or regional events unless unmistakably nationally significant.

FINANCIAL NEWS: Major market moves, central bank policy decisions, significant earnings from large publicly traded companies, major macroeconomic data releases (CPI, jobs, GDP, etc.).

TECH NEWS: Significant product launches or major releases from notable companies, large acquisitions or mergers, major regulatory actions against tech companies, breakthrough research from credible institutions. Exclude: puzzle/game hints or answers (Wordle, Connections, Strands, crosswords), app-of-the-day filler, deals/shopping roundups, and "what to watch" listicles — these are never significant.

SPORTS: Render PLL standings and schedule VERBATIM from the raw data — do not alter scores, records, or times. If the PLL standings or schedule data is one of its "(...unavailable)" / "(No upcoming PLL games found)" placeholder messages rather than real data, display that message as a single line of text instead of an empty table — never render an empty table. Summarize the California Redwoods and Padres news pools into 1–2 items each (only include items of genuine significance — roster moves, injuries, notable performances, contract news; skip fluff). Render the Padres game data (last game / next game / standing) VERBATIM from the raw data.

STORY DEPTH: Write 4-6 substantive sentences per story, drawing on the "Full text (excerpt)" when provided — include specifics: names, numbers, quotes, and context. For stories with only a short description, write what the source supports and no more; NEVER invent details not present in the raw data. For major, high-impact stories (wars, landmark legislation, large market moves, major acquisitions) write comprehensive coverage with full context — no upper sentence limit. Do not pad minor stories with filler.

NO AI-speak, no conversational filler, no meta-commentary.

═══════════════════════════════════════
HTML STRUCTURE AND STYLING
═══════════════════════════════════════

Produce a complete HTML document with this exact structure and styling:

DOCUMENT: <!DOCTYPE html><html lang="en"> with <head> containing charset UTF-8 and viewport meta tags.

BODY: background-color:#f0f0f0; font-family:system-ui,-apple-system,Arial,sans-serif; margin:0; padding:0

CONTAINER: <div> max-width:680px; margin:0 auto; background:#ffffff; padding:36px 44px

HEADER (centered, border-bottom:3px solid #1a1a1a, padding-bottom:18px, margin-bottom:24px):
  - <h1> "The Lithrop Ledger" — font-size:30px; letter-spacing:4px; text-transform:uppercase; font-weight:800; color:#1a1a1a; margin:0
  - <p> date line — font-size:14px; color:#555; letter-spacing:1px; margin:8px 0 5px

SECTION HEADERS <h2>: font-size:12px; letter-spacing:2px; text-transform:uppercase; color:#1a1a1a; border-bottom:1px solid #e0e0e0; padding-bottom:6px; margin:0 0 10px

MARKETS TABLE (immediately after the Markets <h2>, margin-bottom:28px):
  - <table> width:100%; border-collapse:collapse; font-size:14px
  - Header row <tr>: background:#1a1a1a; color:#ffffff; each <th> padding:8px 12px; text-align:left
  - Header columns: Symbol | Close | Change
  - Data rows <tr>: alternating background #fafafa / #ffffff; each <td> padding:7px 12px
  - Change column coloring:
      Starts with "+" → color:#2e7d32; font-weight:600
      Starts with "-" → color:#c62828; font-weight:600
      Equals "Market Closed" → color:#888; font-style:italic (no bold)
      "N/A" or "Error" → color:#888

STORY FORMAT (for each news story):
  <div style="margin-bottom:20px;">
    <p style="margin:0 0 5px;font-size:15px;font-weight:700;color:#1a1a1a;line-height:1.3;">[Story Title]</p>
    <p style="margin:0;font-size:14px;color:#333;line-height:1.65;">[Story body — at least 3 sentences, no upper limit for major stories]</p>
  </div>

Wrap each section's stories in: <div style="margin-bottom:28px;">

SECTIONS IN ORDER:
1. Header
2. Markets (table)
3. World News (3-5 stories)
4. U.S. News (3-5 stories)
5. Finance (3-5 stories)
6. Technology (3-5 stories)
7. Sports (see spec below)
8. Good News (1 uplifting story)
9. Footer

SPORTS SECTION SPEC (section 7, after Technology):
Use <h2> "Sports" as the section header.
Divide into two labeled sub-blocks, each with a sub-header <h3> (font-size:11px; letter-spacing:1.5px; text-transform:uppercase; color:#555; margin:16px 0 8px):

  SUB-BLOCK A — "Premier Lacrosse League"
    - PLL Standings table: same styling as the Markets table above (dark header row, alternating rows).
      Header columns: # | Team | W | L
      Populate with data from [PLL STANDINGS] verbatim. If [PLL STANDINGS] is its "(PLL standings unavailable)"
      placeholder message rather than real data, display that message as a single <p> in color:#888 instead
      of an empty table.
    - Next Event: after the table, a small <p style="font-size:13px;color:#555;margin:8px 0 14px;"> listing the games from [PLL NEXT EVENT], one game per line using <br>. If [PLL NEXT EVENT] is its "(No upcoming PLL games found)" or "(PLL schedule unavailable)" placeholder message, display that message instead of an empty list.
    - California Redwoods news: 1–2 items in STORY FORMAT.

  SUB-BLOCK B — "San Diego Padres"
    - Game recap block: a <div style="background:#f7f7f7;border-left:3px solid #1a1a1a;padding:10px 14px;margin-bottom:14px;font-size:14px;line-height:1.8;color:#333;">
        showing Last game, Next game, and Standing from [PADRES GAME DATA] — three lines, labels in <strong>.
    - Padres news: 1–2 items in STORY FORMAT.

Wrap the entire Sports section in: <div style="margin-bottom:28px;">

FOOTER: margin-top:32px; border-top:1px solid #e0e0e0; padding-top:14px; text-align:center
  <p> font-size:11px; color:#aaa; letter-spacing:1px — "THE LITHROP LEDGER — {datetime.now().strftime('%B %d, %Y')}"


CRITICAL: Output ONLY raw HTML starting with <!DOCTYPE html>. No markdown. No code fences. No preamble. No commentary after </html>. Fill in all content from RAW DATA — do not leave template placeholders or HTML comments in the output.
"""

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    try:
        # Note: We are no longer passing the 'google_search' tool config
        response = generate_content_with_retry(
            client=client,
            model='gemini-3.1-pro-preview',
            contents=prompt
        )

        html_output = response.text.strip()
        if html_output.startswith("```"):
            html_output = html_output.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        send_html_email("Lithrop Ledger Newsletter", html_output)
        log_event("Lithrop Ledger finished successfully.")
    except Exception as e:
        log_event(f"Lithrop Ledger failed after retries: {e}")
        notify("Lithrop Ledger Error", f"Gemini generation failed after multiple attempts: {e}")

if __name__ == "__main__":
    main()
