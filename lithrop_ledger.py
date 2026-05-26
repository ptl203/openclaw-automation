import os
import requests
import feedparser
import yfinance as yf
from datetime import datetime, timedelta
from google import genai
from google.genai import types
from utils import notify, log_event, send_html_email
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

def get_market_data():
    symbols = {
        "S&P 500": "^GSPC",
        "NASDAQ": "^IXIC",
        "DOW": "^DJI",
        "10Y Treasury": "^TNX",
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
                    if ticker == "^TNX":
                        lines.append(f"| {name} | {current_close:.3f}% | Market Closed |")
                    else:
                        lines.append(f"| {name} | {current_close:,.2f} | Market Closed |")
                else:
                    prev_close = hist['Close'].iloc[prev_idx]
                    change_pct = ((current_close - prev_close) / prev_close) * 100

                    if ticker == "^TNX":
                        change_diff = current_close - prev_close
                        lines.append(f"| {name} | {current_close:.3f}% | {change_diff:+.3f} |")
                    elif ticker == "BTC-USD":
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
        
        # Add a small delay between different symbols to avoid rate limits
        time.sleep(1)
            
    return "\n".join(lines)


def fetch_news_category(category, country="us,gb", prioritydomain="top", timeframe=None):
    api_key = os.getenv("NEWSDATA_API_KEY")
    if not api_key or api_key == "your_api_key_here":
        return f"({category} news skipped - NEWSDATA_API_KEY not configured)"

    url = f"https://newsdata.io/api/1/news?apikey={api_key}&category={category}&language=en&country={country}&prioritydomain={prioritydomain}"
    if timeframe is not None:
        url += f"&timeframe={timeframe}"
    try:
        resp = requests.get(url).json()
        if resp.get('status') == 'error':
            log_event(f"NewsData API Error: {resp.get('results', {}).get('message', 'Unknown')}")
            return f"(Error fetching {category} news: API limit reached or key invalid)"
            
        # We fetch up to 15 articles to give Gemini a pool to choose from
        articles = resp.get('results', [])[:15]
        
        if not articles:
            return f"(No {category} news found)"
            
        news_text = ""
        for i, article in enumerate(articles):
            title = article.get('title', 'No Title')
            desc = article.get('description', 'No Description')
            if not desc:
                desc = "No detailed description available."
            news_text += f"Article {i+1}:\nTitle: {title}\nDescription: {desc}\n\n"
        return news_text
    except Exception as e:
        log_event(f"Error fetching {category} news: {e}")
        return f"(Error fetching {category} news: {e})"

def fetch_rss_feed(url, limit=1):
    try:
        feed = feedparser.parse(url)
        entries = feed.entries[:limit]
        
        if not entries:
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

def main():
    log_event("Starting Lithrop Ledger generation (Hybrid Architecture)...")
    
    market_table = get_market_data()
    
    # 1. Fetch all raw data first
    world_news     = fetch_news_category("world")
    us_news        = fetch_news_category("domestic")
    financial_news = fetch_news_category("business")
    tech_news      = fetch_news_category("technology")
    
    uplifting_rss_url = "https://www.goodnewsnetwork.org/category/news/feed/"
    uplifting_news = fetch_rss_feed(uplifting_rss_url, limit=1)
    
    # 2. Construct prompt for Gemini
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

--- UPLIFTING STORY ---
{uplifting_news}

═══════════════════════════════════════
EDITORIAL FILTERS
═══════════════════════════════════════

For each news section, evaluate all provided articles and select the 5 most significant. Apply strict significance criteria per section:

WORLD NEWS: Major geopolitical events, international armed conflicts, high-stakes diplomatic developments, global economic crises affecting multiple nations. Exclude: single-country domestic politics (unless globally significant), regional city/state-level stories, minor local incidents.

US NEWS: Federal policy and legislation, Congressional votes, Supreme Court decisions, major national disasters or crises, high-profile national stories with broad public impact. Exclude: state politics, city politics, or regional events unless unmistakably nationally significant.

FINANCIAL NEWS: Major market moves, central bank policy decisions, significant earnings from large publicly traded companies, major macroeconomic data releases (CPI, jobs, GDP, etc.).

TECH NEWS: Significant product launches or major releases from notable companies, large acquisitions or mergers, major regulatory actions against tech companies, breakthrough research from credible institutions.

STORY DEPTH: Write at least 3 sentences per story. For major, high-impact stories (wars, landmark legislation, large market moves, major acquisitions) write comprehensive coverage with full context — no upper sentence limit. Do not pad minor stories with filler.

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
3. World News (5 stories)
4. U.S. News (5 stories)
5. Finance (5 stories)
6. Technology (5 stories)
7. Good News (1 uplifting story)
8. Footer

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