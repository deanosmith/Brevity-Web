import os
import ssl
import sys
import re
import html
import hashlib
import json
import logging
import math
import time
import requests
import calendar
from urllib.parse import quote
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import yfinance as yf
import feedparser
from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup
from slack_sdk import WebClient

# ==============================================================================
# CONFIGURATION
# ==============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

load_dotenv()

XAI_API_KEY = os.getenv("XAI_API_KEY")
# Ignore blank overrides from empty GitHub Actions variables.
XAI_MODEL = (os.getenv("XAI_MODEL") or "grok-4.20-non-reasoning").strip() or "grok-4.20-non-reasoning"
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID")
# Slack delivery is optional; the primary product is the GitHub Pages site.
SEND_TO_SLACK = os.getenv("SEND_TO_SLACK", "").strip().lower() in {"1", "true", "yes", "on"}
GENERATE_PDF = os.getenv("GENERATE_PDF", "1").strip().lower() not in {"0", "false", "no", "off"}

# Ensure WeasyPrint can locate system libraries on macOS.
if sys.platform == "darwin":
    os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = (
        "/opt/homebrew/lib:" + os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    )

# Allow HTTPS requests in environments with legacy SSL setups.
if hasattr(ssl, "_create_unverified_context"):
    ssl._create_default_https_context = ssl._create_unverified_context


# ==============================================================================
# CONSTANTS & MAPPINGS
# ==============================================================================

WEATHER_COLORS = {
    0: "#FFD700",  # Sun / Clear (Gold)
    1: "#87CEEB",
    2: "#87CEEB",
    3: "#87CEEB",  # Partly Cloudy (Sky Blue)
    45: "#708090",
    48: "#708090",  # Fog (Slate Gray)
    51: "#4682B4",
    53: "#4682B4",
    55: "#4682B4",  # Drizzle (Steel Blue)
    61: "#4682B4",
    63: "#4682B4",
    65: "#4682B4",  # Rain
    80: "#4682B4",
    81: "#4682B4",
    82: "#4682B4",  # Showers
    71: "#E0FFFF",
    73: "#E0FFFF",
    75: "#E0FFFF",
    77: "#E0FFFF",  # Snow (Light Cyan)
    95: "#9370DB",
    96: "#9370DB",
    99: "#9370DB",  # Thunderstorm (Medium Purple)
}

WEATHER_TEXT = {
    0: "Clear Sky",
    1: "Partly Cloudy",
    2: "Partly Cloudy",
    3: "Overcast",
    45: "Foggy",
    48: "Rime Fog",
    51: "Light Drizzle",
    53: "Drizzle",
    55: "Heavy Drizzle",
    61: "Light Rain",
    63: "Rain",
    65: "Heavy Rain",
    80: "Showers",
    81: "Showers",
    82: "Showers",
    71: "Light Snow",
    73: "Snow",
    75: "Heavy Snow",
    77: "Snow Grains",
    95: "Thunderstorm",
    96: "Thunderstorm",
    99: "Thunderstorm",
}

WEATHER_ICONS = {
    0: "☀️",  # Clear
    1: "⛅️",
    2: "⛅️",
    3: "☁️",  # Cloudy
    45: "🌫️",
    48: "🌫️",  # Fog
    51: "🌦️",
    53: "🌦️",
    55: "🌧️",  # Drizzle
    61: "🌧️",
    63: "🌧️",
    65: "🌧️",  # Rain
    80: "🌦️",
    81: "🌦️",
    82: "🌦️",  # Showers
    71: "🌨️",
    73: "🌨️",
    75: "🌨️",
    77: "🌨️",  # Snow
    95: "⛈️",
    96: "⛈️",
    99: "⛈️",  # Thunderstorm
}

# General market watchlist. Prices only — no anchored portfolio baselines.
# Display name -> Yahoo ticker.
STOCK_TICKERS = {
    "Tesla": "TSLA",
    "SPCX": "SPCX",
    "Nvidia": "NVDA",
    "Oklo": "OKLO",
    "Micron": "MU",
    "Palantir": "PLTR",
}

# Copenhagen local sources with fallbacks. World news intentionally removed.
COPENHAGEN_FEEDS = [
    "https://cphpost.dk/feed/",
    "https://www.cphpost.dk/feed/",
    "https://cphpost.dk/category/news/feed/",
    "https://www.thelocal.dk/feeds/rss.php",
]
SPACE_FEEDS = [
    "https://spacenews.com/feed/",
]

COPENHAGEN_TZ = ZoneInfo("Europe/Copenhagen")
# Next notable eclipses after the Aug 2026 events. Used by Sky Watch.
UPCOMING_ECLIPSES = [
    {
        "when": datetime(2026, 8, 28, 4, 13, tzinfo=timezone.utc),
        "name": "Partial Lunar Eclipse",
        "detail": "Visible from Europe",
        "link": "https://science.nasa.gov/eclipses/",
    },
    {
        "when": datetime(2027, 2, 6, 16, 0, tzinfo=timezone.utc),
        "name": "Annular Solar Eclipse",
        "detail": "South America and Africa",
        "link": "https://science.nasa.gov/eclipses/future-eclipses/eclipse-2027/",
    },
    {
        "when": datetime(2027, 8, 2, 10, 7, tzinfo=timezone.utc),
        "name": "Total Solar Eclipse",
        "detail": "Spain and North Africa",
        "link": "https://science.nasa.gov/eclipses/future-eclipses/eclipse-2027/",
    },
    {
        "when": datetime(2028, 1, 26, 15, 8, tzinfo=timezone.utc),
        "name": "Annular Solar Eclipse",
        "detail": "Americas and western Europe",
        "link": "https://science.nasa.gov/eclipses/",
    },
    {
        "when": datetime(2028, 7, 22, 2, 56, tzinfo=timezone.utc),
        "name": "Total Solar Eclipse",
        "detail": "Australia and New Zealand",
        "link": "https://science.nasa.gov/eclipses/",
    },
]

TRENDING_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")
KEYWORDS_RE = re.compile(r"^\s*\[(?P<keywords>[^\]]+)\]\s*")

KEYWORD_COLOR_PALETTE = [
    ("#294f3a", "#3b6b4c", "#d9f4e2"),
    ("#2f3f5c", "#3f567a", "#d9e6ff"),
    ("#5a3a2e", "#7a4f3e", "#ffe1d3"),
    ("#4a375f", "#5f4c78", "#f0e3ff"),
    ("#3b545a", "#4f6f77", "#e0f3f6"),
    ("#5a5a2e", "#78783e", "#fff6c9"),
]

PLASMA_STOPS = (
    (0.0, "#0d0887"),
    (0.25, "#6a00a8"),
    (0.5, "#b12a90"),
    (0.75, "#e16462"),
    (1.0, "#f0f921"),
)

SITE_HTML_PATH = "index.html"
PDF_PATH = "brevity.pdf"
DEFAULT_HEADERS = {"User-Agent": "Brevity/1.0 (+https://deanosmith.github.io/Brevity-Web/)"}
REQUEST_TIMEOUT = 30
AI_TIMEOUT = 60
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 1.2
RETRY_STATUS_CODES = (429, 500, 502, 503, 504)
HTML_TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")

# Open-Meteo source, matching Brevity-Wallpaper's weather provider.
WEATHER_LAT = 55.6761
WEATHER_LON = 12.5683
WEATHER_TIMEZONE = "Europe/Copenhagen"
# Wind dial full-scale in km/h. 24 km/h should read as very strong.
WIND_DIAL_MAX_KMH = 50
WEATHER_SOURCE = {
    "name": "Open-Meteo",
    "url": "https://open-meteo.com/",
    "location": "Copenhagen",
}


# ==============================================================================
# HELPERS
# ==============================================================================


def get_weather_color(code):
    """Return a hex color for WMO weather codes."""
    return WEATHER_COLORS.get(code, "#AAAAAA")


def get_weather_text(code):
    """Return description text for WMO weather codes."""
    return WEATHER_TEXT.get(code, "Unknown")


def get_weather_icon(code):
    """Return an icon for WMO weather codes."""
    return WEATHER_ICONS.get(code, "?")


