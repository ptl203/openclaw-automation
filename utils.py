import os
import re
import time
import socket
import smtplib
import requests
from datetime import datetime
from email.message import EmailMessage
from dotenv import load_dotenv

load_dotenv(override=True)


def wait_for_network(max_wait=120, host="one.one.one.one", interval=5):
    """Block until DNS resolution works, up to *max_wait* seconds.

    Jobs fire moments after pmset wakes the Mac, before Wi-Fi has
    reassociated — the logs show 5:00 AM NameResolutionError clusters.
    Returns True once the network is up, False if it never came up.
    """
    deadline = time.time() + max_wait
    waited = False
    while True:
        try:
            socket.getaddrinfo(host, 443)
            if waited:
                log_event("Network is up — continuing.")
            return True
        except OSError:
            if not waited:
                log_event(f"Network not ready (DNS failing) — waiting up to {max_wait}s...")
                waited = True
            if time.time() >= deadline:
                log_event(f"Network still down after {max_wait}s — proceeding anyway.")
                return False
            time.sleep(interval)

STORMGLASS_TIMEOUT = 20  # seconds per attempt
STORMGLASS_MAX_RETRIES = 3
STORMGLASS_RETRY_BACKOFF = 5  # seconds, doubled each retry


def _stormglass_get(url, timeout=STORMGLASS_TIMEOUT,
                    max_retries=STORMGLASS_MAX_RETRIES,
                    backoff=STORMGLASS_RETRY_BACKOFF):
    """GET a Stormglass endpoint with timeout and retry/backoff.

    Retries on connection/timeout errors and 5xx responses; fails fast on 4xx
    (bad key/params, or 402 quota exhausted). Re-raises the last exception if
    all attempts fail.
    """
    api_key = os.getenv("STORMGLASS_API_KEY")
    if not api_key:
        raise ValueError("STORMGLASS_API_KEY not set")

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


def fetch_stormglass(lat, lng, params, **kwargs):
    """Fetch a Stormglass weather/point forecast (hourly wave/wind data)."""
    url = f"https://api.stormglass.io/v2/weather/point?lat={lat}&lng={lng}&params={params}"
    return _stormglass_get(url, **kwargs)


def fetch_tide_extremes(lat, lng, **kwargs):
    """Fetch Stormglass tide extremes (highs/lows) from 12h back to 36h ahead.

    The window must include the extreme *before* now so the current tide phase
    can be interpolated, and enough ahead for a next-24h outlook.
    """
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    # Plain UTC timestamps — a "+00:00" offset decodes as a space in the query
    # string and Stormglass rejects it with 422.
    start = (now - timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%S")
    end = (now + timedelta(hours=36)).strftime("%Y-%m-%dT%H:%M:%S")
    url = f"https://api.stormglass.io/v2/tide/extremes/point?lat={lat}&lng={lng}&start={start}&end={end}"
    return _stormglass_get(url, **kwargs)

_EMAIL_RETRY_DELAY = 30  # seconds before the single retry


def _deliver_email(msg, subject):
    """Send a prepared message via SMTP with one retry; failures hit automation.log."""
    server = os.getenv("SMTP_SERVER")
    port = os.getenv("SMTP_PORT", 587)
    user = os.getenv("EMAIL_ADDRESS")
    pwd = os.getenv("EMAIL_PASSWORD")

    for attempt in (1, 2):
        try:
            s = smtplib.SMTP(server, int(port))
            s.starttls()
            s.login(user, pwd)
            s.send_message(msg)
            s.quit()
            print(f"Sent Email: {subject}")
            return True
        except Exception as e:
            log_event(f"Email send attempt {attempt}/2 failed for '{subject}': {e}")
            if attempt == 1:
                time.sleep(_EMAIL_RETRY_DELAY)
    print(f"Email failed for {subject}")
    return False


def _email_config_ok(subject):
    if all([os.getenv("SMTP_SERVER"), os.getenv("EMAIL_ADDRESS"),
            os.getenv("EMAIL_PASSWORD"), os.getenv("TO_EMAIL")]):
        return True
    log_event(f"Email configuration missing for: {subject}")
    print(f"Email configuration missing for: {subject}")
    return False


def send_email(subject, message):
    if not _email_config_ok(subject):
        return
    msg = EmailMessage()
    msg.set_content(message)
    msg['Subject'] = f"[LobsterClaw] {subject}"
    msg['From'] = os.getenv("EMAIL_ADDRESS")
    msg['To'] = os.getenv("TO_EMAIL")
    _deliver_email(msg, subject)


def send_html_email(subject, html_body):
    if not _email_config_ok(subject):
        return
    msg = EmailMessage()
    msg['Subject'] = f"[LobsterClaw] {subject}"
    msg['From'] = os.getenv("EMAIL_ADDRESS")
    msg['To'] = os.getenv("TO_EMAIL")
    msg.set_content("This email requires an HTML-capable email client.")
    msg.add_alternative(html_body, subtype='html')
    _deliver_email(msg, subject)

# Query params / header values that must never reach automation.log. Error
# messages from requests embed full URLs, which previously leaked API keys.
_SECRET_PATTERN = re.compile(
    r"(?i)\b((?:api_?key|apikey|application_key|access_token|auth_token|authorization|token|key)=)[^&\s\"']+"
)


def redact_secrets(text):
    return _SECRET_PATTERN.sub(r"\1REDACTED", str(text))


def log_event(message):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    log_path = os.path.join(base_dir, "automation.log")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_path, "a") as f:
        f.write(f"[{timestamp}] {redact_secrets(message)}\n")

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


