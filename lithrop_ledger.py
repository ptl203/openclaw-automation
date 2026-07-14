import os
import re
import requests
import feedparser
import yfinance as yf
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
                stock = yf.Ticker(ticker)
                hist = stock.history(period="5d")

                # yfinance returns None/empty when rate-limited — retrying immediately
                # just repeats the failure, so surface it clearly and move on.
                if hist is None or hist.empty:
                    log_event(f"{name}: yfinance returned no history (likely rate-limited)")
                    lines.append(f"| {name} | N/A | N/A |")
                    break

                if len(hist) < 2:
                    lines.append(f"| {name} | N/A | N/A |")
                    break # Success but no data

                last_idx = -1
                prev_idx = -2

                if ticker != "BTC-USD" and hist.index[-1].date() == today_date:
                    last_idx = -2
                    prev_idx = -3

                current_close = hist['Close'].iloc[last_idx]
                last_close_date = hist.index[last_idx].date()
                market_open_yesterday = ticker == "BTC-USD" or last_close_date >= yesterday

                if not market_open_yesterday:
                    lines.append(f"| {name} | {current_close:,.2f} | Market Closed |")
                else:
                    prev_close = hist['Close'].iloc[prev_idx]
                    change_pct = ((current_close - prev_close) / prev_close) * 100

                    if ticker == "BTC-USD":
                        lines.append(f"| {name} | ${current_close:,.2f} | {change_pct:+.2f}% |")
                    else:
                        lines.append(f"| {name} | {current_close:,.2f} | {change_pct:+.2f}% |")

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
    """Return a formatted standings table for the Premier Lacrosse League."""
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
        log_event(f"Error fetching PLL standings: {e}")
        return "(PLL standings unavailable)"


def get_pll_next_event():
    """Return the matchups and times for the next PLL event weekend."""
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
        log_event(f"Error fetching PLL schedule: {e}")
        return "(PLL schedule unavailable)"


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


def get_worldcup_today():
    """Return today's FIFA World Cup match schedule in Pacific time."""
    try:
        today_str = datetime.now().strftime("%Y%m%d")
        url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard?dates={today_str}"
        resp = requests.get(url, timeout=10).json()
        events = resp.get("events", [])

        if not events:
            return "(No FIFA World Cup matches today)"

        lines = [f"FIFA WORLD CUP — Today's Matches ({datetime.now().strftime('%B %-d, %Y')})"]
        for e in events:
            comps = e.get("competitions", [{}])[0]
            competitors = comps.get("competitors", [])
            team_names = [c.get("team", {}).get("displayName", "?") for c in competitors]
            match_time = _fmt_pt(e.get("date", ""))
            status_desc = comps.get("status", {}).get("type", {}).get("description", "")

            if status_desc in ("Final", "Full Time"):
                scores = {c.get("homeAway"): c.get("score", "?") for c in competitors}
                home_score = scores.get("home", "?")
                away_score = scores.get("away", "?")
                lines.append(f"  {' vs '.join(team_names)} — FINAL {away_score}-{home_score}")
            elif status_desc in ("In Progress", "Halftime"):
                scores = {c.get("homeAway"): c.get("score", "?") for c in competitors}
                home_score = scores.get("home", "?")
                away_score = scores.get("away", "?")
                lines.append(f"  {' vs '.join(team_names)} — LIVE {away_score}-{home_score} ({status_desc})")
            else:
                lines.append(f"  {' vs '.join(team_names)} — {match_time}")
        return "\n".join(lines)
    except Exception as e:
        log_event(f"Error fetching World Cup schedule: {e}")
        return "(FIFA World Cup schedule unavailable)"


def main():
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
    worldcup_today  = get_worldcup_today()

    # 3. Construct prompt for Gemini
    weekend_tag = "  —  WEEKEND EDITION" if datetime.now().weekday() >= 5 else ""
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

[FIFA WORLD CUP — TODAY'S SCHEDULE]
{worldcup_today}

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

SPORTS: Render PLL standings and schedule VERBATIM from the raw data — do not alter scores, records, or times. Summarize the California Redwoods and Padres news pools into 1–2 items each (only include items of genuine significance — roster moves, injuries, notable performances, contract news; skip fluff). Render the Padres game data (last game / next game / standing) VERBATIM from the raw data. Render the World Cup schedule VERBATIM from the raw data.

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
Divide into three labeled sub-blocks, each with a sub-header <h3> (font-size:11px; letter-spacing:1.5px; text-transform:uppercase; color:#555; margin:16px 0 8px):

  SUB-BLOCK A — "Premier Lacrosse League"
    - PLL Standings table: same styling as the Markets table above (dark header row, alternating rows).
      Header columns: # | Team | W | L
      Populate with data from [PLL STANDINGS] verbatim.
    - Next Event: after the table, a small <p style="font-size:13px;color:#555;margin:8px 0 14px;"> listing the games from [PLL NEXT EVENT], one game per line using <br>.
    - California Redwoods news: 1–2 items in STORY FORMAT.

  SUB-BLOCK B — "San Diego Padres"
    - Game recap block: a <div style="background:#f7f7f7;border-left:3px solid #1a1a1a;padding:10px 14px;margin-bottom:14px;font-size:14px;line-height:1.8;color:#333;">
        showing Last game, Next game, and Standing from [PADRES GAME DATA] — three lines, labels in <strong>.
    - Padres news: 1–2 items in STORY FORMAT.

  SUB-BLOCK C — "FIFA World Cup"
    - <p style="font-size:14px;color:#333;line-height:1.8;margin:0 0 14px;"> listing each match from [FIFA WORLD CUP — TODAY'S SCHEDULE], one per line using <br>.
      If no matches today, display the "(No FIFA World Cup matches today)" message as a single <p> in color:#888.

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