def _hex_to_rgb(value):
    value = value.lstrip("#")
    if len(value) != 6:
        return (0, 0, 0)
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def _rgb_to_hex(rgb):
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def plasma_color(value, vmin=0.0, vmax=40.0):
    """Map a numeric value onto the Plasma colormap."""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = vmin
    if vmax == vmin:
        return PLASMA_STOPS[-1][1]
    t = (numeric - vmin) / (vmax - vmin)
    t = max(0.0, min(1.0, t))
    for idx in range(len(PLASMA_STOPS) - 1):
        left_t, left_color = PLASMA_STOPS[idx]
        right_t, right_color = PLASMA_STOPS[idx + 1]
        if t <= right_t:
            if right_t == left_t:
                return right_color
            local = (t - left_t) / (right_t - left_t)
            r0, g0, b0 = _hex_to_rgb(left_color)
            r1, g1, b1 = _hex_to_rgb(right_color)
            r = int(round(r0 + (r1 - r0) * local))
            g = int(round(g0 + (g1 - g0) * local))
            b = int(round(b0 + (b1 - b0) * local))
            return _rgb_to_hex((r, g, b))
    return PLASMA_STOPS[-1][1]




def clamp01(value):
    """Clamp a numeric value into the 0..1 range."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


def temperature_color(celsius):
    """Map temperature onto a cool-blue to hot-red palette over -5C..30C."""
    # -5C (cool blue) through mild to 30C (hot red).
    progress = clamp01(((safe_number(celsius, 10) or 10) + 5) / 35.0)
    stops = [
        (0.0, (64, 148, 255)),   # cool blue
        (0.28, (90, 200, 255)),  # light blue
        (0.5, (255, 214, 102)),  # mild gold
        (0.75, (255, 140, 66)),  # warm orange
        (1.0, (255, 69, 58)),    # hot red
    ]
    for index in range(len(stops) - 1):
        left_t, left_rgb = stops[index]
        right_t, right_rgb = stops[index + 1]
        if progress <= right_t:
            local = 0 if right_t == left_t else (progress - left_t) / (right_t - left_t)
            rgb = tuple(int(round(a + (b - a) * local)) for a, b in zip(left_rgb, right_rgb))
            return _rgb_to_hex(rgb)
    return _rgb_to_hex(stops[-1][1])


def rain_color(percent):
    """Blue intensity scale for rain probability."""
    progress = clamp01((safe_number(percent, 0) or 0) / 100.0)
    # Deep slate -> vivid rain blue
    return _rgb_to_hex(tuple(
        int(round(a + (b - a) * progress))
        for a, b in zip((55, 78, 110), (64, 196, 255))
    ))


def uv_color(uv_index):
    """UV risk colour scale."""
    progress = clamp01((safe_number(uv_index, 0) or 0) / 11.0)
    stops = [
        (0.0, (76, 175, 80)),
        (0.35, (255, 235, 59)),
        (0.6, (255, 152, 0)),
        (1.0, (244, 67, 54)),
    ]
    for index in range(len(stops) - 1):
        left_t, left_rgb = stops[index]
        right_t, right_rgb = stops[index + 1]
        if progress <= right_t:
            local = 0 if right_t == left_t else (progress - left_t) / (right_t - left_t)
            rgb = tuple(int(round(a + (b - a) * local)) for a, b in zip(left_rgb, right_rgb))
            return _rgb_to_hex(rgb)
    return _rgb_to_hex(stops[-1][1])


def _temp_dial_range(high_v, low_v):
    """Map low/high temps onto a fixed -5C..30C arc for the temp speedo."""
    low_progress = clamp01(((low_v if low_v is not None else 5) + 5) / 35.0)
    high_progress = clamp01(((high_v if high_v is not None else 10) + 5) / 35.0)
    if high_progress < low_progress:
        high_progress = low_progress
    # Keep a tiny visible sliver when the day range is extremely narrow.
    min_width = 0.015
    if high_progress - low_progress < min_width:
        high_progress = min(1.0, low_progress + min_width)
        if high_progress - low_progress < min_width:
            low_progress = max(0.0, high_progress - min_width)
    return {
        "high": None if high_v is None else round(high_v),
        "low": None if low_v is None else round(low_v),
        "high_color": temperature_color(high_v if high_v is not None else 10),
        "low_color": temperature_color(low_v if low_v is not None else 5),
        "high_progress": high_progress,
        "low_progress": low_progress,
        "label": "Temp",
    }


def dial_metrics(rain_chance=None, wind_max=None, uv_max=None, high=None, low=None):
    """Build glanceable dial payloads inspired by Brevity-Wallpaper."""
    rain = safe_number(rain_chance, 0) or 0
    wind = safe_number(wind_max, 0) or 0
    uv = safe_number(uv_max, 0) or 0
    high_v = safe_number(high)
    low_v = safe_number(low)
    return {
        "rain": {
            "progress": clamp01(rain / 100.0),
            "color": rain_color(rain),
            "value": f"{int(round(rain))}%",
            "label": "Rain",
        },
        "wind": {
            "progress": clamp01(wind / float(WIND_DIAL_MAX_KMH)),
            "color": plasma_color(wind),
            "value": f"{int(round(wind))} km/h" if wind_max is not None else "—",
            "label": "Wind",
        },
        "uv": {
            "progress": clamp01(uv / 11.0),
            "color": uv_color(uv),
            "value": f"{uv:.1f}" if uv_max is not None else "—",
            "label": "UV",
        },
        "temp": _temp_dial_range(high_v, low_v),
    }

def keyword_style(keyword):
    """Create a deterministic CSS custom-property string for a keyword badge."""
    digest = hashlib.md5(keyword.lower().encode("utf-8")).hexdigest()
    index = int(digest[:8], 16) % len(KEYWORD_COLOR_PALETTE)
    bg, border, text = KEYWORD_COLOR_PALETTE[index]
    return f"--kw-bg: {bg}; --kw-border: {border}; --kw-text: {text};"


def to_pascal_case(value):
    """Convert a keyword into PascalCase for display."""
    if not value:
        return value
    parts = re.findall(r"[A-Za-z0-9]+", value)
    if not parts:
        return value
    formatted = []
    for part in parts:
        if part.isdigit():
            formatted.append(part)
            continue
        lower = part.lower()
        formatted.append(lower[0].upper() + lower[1:] if lower else "")
    return "".join(formatted)


def stylize_keywords(text):
    """Wrap leading [keywords] in styled span badges."""
    if not text:
        return text
    match = KEYWORDS_RE.match(text)
    if not match:
        return text
    keywords = [kw.strip() for kw in match.group("keywords").split(",") if kw.strip()]
    if not keywords:
        return text
    badges = "".join(
        f'<span class="keyword-badge" style="{keyword_style(kw)}">{html.escape(to_pascal_case(kw))}</span>'
        for kw in keywords
    )
    rest = text[match.end() :].strip()
    rest_html = html.escape(rest)
    rest_html = f'<span class="keyword-text">{rest_html}</span>' if rest_html else ""
    return Markup(f'<span class="keyword-badges">{badges}</span>{rest_html}')


def format_trending_since(raw_since):
    """Normalize trending_since to HH:MM, or return None if invalid."""
    if raw_since is None:
        return None
    if isinstance(raw_since, (int, float)):
        if raw_since <= 0:
            return None
        try:
            return datetime.utcfromtimestamp(raw_since).strftime("%H:%M")
        except Exception:
            return None
    if isinstance(raw_since, str):
        value = raw_since.strip()
        if not value:
            return None
        if value.lower() in {"n/a", "na", "none", "null", "unknown"}:
            return None
        if value.isdigit():
            try:
                ts_value = int(value)
                if ts_value > 1_000_000_000_000:
                    ts_value = ts_value / 1000
                return datetime.utcfromtimestamp(ts_value).strftime("%H:%M")
            except Exception:
                return None
        match = TRENDING_TIME_RE.search(value)
        if match:
            hour = int(match.group(1))
            minute = match.group(2)
            if 0 <= hour <= 23:
                return f"{hour:02d}:{minute}"
        lowered = value.lower()
        if "trend" in lowered or "now" in lowered:
            return "Now"
        if len(value) <= 8:
            return value
    return None


def safe_number(value, default=None):
    """Return a finite number, else default."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if isinstance(number, float) and (math.isnan(number) or math.isinf(number)):
        return default
    return number