def filter_nearby_hours(raw_data, back_hours=1, forward_hours=4):
    """Keep hours in the session window: slightly behind now through the hours you'd actually surf."""
    from datetime import datetime, timezone
    now_utc = datetime.now(timezone.utc)
    filtered = []
    for h in raw_data.get("hours", []):
        t = datetime.fromisoformat(h["time"])
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        delta_h = (t - now_utc).total_seconds() / 3600
        if -back_hours <= delta_h <= forward_hours:
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


# ── Swell exposure fit ─────────────────────────────────────────────────────

# Factor applied to swell directions outside every exposure arc for a beach.
_EXPOSURE_DEFAULT_FACTOR = 0.2
# Neutral score when a beach has no exposure arcs or the swell has no direction data.
_NEUTRAL_SWELL_FIT = 0.7


def _exposure_factor(direction, arcs):
    """Return the largest arc factor whose arc contains *direction*."""
    if direction is None:
        return None
    best = None
    for arc in arcs or []:
        if _in_arc(direction % 360, arc["from"], arc["to"]):
            f = arc.get("factor", 1.0)
            best = f if best is None else max(best, f)
    return best if best is not None else _EXPOSURE_DEFAULT_FACTOR


def swell_fit_score(partitions, arcs):
    """Energy-weighted exposure fit for swell partitions.

    *partitions* is a list of (height_ft, direction_deg) — direction may be
    None (e.g. Stormglass gives no secondary swell direction), which scores
    neutral. Energy weighting uses height² so the dominant swell dominates.
    """
    if not arcs:
        return _NEUTRAL_SWELL_FIT

    total_energy = 0.0
    weighted = 0.0
    for height_ft, direction in partitions:
        if not height_ft or height_ft <= 0:
            continue
        energy = height_ft * height_ft
        factor = _exposure_factor(direction, arcs)
        if factor is None:
            factor = _NEUTRAL_SWELL_FIT
        total_energy += energy
        weighted += energy * factor

    if total_energy < 0.01:
        return _NEUTRAL_SWELL_FIT
    return round(weighted / total_energy, 2)


# ── Tide ───────────────────────────────────────────────────────────────────

# Neutral tide score when tide data or a beach preference is unavailable.
_NEUTRAL_TIDE_FIT = 0.65


