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


# ── Stormglass data helpers ────────────────────────────────────────────────

def param_value(hour, key):
    """Extract a numeric value from a Stormglass per-source param dict.

    Prefers the 'sg' (Stormglass model) source; falls back to the first
    numeric source found. Returns None if no numeric value is available.
    """
    sources = hour.get(key, {})
    if not isinstance(sources, dict):
        return None
    if "sg" in sources and sources["sg"] is not None:
        try:
            return float(sources["sg"])
        except (TypeError, ValueError):
            pass
    for v in sources.values():
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def closest_hour(nearby_data):
    """Return the hourly data point nearest to now from filter_nearby_hours output."""
    from datetime import datetime, timezone
    now_utc = datetime.now(timezone.utc)
    hours = nearby_data.get("hours", [])
    if not hours:
        return None

    def age(h):
        t = datetime.fromisoformat(h["time"])
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return abs((t - now_utc).total_seconds())

    return min(hours, key=age)


# ── Unit conversions ───────────────────────────────────────────────────────

def m_to_ft(m):
    return (m or 0) * 3.28084

def mps_to_kts(mps):
    return (mps or 0) * 1.94384

def c_to_f(c):
    return c * 9 / 5 + 32 if c is not None else None


# ── Condition scoring ──────────────────────────────────────────────────────

def _in_arc(angle, start, end):
    """True if *angle* lies within the clockwise arc from *start* to *end*."""
    if start <= end:
        return start <= angle <= end
    return angle >= start or angle <= end  # arc wraps through 0/360


def size_score(height_ft):
    """0–1 score peaking at 1.0 across the ideal 2–4 ft band."""
    if height_ft < 0.5:
        return 0.05
    if height_ft < 1.0:
        return 0.15 + (height_ft - 0.5) * 0.40    # 0.15 → 0.35
    if height_ft < 2.0:
        return 0.35 + (height_ft - 1.0) * 0.45    # 0.35 → 0.80
    if height_ft <= 4.0:
        return 1.0                                   # ideal band
    if height_ft <= 5.0:
        return 1.0 - (height_ft - 4.0) * 0.25      # 1.0 → 0.75
    if height_ft <= 6.0:
        return 0.75 - (height_ft - 5.0) * 0.25     # 0.75 → 0.50
    return max(0.10, 0.50 - (height_ft - 6.0) * 0.15)


def period_score(period_s):
    """0–1 score; 1.0 for the 12–15 s groundswell sweet spot."""
    if period_s is None or period_s < 1:
        return 0.10
    if period_s < 6:
        return 0.15
    if period_s < 9:
        return 0.15 + (period_s - 6) * 0.083       # 0.15 → 0.40
    if period_s < 12:
        return 0.40 + (period_s - 9) * 0.20         # 0.40 → 1.00
    if period_s <= 15:
        return 1.0                                   # optimal groundswell
    return 0.90                                      # very long period — still great


def wind_quality(wind_dir_from, wind_kts, facing_dir):
    """Return (label, score 0–1) based on wind direction/speed vs. beach facing.

    Classifies using wind_ranges(); scales score by strength so that
    light onshore << strong onshore and offshore/glassy score near 1.0.
    """
    on_start, on_end, off_start, off_end = wind_ranges(facing_dir)

    # Under 3 kts the surface is clean regardless of direction — glassy conditions.
    if wind_kts < 3:
        return ("glassy", 0.92)

    if _in_arc(wind_dir_from, off_start, off_end):
        label = "offshore"
        score = 1.0 if wind_kts <= 15 else max(0.65, 1.0 - (wind_kts - 15) * 0.025)
    elif _in_arc(wind_dir_from, on_start, on_end):
        label = "onshore"
        # Forgiving for light onshore (barely any texture); steep once it gets choppy.
        if wind_kts <= 6:
            score = 0.70 - (wind_kts - 3) * 0.067  # 0.70 → 0.50
        elif wind_kts <= 10:
            score = 0.50 - (wind_kts - 6) * 0.075  # 0.50 → 0.20
        elif wind_kts <= 15:
            score = 0.20 - (wind_kts - 10) * 0.020  # 0.20 → 0.10
        else:
            score = max(0.05, 0.10 - (wind_kts - 15) * 0.01)
    else:
        label = "cross-shore"
        if wind_kts <= 6:
            score = 0.78
        elif wind_kts <= 12:
            score = 0.78 - (wind_kts - 6) * 0.05   # 0.78 → 0.48
        else:
            score = max(0.15, 0.48 - (wind_kts - 12) * 0.03)

    return (label, round(score, 2))


# Blend ratio for organization_score: energy-partition vs. wavePeriod/swellPeriod ratio
_ORG_ENERGY_BLEND = 0.70  # 70% energy-partition, 30% dominant-period ratio
_WIND_WAVE_PERIOD = 4.0   # assumed period for wind-wave partition (Stormglass doesn't provide it)