def format_clock(value):
    """Extract HH:MM from an ISO timestamp (24-hour intermediate form)."""
    if not value or not isinstance(value, str):
        return None
    if "T" in value:
        return value.split("T", 1)[1][:5]
    if len(value) >= 5 and value[2] == ":":
        return value[:5]
    return value


def format_hour_12(hour, with_minutes=False):
    """Format an hour as 12-hour clock text without am/pm labels."""
    try:
        hour_i = int(hour) % 24
    except (TypeError, ValueError):
        return None
    hour_12 = hour_i % 12
    if hour_12 == 0:
        hour_12 = 12
    if with_minutes:
        return f"{hour_12}:00"
    return str(hour_12)


def format_clock_12(value):
    """Convert HH:MM or an ISO timestamp to 12-hour time without am/pm."""
    if isinstance(value, str) and ("T" in value or (len(value) >= 5 and value[2] == ":")):
        clock = format_clock(value) if "T" in value else value[:5]
    else:
        clock = value
    if not isinstance(clock, str) or ":" not in clock:
        return format_hour_12(clock, with_minutes=False)
    try:
        hour_s, minute_s = clock.split(":", 1)
        hour_i = int(hour_s)
        minute_i = int(minute_s[:2])
    except (TypeError, ValueError):
        return clock
    hour_12 = hour_i % 12
    if hour_12 == 0:
        hour_12 = 12
    return f"{hour_12}:{minute_i:02d}"


def weekday_label(iso_day, today_iso=None):
    """Human day label for forecast cards."""
    if not iso_day:
        return "Day"
    if today_iso and iso_day == today_iso:
        return "Today"
    try:
        parsed = date.fromisoformat(iso_day)
    except ValueError:
        return iso_day
    if today_iso:
        try:
            today = date.fromisoformat(today_iso)
            delta = (parsed - today).days
            if delta == 1:
                return "Tomorrow"
            if delta == 2:
                return "In 2 days"
        except ValueError:
            pass
    return parsed.strftime("%a")


def build_retry_session():
    retry_kwargs = {
        "total": RETRY_ATTEMPTS,
        "connect": RETRY_ATTEMPTS,
        "read": RETRY_ATTEMPTS,
        "backoff_factor": RETRY_BACKOFF,
        "status_forcelist": RETRY_STATUS_CODES,
        "raise_on_status": False,
        "respect_retry_after_header": True,
    }
    try:
        retries = Retry(**retry_kwargs, allowed_methods=frozenset(["GET", "POST"]))
    except TypeError:
        retries = Retry(**retry_kwargs, method_whitelist=frozenset(["GET", "POST"]))
    adapter = HTTPAdapter(max_retries=retries)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


HTTP_SESSION = build_retry_session()


def retry_call(label, func, attempts=RETRY_ATTEMPTS, base_delay=1.0, max_delay=8.0):
    """Retry a callable with exponential backoff and logging."""
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except Exception as exc:
            if attempt < attempts:
                delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
                logger.warning(
                    "%s attempt %s/%s failed: %s; retrying in %.1fs",
                    label,
                    attempt,
                    attempts,
                    exc,
                    delay,
                )
                time.sleep(delay)
            else:
                logger.error("%s failed after %s attempts: %s", label, attempts, exc)
    return None


def strip_html(value):
    """Remove simple HTML tags and collapse whitespace from feed text."""
    if not value:
        return ""
    text = HTML_TAG_RE.sub(" ", str(value))
    text = html.unescape(text)
    return WHITESPACE_RE.sub(" ", text).strip()


def json_safe(value):
    """Convert values so they can be written to JSON."""
    if isinstance(value, Markup):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def extract_search_term(trend_name):
    """
    Build a tighter X search query from a personalized trend title.

    Personalized trends often return long summaries. Prefer hashtags, cashtags,
    tickers, and compact proper-noun phrases for useful search links.
    """
    if not trend_name:
        return ""
    name = strip_html(str(trend_name)).strip()
    if not name:
        return ""

    hashtags = re.findall(r"#[\w']+", name)
    if hashtags:
        return hashtags[0]

    cashtags = re.findall(r"\$[A-Za-z]{1,6}\b", name)
    if cashtags:
        return cashtags[0]

    # Exact short trend names are already searchable.
    words = re.findall(r"[A-Za-z0-9][\w'#.-]*", name)
    if 1 <= len(words) <= 3 and len(name) <= 40:
        return name

    # Quoted key phrases often carry the actual topic.
    quoted = re.findall(r"['\"]([^'\"]{2,48})['\"]", name)
    if quoted:
        candidate = quoted[0].strip()
        if candidate:
            return candidate

    # Leading proper-noun / product phrase: "DeepSeek V4", "Jon Bernthal", etc.
    leading = re.match(
        r"^((?:[A-Z][\w'&.-]+)(?:\s+(?:[A-Z0-9][\w'&.-]*)){0,3})",
        name,
    )
    if leading:
        phrase = leading.group(1).strip(" -,:;")
        # Avoid ultra-generic one-word openers from full sentences.
        weak_starters = {
            "call", "calls", "new", "why", "how", "what", "when", "after",
            "before", "this", "that", "with", "from", "into", "over",
        }
        if phrase and phrase.lower() not in weak_starters and len(phrase) <= 48:
            return phrase

    # Tickers / dense acronyms, but only if they look topic-like.
    caps = re.findall(r"\b[A-Z]{3,}(?:\d+)?\b", name)
    stop = {"THE", "AND", "FOR", "WITH", "FROM", "THIS", "THAT", "INTO", "OVER", "AFTER", "ARE", "WAS"}
    caps = [token for token in caps if token not in stop]
    if caps:
        return caps[0]

    # Fallback: first few content words.
    if not words:
        return name
    short = " ".join(words[:4]).strip(" -,:;")
    return short or name

def x_search_link(term):
    """Create an X search URL for a term or hashtag."""
    query = (term or "").strip()
    if not query:
        return "https://x.com/explore"
    return f"https://x.com/search?q={quote(query)}&src=typed_query"


def fetch_json(url, params=None, extra_headers=None, label="JSON fetch"):
    """GET JSON with the shared retry session."""
    headers = dict(DEFAULT_HEADERS)
    if extra_headers:
        headers.update(extra_headers)

    def _request():
        response = HTTP_SESSION.get(
            url, params=params, timeout=REQUEST_TIMEOUT, headers=headers
        )
        response.raise_for_status()
        return response.json()

    return retry_call(label, _request)


def copenhagen_now():
    """Current time in Copenhagen."""
    return datetime.now(COPENHAGEN_TZ)


def as_utc(moment):
    """Normalize a datetime or ISO stamp to timezone-aware UTC."""
    if moment is None:
        return None
    if isinstance(moment, str):
        text = moment.strip()
        if not text:
            return None
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(moment, datetime):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def tonight_copenhagen(hour=21):
    """This evening in Copenhagen, used as the moon-viewing sort time."""
    now = copenhagen_now()
    evening = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if evening < now:
        return now
    return evening


def relative_local_label(moment):
    """Compact Copenhagen-local label, matching the brief's 12-hour clocks."""
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    local = moment.astimezone(COPENHAGEN_TZ)
    today = copenhagen_now().date()
    clock = format_clock_12(local.strftime("%H:%M"))
    if local.date() == today:
        return f"Today {clock}"
    if local.date() == today + timedelta(days=1):
        return f"Tomorrow {clock}"
    return f"{local.strftime('%a')} {clock}"


def extract_json_object(text):
    """Parse a JSON object from model output, ignoring markdown fences."""
    if not text or not isinstance(text, str):
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.S)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None


def responses_output_text(payload):
    """Flatten xAI Responses API output into a single string."""
    if not isinstance(payload, dict):
        return ""
    chunks = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") and item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if isinstance(part, dict) and part.get("text"):
                chunks.append(str(part["text"]))
            elif isinstance(part, str):
                chunks.append(part)
    return "\n".join(chunks).strip()


# ==============================================================================
# DATA FETCHING
# ==============================================================================


def _peak_rain_time(times, values, day_iso):
    """Return HH:MM for the highest precip probability on a given day."""
    if not times or not values or not day_iso:
        return None
    peak_index = -1
    peak_value = -1
    for index, (stamp, value) in enumerate(zip(times, values)):
        if not isinstance(stamp, str) or not stamp.startswith(day_iso):
            continue
        number = safe_number(value)
        if number is None:
            continue
        if number > peak_value:
            peak_value = number
            peak_index = index
    if peak_index < 0 or peak_value <= 0:
        return None
    return format_clock_12(times[peak_index])