def tide_state(extremes_data, at_time):
    """Interpolate the tide at *at_time* (aware datetime) from Stormglass extremes.

    Returns dict(height_ft, phase, direction, next_type, next_time, next_height_ft)
    where phase runs 0.0 (dead low) → 1.0 (dead high), or None if *at_time*
    isn't bracketed by two extremes. Uses sinusoidal interpolation, which is
    how real tides move between extremes.
    """
    import math
    from datetime import datetime, timezone

    events = []
    for e in extremes_data.get("data", []):
        try:
            t = datetime.fromisoformat(e["time"].replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            events.append((t, float(e["height"]), e.get("type", "")))
        except Exception:
            continue
    events.sort(key=lambda x: x[0])

    prev = next_e = None
    for ev in events:
        if ev[0] <= at_time:
            prev = ev
        elif next_e is None:
            next_e = ev
            break
    if prev is None or next_e is None:
        return None

    (t1, h1, type1), (t2, h2, type2) = prev, next_e
    x = (at_time - t1).total_seconds() / max(1.0, (t2 - t1).total_seconds())
    height_m = h1 + (h2 - h1) * (1 - math.cos(math.pi * x)) / 2

    rising = h2 > h1
    phase = x if rising else 1 - x

    return {
        "height_ft":      round(m_to_ft(height_m), 1),
        "phase":          round(phase, 2),
        "direction":      "rising" if rising else "falling",
        "next_type":      type2 or ("high" if rising else "low"),
        "next_time":      t2,
        "next_height_ft": round(m_to_ft(h2), 1),
    }


def tide_fit_score(phase, pref_range):
    """1.0 inside the beach's preferred phase window, linear falloff to 0.4 outside."""
    if phase is None or not pref_range:
        return _NEUTRAL_TIDE_FIT
    lo, hi = pref_range
    if lo <= phase <= hi:
        return 1.0
    dist = (lo - phase) if phase < lo else (phase - hi)
    return round(max(0.4, 1.0 - (dist / 0.3) * 0.6), 2)


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
_W_SIZE   = 0.18
_W_WIND   = 0.34
_W_PERIOD = 0.14
_W_ORG    = 0.09
_W_SWELL  = 0.15   # swell-direction fit vs. per-beach exposure arcs
_W_TIDE   = 0.10   # tide-phase fit vs. per-beach preference

# Raw score (0–100) → stars (evaluated highest-threshold first)
_STAR_MAP = [(90, 5), (75, 4), (55, 3), (38, 2), (0, 1)]

# Minimum raw score for a GO recommendation (≥ ★★★★ — a notably good day)
GO_THRESHOLD = 75


def score_conditions(hour, facing_dir, swell_exposure=None, tide_extremes=None,
                     tide_pref=None):
    """Score a single Stormglass hourly data point for a given beach.

    *swell_exposure* is the beach's exposure arc list from beaches.json;
    *tide_extremes* the fetch_tide_extremes() response (shared across
    beaches); *tide_pref* the beach's preferred tide-phase window.
    Missing data scores neutral, so beaches stay comparable.

    Returns a dict with all converted values and scores, ready for use in
    the email body and the Gemini verdict prompt.
    """
    from datetime import datetime, timezone
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

    # Swell partitions with directions (Stormglass has no secondary direction;
    # wind swell arrives with the wind, so use wind direction reversed to "toward").
    partitions = [(swell_h_ft, swell_dir)]
    if sec_h_ft:
        partitions.append((sec_h_ft, None))
    if ww_h_ft:
        partitions.append((ww_h_ft, (wind_dir_deg + 180) % 360))
    s_swell = swell_fit_score(partitions, swell_exposure)

    tide = None
    if tide_extremes:
        try:
            hour_time = datetime.fromisoformat(hour["time"])
            if hour_time.tzinfo is None:
                hour_time = hour_time.replace(tzinfo=timezone.utc)
            tide = tide_state(tide_extremes, hour_time)
        except Exception:
            tide = None
    s_tide = tide_fit_score(tide["phase"] if tide else None, tide_pref)

    total = round(
        (_W_SIZE * s_size + _W_WIND * s_wind + _W_PERIOD * s_period
         + _W_ORG * s_org + _W_SWELL * s_swell + _W_TIDE * s_tide) * 100
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
            "swell_fit": round(s_swell, 2),
            "tide":   round(s_tide, 2),
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
        "hour_time":        hour.get("time"),
        "tide":             tide,
    }