def organization_score(swell_h_ft, swell_period, sec_h_ft, sec_period, ww_h_ft, wave_period):
    """0–1 score measuring how clean and organized the swell is.

    Uses energy-partition analysis: each wave partition's energy (∝ height²) is weighted
    by its own period quality. The fraction of energy in long-period (clean) partitions
    is the primary signal. wavePeriod/swellPeriod ratio acts as a cross-check blend.

    Returns (score, clean_pct) where clean_pct is the energy-fraction for display.
    """
    partitions = [(swell_h_ft or 0.0, swell_period or 0.0)]
    if sec_h_ft:
        partitions.append((sec_h_ft, sec_period or 0.0))
    if ww_h_ft:
        partitions.append((ww_h_ft, _WIND_WAVE_PERIOD))

    total_energy = sum(h * h for h, _ in partitions)
    if total_energy < 0.01:
        return (0.50, 50)  # no meaningful data — neutral

    weighted_energy = sum(h * h * period_score(p) for h, p in partitions)
    energy_org = weighted_energy / total_energy

    # wavePeriod/swellPeriod ratio: how much does the dominant sea period match the primary swell?
    if swell_period and swell_period > 0 and wave_period and wave_period > 0:
        ratio = min(1.0, wave_period / swell_period)
    else:
        ratio = energy_org  # fall back to energy signal when periods unavailable

    score = _ORG_ENERGY_BLEND * energy_org + (1 - _ORG_ENERGY_BLEND) * ratio
    clean_pct = round(energy_org * 100)
    return (round(score, 2), clean_pct)


def _org_label(score):
    if score >= 0.85:
        return "clean"
    if score >= 0.70:
        return "mostly clean"
    if score >= 0.50:
        return "mixed"
    return "messy"


# Tunable scoring weights (must sum to 1.0)
_W_SIZE   = 0.20
_W_WIND   = 0.45
_W_PERIOD = 0.20
_W_ORG    = 0.15

# Raw score (0–100) → stars (evaluated highest-threshold first)
_STAR_MAP = [(90, 5), (75, 4), (55, 3), (38, 2), (0, 1)]

# Minimum raw score for a GO recommendation (≥ ★★★)
GO_THRESHOLD = 55


def score_conditions(hour, facing_dir):
    """Score a single Stormglass hourly data point for a given beach facing.

    Returns a dict with all converted values and scores, ready for use in
    the email body and the Gemini verdict prompt.
    """
    height_m       = param_value(hour, "waveHeight") or 0.0
    wave_period    = param_value(hour, "wavePeriod") or 0.0
    wave_dir       = param_value(hour, "waveDirection")
    swell_h_m      = param_value(hour, "swellHeight") or 0.0
    swell_period   = param_value(hour, "swellPeriod") or 0.0
    swell_dir      = param_value(hour, "swellDirection")
    sec_h_m        = param_value(hour, "secondarySwellHeight")
    sec_period     = param_value(hour, "secondarySwellPeriod")
    wind_wave_h_m  = param_value(hour, "windWaveHeight")
    wind_speed_mps = param_value(hour, "windSpeed") or 0.0
    wind_dir_deg   = param_value(hour, "windDirection") or 0.0
    water_temp_c   = param_value(hour, "waterTemperature")

    height_ft    = round(m_to_ft(height_m), 1)
    wind_kts     = round(mps_to_kts(wind_speed_mps), 1)
    water_temp_f = round(c_to_f(water_temp_c), 1) if water_temp_c is not None else None
    swell_h_ft   = round(m_to_ft(swell_h_m), 1)
    sec_h_ft     = round(m_to_ft(sec_h_m), 1) if sec_h_m is not None else None
    ww_h_ft      = round(m_to_ft(wind_wave_h_m), 1) if wind_wave_h_m is not None else None

    # Primary swell period is the best indicator of groundswell power.
    primary_period = swell_period if swell_period > 0 else wave_period

    s_size             = size_score(height_ft)
    wind_label, s_wind = wind_quality(wind_dir_deg, wind_kts, facing_dir)
    s_period           = period_score(primary_period)
    s_org, clean_pct   = organization_score(
        swell_h_ft, swell_period, sec_h_ft, sec_period, ww_h_ft, wave_period
    )

    total = round(
        (_W_SIZE * s_size + _W_WIND * s_wind + _W_PERIOD * s_period + _W_ORG * s_org) * 100
    )

    stars = 1
    for threshold, s in _STAR_MAP:
        if total >= threshold:
            stars = s
            break

    size_label = "small" if height_ft < 2 else "decent" if height_ft <= 4 else "big surf"

    return {
        "stars":            stars,
        "total":            total,
        "sub_scores":       {
            "size":   round(s_size, 2),
            "wind":   round(s_wind, 2),
            "period": round(s_period, 2),
            "org":    s_org,
        },
        "height_ft":        height_ft,
        "size_label":       size_label,
        "wave_period_s":    round(wave_period, 1),
        "wave_dir":         round(wave_dir, 1) if wave_dir is not None else None,
        "swell_h_ft":       swell_h_ft,
        "swell_period_s":   round(swell_period, 1),
        "swell_dir":        round(swell_dir, 1) if swell_dir is not None else None,
        "sec_h_ft":         sec_h_ft,
        "sec_period_s":     round(sec_period, 1) if sec_period is not None else None,
        "ww_h_ft":          ww_h_ft,
        "wind_kts":         wind_kts,
        "wind_dir":         round(wind_dir_deg, 1),
        "wind_label":       wind_label,
        "water_temp_f":     water_temp_f,
        "primary_period_s": round(primary_period, 1),
        "org_label":        _org_label(s_org),
        "clean_pct":        clean_pct,
    }