def _hourly_rain_points(times, values, day_iso, start_h=0, end_h=24):
    """Hourly precip probability points for a day window, used by rain timelines."""
    if not times or not values or not day_iso:
        return []

    by_hour = {}
    for stamp, value in zip(times, values):
        if not isinstance(stamp, str) or not stamp.startswith(day_iso) or len(stamp) < 13:
            continue
        try:
            hour = int(stamp[11:13])
        except (TypeError, ValueError):
            continue
        if hour < start_h or hour >= end_h:
            continue
        number = safe_number(value)
        if number is None:
            continue
        by_hour[hour] = round(number)

    points = []
    # Sparse 12-hour labels (no am/pm) keep the timeline readable.
    label_hours = {start_h, 9, 12, 15, 18, 21, end_h - 1}
    for hour in range(start_h, end_h):
        precip = by_hour.get(hour, 0)
        label = format_hour_12(hour, with_minutes=False) if hour in label_hours else None
        points.append(
            {
                "hour": hour,
                "precip": precip,
                "label": label,
            }
        )
    return points


def _rain_timeline(times, values, day_iso, start_h=0, end_h=24, rain_color_value=None):
    """Build a compact rain-peak timeline payload for template rendering."""
    points = _hourly_rain_points(times, values, day_iso, start_h=start_h, end_h=end_h)
    if not points:
        return {
            "points": [],
            "max_precip": 0,
            "peak_time": None,
            "color": rain_color(rain_color_value if rain_color_value is not None else 0),
        }

    max_precip = max((point.get("precip") or 0) for point in points)
    peak_time = None
    if max_precip > 0:
        peak_point = max(points, key=lambda point: point.get("precip") or 0)
        peak_time = format_hour_12(peak_point.get("hour") or 0, with_minutes=True)

    color_source = rain_color_value if rain_color_value is not None else max_precip
    return {
        "points": points,
        "max_precip": max_precip,
        "peak_time": peak_time,
        "color": rain_color(color_source),
    }


def _segment_stats(hourly, start_h, end_h):
    """Average/max stats for a same-day hour window."""

    def slice_list(values):
        return values[start_h:end_h] if isinstance(values, list) else []

    def clean(values):
        return [value for value in values if isinstance(value, (int, float))]

    temps = clean(slice_list(hourly.get("temperature_2m")))
    feels = clean(slice_list(hourly.get("apparent_temperature")))
    precips = clean(slice_list(hourly.get("precipitation_probability")))
    winds = clean(slice_list(hourly.get("wind_speed_10m")))
    wind_dirs = clean(slice_list(hourly.get("wind_direction_10m")))
    codes = slice_list(hourly.get("weather_code"))
    codes = [code for code in codes if isinstance(code, (int, float))]

    avg_temp = sum(temps) / len(temps) if temps else None
    avg_feels = sum(feels) / len(feels) if feels else None
    max_precip = max(precips) if precips else 0
    max_wind = max(winds) if winds else None
    avg_wind_dir = sum(wind_dirs) / len(wind_dirs) if wind_dirs else None
    code = int(max(codes, key=codes.count)) if codes else 0

    return {
        "temp": None if avg_temp is None else round(avg_temp),
        "feels_like": None if avg_feels is None else round(avg_feels),
        "precip": round(max_precip),
        "wind": None if max_wind is None else round(max_wind),
        "wind_dir": None if avg_wind_dir is None else round(avg_wind_dir),
        "wind_color": plasma_color(max_wind or 0),
        "color": get_weather_color(code),
        "condition": get_weather_text(code),
        "icon": get_weather_icon(code),
        "code": code,
    }


def _percent_from_closes(closes, sessions_back):
    """Percent change from N trading sessions ago to the latest close."""
    if not closes:
        return None
    current = closes[-1]
    if sessions_back <= 0:
        return 0.0
    if len(closes) <= sessions_back:
        past = closes[0]
    else:
        past = closes[-(sessions_back + 1)]
    if past in (None, 0) or current is None:
        return None
    return ((current - past) / past) * 100


def _change_style(percent):
    """Map a percent change to the stock colour/arrow pair used by the template."""
    if percent is None:
        return "grey", "-"
    if percent >= 0:
        return "green", "↑"
    return "red", "↓"


def fetch_weather():
    """
    Fetch Copenhagen weather from Open-Meteo.

    Source strategy matches Brevity-Wallpaper: Open-Meteo daily + hourly
    precipitation probabilities, including expected peak rain time.
    """
    logger.info("Fetching weather from Open-Meteo...")
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": WEATHER_LAT,
        "longitude": WEATHER_LON,
        "forecast_days": 3,
        "timezone": WEATHER_TIMEZONE,
        "temperature_unit": "celsius",
        "wind_speed_unit": "kmh",
        "current": [
            "temperature_2m",
            "apparent_temperature",
            "weather_code",
            "precipitation_probability",
            "wind_speed_10m",
            "wind_direction_10m",
        ],
        "hourly": [
            "temperature_2m",
            "apparent_temperature",
            "precipitation_probability",
            "wind_speed_10m",
            "wind_direction_10m",
            "weather_code",
        ],
        "daily": [
            "weather_code",
            "temperature_2m_max",
            "temperature_2m_min",
            "sunrise",
            "sunset",
            "uv_index_max",
            "precipitation_probability_max",
            "precipitation_sum",
            "wind_speed_10m_max",
            "wind_direction_10m_dominant",
        ],
    }

    def _request():
        response = HTTP_SESSION.get(
            url, params=params, timeout=REQUEST_TIMEOUT, headers=DEFAULT_HEADERS
        )
        response.raise_for_status()
        return response.json()

    data = retry_call("Weather fetch", _request)
    if not data:
        return None

    try:
        hourly = data.get("hourly", {}) or {}
        daily = data.get("daily", {}) or {}
        current = data.get("current", {}) or {}

        days = daily.get("time") or []
        if not days:
            logger.warning("Incomplete weather payload; skipping weather section")
            return None

        today_iso = days[0]
        today_code = int(safe_number((daily.get("weather_code") or [None])[0], 0) or 0)
        current_code = int(safe_number(current.get("weather_code"), today_code) or today_code)

        hourly_times = hourly.get("time") or []
        hourly_precip = hourly.get("precipitation_probability") or []
        today_rain_chance = round(safe_number((daily.get("precipitation_probability_max") or [0])[0], 0) or 0)
        # Daytime window used by the continuous morning/afternoon/evening strip.
        today_rain_timeline = _rain_timeline(
            hourly_times,
            hourly_precip,
            today_iso,
            start_h=6,
            end_h=24,
            rain_color_value=today_rain_chance,
        )
        today = {
            "date": today_iso,
            "label": "Today",
            "high": None if safe_number((daily.get("temperature_2m_max") or [None])[0]) is None else round(safe_number((daily.get("temperature_2m_max") or [None])[0])),
            "low": None if safe_number((daily.get("temperature_2m_min") or [None])[0]) is None else round(safe_number((daily.get("temperature_2m_min") or [None])[0])),
            "rain_chance": today_rain_chance,
            "rain_sum_mm": safe_number((daily.get("precipitation_sum") or [None])[0]),
            "rain_peak_time": today_rain_timeline.get("peak_time") or _peak_rain_time(
                hourly_times,
                hourly_precip,
                today_iso,
            ),
            "rain_timeline": today_rain_timeline,
            "wind_max": None if safe_number((daily.get("wind_speed_10m_max") or [None])[0]) is None else round(safe_number((daily.get("wind_speed_10m_max") or [None])[0])),
            "wind_dir": None if safe_number((daily.get("wind_direction_10m_dominant") or [None])[0]) is None else round(safe_number((daily.get("wind_direction_10m_dominant") or [None])[0])),
            "wind_color": plasma_color(safe_number((daily.get("wind_speed_10m_max") or [0])[0], 0) or 0),
            "uv_max": safe_number((daily.get("uv_index_max") or [None])[0]),
            "sunrise": format_clock((daily.get("sunrise") or [None])[0]),
            "sunset": format_clock((daily.get("sunset") or [None])[0]),
            "code": today_code,
            "condition": get_weather_text(today_code),
            "icon": get_weather_icon(today_code),
            "color": get_weather_color(today_code),
            "current": {
                "temp": None if safe_number(current.get("temperature_2m")) is None else round(safe_number(current.get("temperature_2m"))),
                "feels_like": None if safe_number(current.get("apparent_temperature")) is None else round(safe_number(current.get("apparent_temperature"))),
                "code": current_code,
                "condition": get_weather_text(current_code),
                "icon": get_weather_icon(current_code),
                "color": get_weather_color(current_code),
                "wind": None if safe_number(current.get("wind_speed_10m")) is None else round(safe_number(current.get("wind_speed_10m"))),
                "wind_dir": None if safe_number(current.get("wind_direction_10m")) is None else round(safe_number(current.get("wind_direction_10m"))),
                "precip": round(safe_number(current.get("precipitation_probability"), 0) or 0),
            },
            # Keep period breakdown for richer "today" detail.
            "morning": _segment_stats(hourly, 6, 12),
            "afternoon": _segment_stats(hourly, 12, 18),
            "evening": _segment_stats(hourly, 18, 24),
        }
        today["dials"] = dial_metrics(
            rain_chance=today.get("rain_chance"),
            wind_max=today.get("wind_max"),
            uv_max=today.get("uv_max"),
            high=today.get("high"),
            low=today.get("low"),
        )
        # Colour accents used by glanceable cards.
        today["high_color"] = today["dials"]["temp"]["high_color"]
        today["low_color"] = today["dials"]["temp"]["low_color"]
        today["rain_color"] = today["dials"]["rain"]["color"]

        upcoming = []
        for index in range(1, min(3, len(days))):
            code = int(safe_number((daily.get("weather_code") or [None])[index], 0) or 0)
            day_iso = days[index]
            high_v = safe_number((daily.get("temperature_2m_max") or [None])[index])
            low_v = safe_number((daily.get("temperature_2m_min") or [None])[index])
            rain_v = safe_number((daily.get("precipitation_probability_max") or [0])[index], 0) or 0
            wind_v = safe_number((daily.get("wind_speed_10m_max") or [None])[index])
            day_rain_timeline = _rain_timeline(
                hourly_times,
                hourly_precip,
                day_iso,
                start_h=0,
                end_h=24,
                rain_color_value=rain_v,
            )
            upcoming.append(
                {
                    "date": day_iso,
                    "label": weekday_label(day_iso, today_iso),
                    "high": None if high_v is None else round(high_v),
                    "low": None if low_v is None else round(low_v),
                    "rain_chance": round(rain_v),
                    "wind_max": None if wind_v is None else round(wind_v),
                    "code": code,
                    "condition": get_weather_text(code),
                    "icon": get_weather_icon(code),
                    "color": get_weather_color(code),
                    "high_color": temperature_color(high_v if high_v is not None else 10),
                    "low_color": temperature_color(low_v if low_v is not None else 5),
                    "rain_color": rain_color(rain_v),
                    "rain_progress": clamp01(rain_v / 100.0),
                    "rain_timeline": day_rain_timeline,
                    "rain_peak_time": day_rain_timeline.get("peak_time"),
                }
            )

        return {
            "location": WEATHER_SOURCE["location"],
            "timezone": data.get("timezone") or WEATHER_TIMEZONE,
            "source": WEATHER_SOURCE,
            "today": today,
            "upcoming": upcoming,
            # Backward-compatible aliases used by older templates/PDF styles.
            "morning": today["morning"],
            "afternoon": today["afternoon"],
            "evening": today["evening"],
            "sunrise": (daily.get("sunrise") or [""])[0],
            "sunset": (daily.get("sunset") or [""])[0],
            "daily_precip": today["rain_chance"],
            "rain_peak_time": today["rain_peak_time"],
        }
    except Exception as exc:
        logger.error("Error parsing weather data: %s", exc)
        return None


