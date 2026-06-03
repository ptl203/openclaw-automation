import os
import time
import smtplib
import requests
from datetime import datetime
from email.message import EmailMessage
from dotenv import load_dotenv

load_dotenv(override=True)

STORMGLASS_TIMEOUT = 20  # seconds per attempt
STORMGLASS_MAX_RETRIES = 3
STORMGLASS_RETRY_BACKOFF = 5  # seconds, doubled each retry


def fetch_stormglass(lat, lng, params, timeout=STORMGLASS_TIMEOUT,
                     max_retries=STORMGLASS_MAX_RETRIES,
                     backoff=STORMGLASS_RETRY_BACKOFF):
    """Fetch a Stormglass weather/point forecast with timeout and retry/backoff.

    Retries on connection/timeout errors and 5xx responses; fails fast on 4xx
    (bad key/params). Re-raises the last exception if all attempts fail.
    """
    api_key = os.getenv("STORMGLASS_API_KEY")
    if not api_key:
        raise ValueError("STORMGLASS_API_KEY not set")
    url = f"https://api.stormglass.io/v2/weather/point?lat={lat}&lng={lng}&params={params}"

    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(
                url,
                headers={"Authorization": api_key},
                timeout=timeout,
            )
            resp.raise_for_status()
            return resp.json()
        except (requests.Timeout, requests.ConnectionError) as e:
            last_exc = e
            if attempt < max_retries:
                delay = backoff * (2 ** (attempt - 1))
                log_event(
                    f"Stormglass fetch attempt {attempt}/{max_retries} failed: {e}. "
                    f"Retrying in {delay}s..."
                )
                time.sleep(delay)
        except requests.HTTPError as e:
            # 4xx: retrying won't help (bad key/params). 5xx: transient, retry.
            last_exc = e
            status = e.response.status_code if e.response is not None else None
            if status and 500 <= status < 600 and attempt < max_retries:
                delay = backoff * (2 ** (attempt - 1))
                log_event(
                    f"Stormglass returned {status} (attempt {attempt}/{max_retries}). "
                    f"Retrying in {delay}s..."
                )
                time.sleep(delay)
            else:
                raise

    raise last_exc

def send_email(subject, message):
    server = os.getenv("SMTP_SERVER")
    port = os.getenv("SMTP_PORT", 587)
    user = os.getenv("EMAIL_ADDRESS")
    pwd = os.getenv("EMAIL_PASSWORD")
    to_email = os.getenv("TO_EMAIL")
    
    if not all([server, user, pwd, to_email]):
        print(f"Email configuration missing for: {subject}")
        return
        
    msg = EmailMessage()
    msg.set_content(message)
    msg['Subject'] = f"[LobsterClaw] {subject}"
    msg['From'] = user
    msg['To'] = to_email
    
    try:
        s = smtplib.SMTP(server, int(port))
        s.starttls()
        s.login(user, pwd)
        s.send_message(msg)
        s.quit()
        print(f"Sent Email: {subject}")
    except Exception as e:
        print(f"Email failed for {subject}: {e}")

def send_html_email(subject, html_body):
    server = os.getenv("SMTP_SERVER")
    port = os.getenv("SMTP_PORT", 587)
    user = os.getenv("EMAIL_ADDRESS")
    pwd = os.getenv("EMAIL_PASSWORD")
    to_email = os.getenv("TO_EMAIL")

    if not all([server, user, pwd, to_email]):
        print(f"HTML email configuration missing for: {subject}")
        return

    msg = EmailMessage()
    msg['Subject'] = f"[LobsterClaw] {subject}"
    msg['From'] = user
    msg['To'] = to_email
    msg.set_content("This email requires an HTML-capable email client.")
    msg.add_alternative(html_body, subtype='html')

    try:
        s = smtplib.SMTP(server, int(port))
        s.starttls()
        s.login(user, pwd)
        s.send_message(msg)
        s.quit()
        print(f"Sent HTML Email: {subject}")
    except Exception as e:
        print(f"HTML email failed for {subject}: {e}")

def log_event(message):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    log_path = os.path.join(base_dir, "automation.log")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_path, "a") as f:
        f.write(f"[{timestamp}] {message}\n")

def notify(subject, message):
    log_event(f"NOTIFY [{subject}]: {message[:100]}...")
    print(f"--- {subject} ---")
    print(message)
    send_email(subject, message)


def compass_label(deg):
    dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return dirs[round(deg / 22.5) % 16]


def wind_ranges(facing_deg):
    """Return (onshore_start, onshore_end, offshore_start, offshore_end) in degrees."""
    onshore_start = int((facing_deg - 45) % 360)
    onshore_end = int((facing_deg + 45) % 360)
    offshore_center = int((facing_deg + 180) % 360)
    offshore_start = int((offshore_center - 45) % 360)
    offshore_end = int((offshore_center + 45) % 360)
    return onshore_start, onshore_end, offshore_start, offshore_end


def filter_nearby_hours(raw_data, window_hours=3):
    from datetime import datetime, timezone
    now_utc = datetime.now(timezone.utc)
    filtered = []
    for h in raw_data.get("hours", []):
        t = datetime.fromisoformat(h["time"])
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        if abs((t - now_utc).total_seconds()) / 3600 <= window_hours:
            filtered.append(h)
    return {"hours": filtered, "meta": raw_data.get("meta", {})}