def fetch_stocks():
    """Fetch general stock watchlist prices with day, 7-day, and 1-month change."""
    logger.info("Fetching stocks...")
    stock_data = {}

    for name, symbol in STOCK_TICKERS.items():
        def _fetch_history(period="3mo"):
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period=period, auto_adjust=True)
            if hist is None or hist.empty:
                raise ValueError("No price history returned")
            # yfinance can return NaN rows around market close / holidays.
            closes = [safe_number(value) for value in hist["Close"].tolist()]
            closes = [value for value in closes if value is not None]
            if len(closes) < 1:
                raise ValueError("No finite closes returned")
            return closes

        closes = retry_call(f"Stock fetch {name}", lambda: _fetch_history("3mo"))
        if closes is None:
            closes = retry_call(f"Stock fetch {name} (1mo)", lambda: _fetch_history("1mo"), attempts=2)

        empty_row = {
            "symbol": symbol,
            "price": None,
            "change": None,
            "percent": None,
            "color": "grey",
            "arrow": "-",
            "percent_7d": None,
            "color_7d": "grey",
            "arrow_7d": "-",
            "percent_1m": None,
            "color_1m": "grey",
            "arrow_1m": "-",
        }
        if not closes:
            stock_data[name] = empty_row
            continue

        try:
            current_close = closes[-1]
            prev_close = closes[-2] if len(closes) > 1 else current_close
            change = current_close - prev_close
            percent_change = (change / prev_close) * 100 if prev_close else 0.0
            # Approximate calendar windows with trading sessions.
            percent_7d = _percent_from_closes(closes, 5)
            percent_1m = _percent_from_closes(closes, 21)
            color_7d, arrow_7d = _change_style(percent_7d)
            color_1m, arrow_1m = _change_style(percent_1m)
            stock_data[name] = {
                "symbol": symbol,
                "price": current_close,
                "change": change,
                "percent": percent_change,
                "color": "green" if change >= 0 else "red",
                "arrow": "↑" if change >= 0 else "↓",
                "percent_7d": percent_7d,
                "color_7d": color_7d,
                "arrow_7d": arrow_7d,
                "percent_1m": percent_1m,
                "color_1m": color_1m,
                "arrow_1m": arrow_1m,
            }
        except Exception as exc:
            logger.error("Error parsing %s data: %s", name, exc)
            stock_data[name] = empty_row

    percents = [
        data.get("percent")
        for data in stock_data.values()
        if isinstance(data, dict) and isinstance(data.get("percent"), (int, float))
    ]
    stock_data["average_percent"] = sum(percents) / len(percents) if percents else 0.0
    return stock_data


# Shared xAI availability flag so one bad key does not thrash every item.
XAI_AVAILABLE = bool(XAI_API_KEY)


def mark_xai_unavailable(reason):
    """Disable further xAI calls after a hard auth/config failure."""
    global XAI_AVAILABLE
    if XAI_AVAILABLE:
        logger.error("Disabling xAI for this run: %s", reason)
    XAI_AVAILABLE = False


def summarize_with_ai(text, prompt_prefix="Summarize this news item:"):
    """Summarize text using the xAI API."""
    clean_text = strip_html(text)
    if not clean_text:
        return "Content unavailable"
    if not XAI_AVAILABLE:
        return clean_text

    headers = {
        "Authorization": f"Bearer {XAI_API_KEY}",
        "Content-Type": "application/json",
    }
    system_prompt = (
        "You are a helpful news assistant. "
        "Merge the news title and description into one concise sentence (max 22 words). "
        "Start with 2-3 bracketed keywords, e.g. [AI, Nvidia, chips]. "
        "Do not filter anything out. Be specific."
    )
    payload = {
        "model": XAI_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"{prompt_prefix}\n\n{clean_text}"},
        ],
    }

    def _summarize():
        response = HTTP_SESSION.post(
            "https://api.x.ai/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=AI_TIMEOUT,
        )
        if response.status_code >= 400:
            body = (response.text or "")[:300]
            lower = body.lower()
            if response.status_code in {401, 403} or "incorrect api key" in lower or "invalid api key" in lower:
                mark_xai_unavailable(f"xAI {response.status_code}: {body}")
            raise RuntimeError(f"xAI {response.status_code}: {body}")
        content = response.json()["choices"][0]["message"]["content"].strip()
        if not content:
            raise ValueError("Empty summary response")
        return content

    summary = retry_call("AI summarization", _summarize)
    if not summary:
        logger.error("AI summarization failed; falling back to raw text")
        return clean_text
    return summary


def fetch_feed(url):
    def _request():
        response = HTTP_SESSION.get(url, timeout=REQUEST_TIMEOUT, headers=DEFAULT_HEADERS)
        response.raise_for_status()
        return response.content

    content = retry_call(f"RSS fetch {url}", _request)
    if not content:
        return None

    feed = feedparser.parse(content)
    if getattr(feed, "bozo", False):
        logger.warning("Feed parse warning for %s: %s", url, getattr(feed, "bozo_exception", "unknown"))
    return feed


def fetch_rss_feed(url, limit=5, prompt="Summarize this content:", summarize=True):
    """Fetch and optionally summarize items from an RSS feed."""
    news_items = []
    feed = fetch_feed(url)
    if not feed or not getattr(feed, "entries", None):
        return []

    for entry in feed.entries[:limit]:
        try:
            title = strip_html(getattr(entry, "title", "") or "")
            summary = strip_html(getattr(entry, "summary", getattr(entry, "description", "")) or "")
            content_text = f"{title}. {summary}".strip(". ").strip()
            if not content_text:
                continue
            if summarize and XAI_AVAILABLE:
                item_summary = summarize_with_ai(content_text, prompt)
                item_summary = stylize_keywords(item_summary)
            else:
                # Keep the page useful even when AI is unavailable.
                item_summary = title or content_text
            if item_summary:
                news_items.append({
                    "headline": item_summary,
                    "link": entry.get("link", ""),
                    "source": strip_html(getattr(getattr(feed, "feed", None), "title", "") or ""),
                })
        except Exception as exc:
            logger.warning("Error parsing feed entry from %s: %s", url, exc)

    return news_items


def fetch_first_rss(urls, limit=5, prompt="Summarize this content:", label="feed"):
    """Try multiple RSS URLs until one returns items."""
    for url in urls:
        logger.info("Trying %s feed: %s", label, url)
        items = fetch_rss_feed(url, limit=limit, prompt=prompt)
        if items:
            logger.info("%s feed ok via %s (%s items)", label, url, len(items))
            return items
    logger.warning("No items found for %s feeds", label)
    return []


def fetch_space_news():
    """Fetch and summarize space news."""
    logger.info("Fetching space news...")
    return fetch_first_rss(
        SPACE_FEEDS,
        limit=6,
        prompt="Summarize this content:",
        label="space",
    )


def fetch_copenhagen_events():
    """Fetch and summarize Copenhagen events/news with source fallbacks."""
    logger.info("Fetching Copenhagen events...")
    return fetch_first_rss(
        COPENHAGEN_FEEDS,
        limit=6,
        prompt="Summarize this Copenhagen/Denmark news item.",
        label="copenhagen",
    )


def _format_post_count(value):
    if isinstance(value, int):
        return f"{value:,} posts"
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _trend_card(name, post_count=None, category="Personalized", trending_since=None, link=None):
    """One card in the Personalized / Sky Watch lists."""
    raw_name = strip_html(str(name or "")).strip()
    if not raw_name:
        return None
    search_term = extract_search_term(raw_name) or raw_name
    since = format_trending_since(trending_since) if trending_since else None
    if since and since[:1].isdigit():
        since = f"Since {since}"
    return {
        "name": raw_name,
        "search_term": search_term,
        "post_count": _format_post_count(post_count) or "Live",
        "category": category or "Personalized",
        "trending_since": since,
        "link": link or x_search_link(search_term),
        "source": category,
    }


def _collect_trend_cards(raw_items, category, max_items):
    out = []
    seen = set()
    for trend in raw_items or []:
        if not isinstance(trend, dict):
            continue
        item = _trend_card(
            trend.get("trend_name") or trend.get("name") or trend.get("query"),
            post_count=trend.get("post_count", trend.get("tweet_count", trend.get("tweet_volume"))),
            category=trend.get("category") or category,
            trending_since=trend.get("trending_since"),
        )
        if not item:
            continue
        key = item["name"].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= max_items:
            break
    return out


def _fetch_x_personalized_official(limit):
    """
    Official X personalized_trends. Requires X Premium on the user token.

    Returns a list of cards, or None when the endpoint is unavailable so a
    fallback can run. Empty list means a successful but blank payload.
    """
    consumer_key = os.getenv("CONSUMER_KEY")
    consumer_secret = os.getenv("CONSUMER_SECRET")
    access_token = os.getenv("ACCESS_TOKEN")
    access_token_secret = os.getenv("ACCESS_TOKEN_SECRET")
    if not all([consumer_key, consumer_secret, access_token, access_token_secret]):
        logger.info("X personalized API skipped: missing OAuth credentials.")
        return None

    try:
        from requests_oauthlib import OAuth1Session
    except ImportError:
        logger.error("requests_oauthlib not installed - cannot fetch official X trends")
        return None

    oauth = OAuth1Session(
        consumer_key,
        client_secret=consumer_secret,
        resource_owner_key=access_token,
        resource_owner_secret=access_token_secret,
    )
    try:
        response = oauth.get(
            "https://api.x.com/2/users/personalized_trends",
            params={
                "personalized_trend.fields": "category,post_count,trend_name,trending_since",
            },
            timeout=REQUEST_TIMEOUT,
        )
    except Exception as exc:
        logger.warning("X personalized API request failed: %s", exc)
        return None

    body = (response.text or "")[:400]
    if response.status_code == 401 and "premium" in body.lower():
        logger.warning(
            "X personalized trends require Premium; falling back to Grok X search."
        )
        return None
    if response.status_code != 200:
        logger.warning("X personalized API error %s: %s", response.status_code, body)
        return None

    payload = response.json() if response.content else {}
    raw_items = payload.get("data", []) if isinstance(payload, dict) else []
    logger.info("X personalized API returned %s items.", len(raw_items or []))
    return _collect_trend_cards(raw_items, "Personalized", limit)


def _fetch_x_personalized_grok(limit):
    """Live For-You topics from Grok's X search, tuned to this brief's interests."""
    if not XAI_AVAILABLE:
        return []

    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    prompt = (
        f"Using X search, list {limit} topics that are actually moving on X today ({today}). "
        "Choose what would matter to someone in Copenhagen who follows spaceflight and SpaceX, "
        "AI and semiconductors, nuclear energy, Tesla, Palantir, and Denmark or Europe. "
        "Skip celebrity gossip, sports scores, and meme coins unless they are market-moving. "
        "Each name must be a short search query or hashtag, not a sentence. "
        'Return JSON only: {"trends":[{"name":"Topic","post_count":"Rising or ~12k posts",'
        '"category":"Space"}]}. '
        "post_count should be a short volume hint, not a paragraph."
    )
    headers = {
        "Authorization": f"Bearer {XAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": XAI_MODEL,
        "input": [
            {
                "role": "system",
                "content": (
                    "You curate a tight morning X briefing. Output JSON only. "
                    "Use X search. No markdown."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "tools": [{"type": "x_search", "from_date": yesterday}],
        "store": False,
    }

    def _request():
        response = HTTP_SESSION.post(
            "https://api.x.ai/v1/responses",
            headers=headers,
            json=payload,
            timeout=90,
        )
        if response.status_code >= 400:
            body = (response.text or "")[:300]
            lower = body.lower()
            if response.status_code in {401, 403} or "incorrect api key" in lower or "invalid api key" in lower:
                mark_xai_unavailable(f"xAI {response.status_code}: {body}")
            raise RuntimeError(f"xAI responses {response.status_code}: {body}")
        return response.json()

    result = retry_call("Grok X search", _request, attempts=2)
    parsed = extract_json_object(responses_output_text(result)) if result else None

    if not parsed:
        # Chat completions fallback without live search still beats an empty column.
        chat_payload = {
            "model": XAI_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": "You curate a tight morning X briefing. Output JSON only.",
                },
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
        }

        def _chat():
            response = HTTP_SESSION.post(
                "https://api.x.ai/v1/chat/completions",
                headers=headers,
                json=chat_payload,
                timeout=AI_TIMEOUT,
            )
            if response.status_code >= 400:
                body = (response.text or "")[:300]
                lower = body.lower()
                if response.status_code in {401, 403} or "incorrect api key" in lower or "invalid api key" in lower:
                    mark_xai_unavailable(f"xAI {response.status_code}: {body}")
                raise RuntimeError(f"xAI {response.status_code}: {body}")
            return response.json()["choices"][0]["message"]["content"].strip()

        content = retry_call("Grok X briefing", _chat, attempts=2)
        parsed = extract_json_object(content) if content else None

    raw_trends = []
    if isinstance(parsed, dict):
        raw_trends = parsed.get("trends") or parsed.get("topics") or parsed.get("items") or []
    cards = _collect_trend_cards(raw_trends, "Personalized", limit)
    if cards:
        logger.info("Grok returned %s personalized X topics.", len(cards))
    return cards


def fetch_x_trending(limit=5):
    """
    Personalized X topics for the left Watch column.

    Tries the official personalized_trends endpoint first. That route now
    requires X Premium, so Grok X search is the reliable fallback, tuned to
    this brief (Copenhagen, space, AI, energy).
    """
    logger.info("Fetching personalized X topics...")
    official = _fetch_x_personalized_official(limit)
    personalized = official if official else _fetch_x_personalized_grok(limit)
    if not personalized:
        logger.warning("Personalized X topics unavailable.")
        return []
    return [("Personalized", personalized)]


def _moon_watch(now_utc):
    """Moon phase from a known new moon. No network."""
    known_new = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
    synodic = 29.530588853
    age_days = (now_utc - known_new).total_seconds() / 86400.0
    phase = (age_days / synodic) % 1.0
    illumination = (1.0 - math.cos(2.0 * math.pi * phase)) / 2.0 * 100.0
    names = (
        (0.03, "New Moon"),
        (0.22, "Waxing Crescent"),
        (0.28, "First Quarter"),
        (0.47, "Waxing Gibbous"),
        (0.53, "Full Moon"),
        (0.72, "Waning Gibbous"),
        (0.78, "Last Quarter"),
        (0.97, "Waning Crescent"),
        (1.01, "New Moon"),
    )
    name = "Moon"
    for threshold, label in names:
        if phase < threshold:
            name = label
            break
    lit = int(round(illumination))
    return {
        "name": name,
        "post_count": f"{lit}% Lit",
        "trending_since": "Tonight",
        "link": "https://moon.nasa.gov/moon-in-motion/moon-phases/",
        "category": "Sky Watch",
        "sort_at": tonight_copenhagen().isoformat(),
    }


def _solar_flare_class(flux):
    """Map GOES long-band X-ray flux to A/B/C/M/X class."""
    flux = safe_number(flux)
    if flux is None or flux <= 0:
        return None
    bands = (
        (1e-4, "X"),
        (1e-5, "M"),
        (1e-6, "C"),
        (1e-7, "B"),
        (1e-8, "A"),
    )
    for threshold, letter in bands:
        if flux >= threshold:
            magnitude = flux / threshold
            if magnitude < 10:
                label = f"{letter}{magnitude:.1f}"
                return label[:-2] if label.endswith(".0") else label
            return f"{letter}{int(round(magnitude))}"
    return "A0"


def _sky_aurora_card():
    kp_payload = fetch_json(
        "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json",
        label="NOAA Kp",
    )
    latest = (kp_payload or [])[-1] if isinstance(kp_payload, list) and kp_payload else {}
    kp = safe_number((latest or {}).get("estimated_kp"), latest.get("kp_index") if isinstance(latest, dict) else None)
    kp_label = f"Kp {kp:.1f}".replace(".0", "") if kp is not None else "Kp —"

    aurora = None
    ovation = fetch_json(
        "https://services.swpc.noaa.gov/json/ovation_aurora_latest.json",
        label="NOAA aurora",
    )
    if isinstance(ovation, dict):
        best = None
        best_dist = 10**9
        for row in ovation.get("coordinates") or []:
            if not isinstance(row, (list, tuple)) or len(row) < 3:
                continue
            lon, lat, value = row[0], row[1], row[2]
            dist = abs((safe_number(lon) or 0) - 13) + abs((safe_number(lat) or 0) - 56)
            if dist < best_dist:
                best_dist = dist
                best = safe_number(value)
        aurora = best

    if kp is None:
        chance = "No reading"
        name = "Aurora"
    elif kp >= 6 or (aurora is not None and aurora >= 20):
        chance = "Possible here"
        name = "Aurora Watch"
    elif kp >= 4:
        chance = "North horizon"
        name = "Unsettled Aurora"
    else:
        chance = "Unlikely here"
        name = "Quiet Aurora"

    observed = as_utc((latest or {}).get("time_tag")) or datetime.now(timezone.utc)
    return {
        "name": name,
        "post_count": kp_label,
        "trending_since": chance,
        "link": "https://www.swpc.noaa.gov/",
        "category": "Sky Watch",
        "sort_at": observed.isoformat(),
    }


def _sky_solar_card():
    xrays = fetch_json(
        "https://services.swpc.noaa.gov/json/goes/primary/xrays-6-hour.json",
        label="GOES X-ray",
    )
    longs = [
        row for row in (xrays or [])
        if isinstance(row, dict) and row.get("energy") == "0.1-0.8nm"
    ]
    flux = longs[-1].get("flux") if longs else None
    flare = _solar_flare_class(flux)
    if not flare:
        name, meta = "Solar Flux", "No reading"
    elif flare.startswith("X") or flare.startswith("M"):
        name, meta = f"Solar Class {flare}", "Active sun"
    elif flare.startswith("C"):
        name, meta = f"Solar Class {flare}", "C-class"
    else:
        name, meta = f"Solar Class {flare}", "Quiet sun"
    observed = as_utc(longs[-1].get("time_tag") if longs else None) or datetime.now(timezone.utc)
    return {
        "name": name,
        "post_count": "GOES X-ray",
        "trending_since": meta,
        "link": "https://www.swpc.noaa.gov/products/goes-x-ray-flux",
        "category": "Sky Watch",
        "sort_at": observed.isoformat(),
    }


def _sky_launch_card(now_utc):
    payload = fetch_json(
        "https://ll.thespacedevs.com/2.2.0/launch/upcoming/?limit=8&mode=list",
        label="Launch Library",
    )
    results = payload.get("results") if isinstance(payload, dict) else []
    skip_status = {"success", "failure", "partial failure"}
    for item in results or []:
        if not isinstance(item, dict):
            continue
        status_name = str((item.get("status") or {}).get("name") or "").lower()
        if status_name in skip_status:
            continue
        net_raw = item.get("net")
        try:
            net = datetime.fromisoformat(str(net_raw).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if net.tzinfo is None:
            net = net.replace(tzinfo=timezone.utc)
        if net < now_utc - timedelta(hours=2):
            continue
        full_name = strip_html(item.get("name") or "")
        parts = [part.strip() for part in full_name.split("|", 1)]
        vehicle = parts[0] or "Launch"
        mission = parts[1] if len(parts) > 1 else "Orbital launch"
        launch_id = item.get("id") or ""
        link = f"https://spacelaunchnow.me/launch/{launch_id}/" if launch_id else "https://spacelaunchnow.me/"
        return {
            "name": vehicle,
            "post_count": mission,
            "trending_since": relative_local_label(net),
            "link": link,
            "category": "Sky Watch",
            "sort_at": net.isoformat(),
        }
    return None


def _sky_eclipse_card(now_utc):
    today_local = now_utc.astimezone(COPENHAGEN_TZ).date()
    chosen = None
    for event in UPCOMING_ECLIPSES:
        event_local = event["when"].astimezone(COPENHAGEN_TZ).date()
        if event_local >= today_local:
            chosen = event
            break
    if not chosen:
        return None
    event_local = chosen["when"].astimezone(COPENHAGEN_TZ).date()
    delta = (event_local - today_local).days
    if delta == 0:
        when_label = "This Morning" if chosen["when"] < now_utc else "Today"
    elif delta == 1:
        when_label = "Tomorrow"
    elif delta < 14:
        when_label = relative_local_label(chosen["when"])
    else:
        when_label = chosen["when"].astimezone(COPENHAGEN_TZ).strftime("%b %d, %Y").replace(" 0", " ")
    return {
        "name": chosen["name"],
        "post_count": chosen["detail"],
        "trending_since": when_label,
        "link": chosen["link"],
        "category": "Sky Watch",
        "sort_at": chosen["when"].isoformat(),
    }


def fetch_sky_watch(limit=5):
    """
    Copenhagen-facing sky briefing: moon, aurora, solar weather, next launch, next eclipse.

    Fills the column that used to be United States X trends.
    """
    logger.info("Fetching Sky Watch...")
    now_utc = datetime.now(timezone.utc)
    cards = []

    builders = (
        lambda: _moon_watch(now_utc),
        _sky_aurora_card,
        _sky_solar_card,
        lambda: _sky_launch_card(now_utc),
        lambda: _sky_eclipse_card(now_utc),
    )
    for builder in builders:
        try:
            card = builder()
        except Exception as exc:
            logger.warning("Sky Watch item failed: %s", exc)
            continue
        if not card or not card.get("name"):
            continue
        cards.append(card)

    far_future = datetime.max.replace(tzinfo=timezone.utc)

    def sort_key(card):
        moment = as_utc(card.get("sort_at")) or far_future
        return (moment, card.get("name") or "")

    cards.sort(key=sort_key)
    cards = cards[:limit]

    if not cards:
        logger.warning("Sky Watch returned no items.")
    else:
        logger.info(
            "Sky Watch assembled %s items: %s",
            len(cards),
            ", ".join(item.get("name") or "?" for item in cards),
        )
    return cards


def fetch_reflection(seed_date=None):
    """
    Generate one difficult Christian philosophical / psychological question.

    Replaces both the Jesus quote and the stoic/proverb quote.
    """
    logger.info("Generating Christian reflection question...")
    seed = seed_date or date.today()
    fallback_questions = [
        {
            "text": "If love of neighbour is the measure of faith, what does your irritation with the people closest to you reveal about the god you actually trust?",
            "focus": "Love and self-knowledge",
        },
        {
            "text": "When you pray for guidance but already know the answer that would cost you least, are you seeking God or permission?"
        },
        {
            "text": "If forgiveness requires truth, what wound are you calling 'grace' so you never have to name the harm?",
            "focus": "Forgiveness",
        },
        {
            "text": "Would your public Christian convictions survive if they never improved your status, only your obedience?",
            "focus": "Integrity",
        },
        {
            "text": "Where does your need to be right quietly replace your duty to be merciful?",
            "focus": "Pride and mercy",
        },
        {
            "text": "If Christ is present in weakness, why do you treat your own limits as evidence that God is absent?",
            "focus": "Weakness",
        },
        {
            "text": "What part of your moral life is performance for an audience you would never admit you need?",
            "focus": "Authenticity",
        },
    ]
    fallback = fallback_questions[(seed.toordinal() - 1) % len(fallback_questions)]

    if not XAI_AVAILABLE:
        return fallback

    headers = {
        "Authorization": f"Bearer {XAI_API_KEY}",
        "Content-Type": "application/json",
    }
    prompt = (
        "Write one difficult philosophical and psychological question centered on Christianity. "
        "It should make a thoughtful adult stop and examine conscience, motive, faith, pride, love, "
        "forgiveness, suffering, or hypocrisy. No quote, no Bible citation, no sermon, no answer. "
        "One sentence only. Return JSON: "
        "{\"text\": \"question\", \"focus\": \"short theme\"}."
    )
    payload = {
        "model": XAI_MODEL,
        "messages": [
            {"role": "system", "content": "You write piercing, non-cynical Christian reflection questions. Output JSON only."},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
    }

    def _request():
        response = HTTP_SESSION.post(
            "https://api.x.ai/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=AI_TIMEOUT,
        )
        if response.status_code >= 400:
            body = (response.text or "")[:300]
            lower = body.lower()
            if response.status_code in {401, 403} or "incorrect api key" in lower or "invalid api key" in lower:
                mark_xai_unavailable(f"xAI {response.status_code}: {body}")
            raise RuntimeError(f"xAI {response.status_code}: {body}")
        content = response.json()["choices"][0]["message"]["content"].strip()
        return json.loads(content)

    question = retry_call("Reflection fetch", _request)
    if not isinstance(question, dict) or not question.get("text"):
        return fallback
    text = str(question.get("text") or "").strip()
    focus = str(question.get("focus") or "Reflection").strip() or "Reflection"
    if not text.endswith("?"):
        text = text.rstrip(".!") + "?"
    return {"text": text, "focus": focus}


# ==============================================================================
# SITE / PDF GENERATION
# ==============================================================================


def render_html(data):
    """Render the Jinja2 template for the website (and Slack PDF when enabled)."""
    env = Environment(loader=FileSystemLoader(os.path.dirname(__file__) or "."))
    template = env.get_template("brevity_template.html")
    return template.render(**data)


def write_site_html(data, path=SITE_HTML_PATH):
    """Write the static GitHub Pages homepage."""
    logger.info("Writing site HTML to %s...", path)
    try:
        html_out = render_html(data)
        with open(path, "w", encoding="utf-8") as file:
            file.write(html_out)
        logger.info("Site HTML written to %s", path)
        return path
    except Exception as exc:
        logger.error("Error writing site HTML: %s", exc)
        return None


def generate_pdf(data, path=PDF_PATH):
    """Generate PDF from the HTML template using WeasyPrint."""
    logger.info("Generating PDF...")
    try:
        from weasyprint import HTML
    except Exception as exc:
        logger.error("WeasyPrint import failed: %s", exc)
        logger.error("PDF generation skipped. Install WeasyPrint system dependencies.")
        return None
    try:
        html_out = render_html(data)
        HTML(string=html_out, base_url=os.path.dirname(__file__) or ".").write_pdf(path)
        logger.info("PDF generated at %s", path)
        return path
    except Exception as exc:
        logger.error("Error generating PDF: %s", exc)
        return None


def send_to_slack(pdf_path):
    """Upload PDF to Slack using the v2 API (optional)."""
    logger.info("Sending to Slack...")

    if not os.path.exists(pdf_path):
        logger.error("PDF file does not exist.")
        return

    if not SLACK_BOT_TOKEN:
        logger.warning("SLACK_BOT_TOKEN not found. Skipping upload.")
        return

    client = WebClient(token=SLACK_BOT_TOKEN)

    def _upload():
        client.files_upload_v2(
            channel=SLACK_CHANNEL_ID,
            file=pdf_path,
            title=f"Brevity - {date.today().strftime('%Y-%m-%d')}",
            initial_comment="Here is your update Mr Smith.",
        )
        return True

    result = retry_call("Slack upload", _upload, attempts=3, base_delay=2.0)
    if result:
        logger.info("PDF uploaded to Slack successfully.")
    else:
        logger.error("Slack upload failed after retries.")


def build_brief_data(today=None):
    """Fetch all sources and assemble the daily brief payload."""
    today = today or date.today()
    weather = fetch_weather()
    stocks = fetch_stocks()
    space_news = fetch_space_news()
    copenhagen = fetch_copenhagen_events()
    x_trending = fetch_x_trending(limit=5)
    sky_watch = fetch_sky_watch(limit=5)
    reflection = fetch_reflection(today)

    day_of_year = today.timetuple().tm_yday
    days_in_year = 366 if calendar.isleap(today.year) else 365
    year_percent = (day_of_year / days_in_year) * 100
    generated_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")

    return {
        "date": today.strftime("%A, %B %d"),
        "iso_date": today.isoformat(),
        "generated_at": generated_at,
        "year_percent": year_percent,
        "weather": weather,
        "stocks": stocks,
        "space_news": space_news,
        "copenhagen": copenhagen,
        "reflection": reflection,
        "x_trending": x_trending,
        "sky_watch": sky_watch,
    }


def main():
    logger.info("Starting Brevity generation...")
    if XAI_AVAILABLE:
        logger.info("Using xAI model: %s", XAI_MODEL)
    else:
        logger.warning("XAI_API_KEY missing; news will not be summarised.")

    data = build_brief_data()

    # Public GitHub Pages only needs the rendered homepage.
    write_site_html(data)

    # PDF is retained solely for optional Slack delivery.
    pdf_path = None
    if SEND_TO_SLACK:
        if GENERATE_PDF:
            pdf_path = generate_pdf(data)
        if pdf_path:
            send_to_slack(pdf_path)
        else:
            logger.warning("SEND_TO_SLACK enabled but no PDF was generated.")

    logger.info("Done.")


if __name__ == "__main__":
    main()
