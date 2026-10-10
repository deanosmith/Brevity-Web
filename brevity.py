import os
import ssl
import sys
import re
import html
import hashlib
import json
import logging
import math
import random
import time
import requests
import calendar
from urllib.parse import quote, urlparse
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

load_dotenv()

XAI_API_KEY = os.getenv("XAI_API_KEY")
XAI_MODEL = (os.getenv("XAI_MODEL") or "grok-4.20-non-reasoning").strip() or "grok-4.20-non-reasoning"
ESV_API_KEY = (os.getenv("ESV_API_KEY") or "").strip()
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID")
SEND_TO_SLACK = os.getenv("SEND_TO_SLACK", "").strip().lower() in {"1", "true", "yes", "on"}
GENERATE_PDF = os.getenv("GENERATE_PDF", "1").strip().lower() not in {"0", "false", "no", "off"}

if sys.platform == "darwin":
    os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = (
        "/opt/homebrew/lib:" + os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    )

if hasattr(ssl, "_create_unverified_context"):
    ssl._create_default_https_context = ssl._create_unverified_context

# WMO code -> (color, label, icon)
WEATHER = {
    0: ("#FFD700", "Clear Sky", "☀️"),
    1: ("#87CEEB", "Partly Cloudy", "⛅️"),
    2: ("#87CEEB", "Partly Cloudy", "⛅️"),
    3: ("#87CEEB", "Overcast", "☁️"),
    45: ("#708090", "Foggy", "🌫️"),
    48: ("#708090", "Rime Fog", "🌫️"),
    51: ("#4682B4", "Light Drizzle", "🌦️"),
    53: ("#4682B4", "Drizzle", "🌦️"),
    55: ("#4682B4", "Heavy Drizzle", "🌧️"),
    61: ("#4682B4", "Light Rain", "🌧️"),
    63: ("#4682B4", "Rain", "🌧️"),
    65: ("#4682B4", "Heavy Rain", "🌧️"),
    80: ("#4682B4", "Showers", "🌦️"),
    81: ("#4682B4", "Showers", "🌦️"),
    82: ("#4682B4", "Showers", "🌦️"),
    71: ("#E0FFFF", "Light Snow", "🌨️"),
    73: ("#E0FFFF", "Snow", "🌨️"),
    75: ("#E0FFFF", "Heavy Snow", "🌨️"),
    77: ("#E0FFFF", "Snow Grains", "🌨️"),
    95: ("#9370DB", "Thunderstorm", "⛈️"),
    96: ("#9370DB", "Thunderstorm", "⛈️"),
    99: ("#9370DB", "Thunderstorm", "⛈️"),
}
_WEATHER_FALLBACK = ("#AAAAAA", "Unknown", "?")

STOCK_TICKERS = {
    "Tesla": "TSLA",
    "SPCX": "SPCX",
    "Nvidia": "NVDA",
    "Oklo": "OKLO",
    "Micron": "MU",
    "Palantir": "PLTR",
    "Bitcoin": "BTC-USD",
    "Vanguard S&P 500": "VUSA.AS",
}

COPENHAGEN_FEEDS = [
    "https://cphpost.dk/feed/",
    "https://www.cphpost.dk/feed/",
    "https://cphpost.dk/category/news/feed/",
    "https://www.thelocal.dk/feeds/rss.php",
]
SPACE_FEEDS = [
    "https://spacenews.com/feed/",
    "https://spaceflightnow.com/feed/",
    "https://www.nasaspaceflight.com/feed/",
]
SOUTH_AFRICA_FEEDS = [
    "https://www.sanews.gov.za/rss.xml",
    "https://www.gov.za/rss.xml",
    "https://www.gcis.gov.za/rss.xml",
    "https://www.moneyweb.co.za/feed/",
    "https://www.dailymaverick.co.za/dmrss/",
    "https://mg.co.za/rss/",
    "https://www.sabcnews.com/sabcnews/feed/",
]
SOUTH_AFRICA_SKIP_RE = re.compile(
    r"\b(murder|hijack|rape|assault|robbery|shooting|pothole|lotto|"
    r"soccer|rugby|cricket|bafana|banyana|springbok|celebrity)\b",
    re.I,
)
SOUTH_AFRICA_PETTY_RE = re.compile(
    r"\b("
    r"ethekwini|hammarsdale|pothole|traders|informal economy|"
    r"heritage month|public service month|tourism month|"
    r"apply for ids?|identity documents?|"
    r"women['’]?s struggle|sport awards|commemorat|"
    r"load.?shedding schedule"
    r")\b",
    re.I,
)
SOUTH_AFRICA_GEO_RE = re.compile(
    r"\b(south africa|south african|pretoria|ramaphosa|gnu|"
    r"reserve bank|sarb|dirco|agoa|the rand)\b",
    re.I,
)
SOUTH_AFRICA_KEEP_RE = re.compile(
    r"\b(cabinet|parliament|presidency|ramaphosa|gnu|"
    r"government of national unity|treasury|"
    r"sarb|reserve bank|repo rate|the rand|zar|"
    r"gdp|inflation|unemployment|budget|current account|"
    r"credit rating|fatf|investment|"
    r"brics|sadc|dirco|diplomat|agoa|trade|export|imf|world bank|"
    r"election|eskom)\b",
    re.I,
)
SOUTH_AFRICA_WEAK_TERMS = frozenset({"minister", "policy", "election"})

COPENHAGEN_TZ = ZoneInfo("Europe/Copenhagen")
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

SITE_HTML_PATH = "index.html"
BRIEF_DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "brief-data.json")
NEWS_KEYS = ("copenhagen", "south_africa", "space_news")
GITHUB_REPO = (os.getenv("GITHUB_REPOSITORY") or "deanosmith/Brevity-Web").strip()
GITHUB_WORKFLOW = "main.yml"
PDF_PATH = "brevity.pdf"
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTURE_VERSES_PATH = os.path.join(_BASE_DIR, "resources", "scripture-verses.json")
SCRIPTURE_HISTORY_PATH = os.path.join(_BASE_DIR, "resources", "scripture-history.json")
SCRIPTURE_FALLBACK = [
    {
        "ref": "Proverbs 3:5",
        "book": "Proverbs",
        "text": "Trust in the Lord with all thine heart; and lean not unto thine own understanding.",
    },
    {
        "ref": "Proverbs 27:1",
        "book": "Proverbs",
        "text": "Boast not thyself of to morrow; for thou knowest not what a day may bring forth.",
    },
    {
        "ref": "Ecclesiastes 3:1",
        "book": "Ecclesiastes",
        "text": "To every thing there is a season, and a time to every purpose under the heaven:",
    },
    {
        "ref": "Ecclesiastes 12:13",
        "book": "Ecclesiastes",
        "text": "Let us hear the conclusion of the whole matter: Fear God, and keep his commandments: for this is the whole duty of man.",
    },
]
ESV_API_URL = "https://api.esv.org/v3/passage/text/"
SCRIPTURE_TRANSLATIONS = {
    "Esv": {"name": "English Standard Version", "url": "https://www.esv.org/", "gateway": "ESV"},
    "Kjv": {"name": "King James Version", "url": "https://www.biblegateway.com/versions/King-James-Version-KJV-Bible/", "gateway": "KJV"},
}
DEFAULT_HEADERS = {"User-Agent": "Brevity/1.0 (+https://deanosmith.github.io/Brevity-Web/)"}
REQUEST_TIMEOUT = 30
AI_TIMEOUT = 60
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 1.2
RETRY_STATUS_CODES = (429, 500, 502, 503, 504)
HTML_TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")

WEATHER_LAT = 55.6761
WEATHER_LON = 12.5683
WEATHER_TIMEZONE = "Europe/Copenhagen"
WIND_DIAL_MAX_KMH = 50
WEATHER_SOURCE = {
    "name": "Open-Meteo",
    "url": "https://open-meteo.com/",
    "location": "Copenhagen",
}


def get_weather_color(code):
    return WEATHER.get(code, _WEATHER_FALLBACK)[0]


def get_weather_text(code):
    return WEATHER.get(code, _WEATHER_FALLBACK)[1]


def get_weather_icon(code):
    return WEATHER.get(code, _WEATHER_FALLBACK)[2]


def _rgb_to_hex(rgb):
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _mix_rgb(stops, t):
    t = max(0.0, min(1.0, t))
    for index in range(len(stops) - 1):
        left_t, left_rgb = stops[index]
        right_t, right_rgb = stops[index + 1]
        if t <= right_t:
            local = 0 if right_t == left_t else (t - left_t) / (right_t - left_t)
            return tuple(int(round(a + (b - a) * local)) for a, b in zip(left_rgb, right_rgb))
    return stops[-1][1]


def plasma_color(value, vmin=0.0, vmax=40.0):
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = vmin
    t = 0.0 if vmax == vmin else (numeric - vmin) / (vmax - vmin)
    return _rgb_to_hex(_mix_rgb(
        ((0.0, (13, 8, 135)), (0.25, (106, 0, 168)), (0.5, (177, 42, 144)), (0.75, (225, 100, 98)), (1.0, (240, 249, 33))),
        t,
    ))


def clamp01(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


def temperature_color(celsius):
    return _rgb_to_hex(_mix_rgb(
        ((0.0, (64, 148, 255)), (0.28, (90, 200, 255)), (0.5, (255, 214, 102)), (0.75, (255, 140, 66)), (1.0, (255, 69, 58))),
        clamp01(((safe_number(celsius, 10) or 10) + 5) / 35.0),
    ))


def rain_color(percent):
    progress = clamp01((safe_number(percent, 0) or 0) / 100.0)
    return _rgb_to_hex(tuple(
        int(round(a + (b - a) * progress))
        for a, b in zip((55, 78, 110), (64, 196, 255))
    ))


def uv_color(uv_index):
    return _rgb_to_hex(_mix_rgb(
        ((0.0, (76, 175, 80)), (0.35, (255, 235, 59)), (0.6, (255, 152, 0)), (1.0, (244, 67, 54))),
        clamp01((safe_number(uv_index, 0) or 0) / 11.0),
    ))


def _temp_dial_range(high_v, low_v):
    low_progress = clamp01(((low_v if low_v is not None else 5) + 5) / 35.0)
    high_progress = clamp01(((high_v if high_v is not None else 10) + 5) / 35.0)
    if high_progress < low_progress:
        high_progress = low_progress
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
    digest = hashlib.md5(keyword.lower().encode("utf-8")).hexdigest()
    index = int(digest[:8], 16) % len(KEYWORD_COLOR_PALETTE)
    bg, border, text = KEYWORD_COLOR_PALETTE[index]
    return f"--kw-bg: {bg}; --kw-border: {border}; --kw-text: {text};"


def to_pascal_case(value):
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
    if raw_since is None:
        return None
    if isinstance(raw_since, (int, float)):
        if raw_since <= 0:
            return None
        try:
            return format_clock_12(datetime.utcfromtimestamp(raw_since).strftime("%H:%M"))
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
                return format_clock_12(datetime.utcfromtimestamp(ts_value).strftime("%H:%M"))
            except Exception:
                return None
        match = TRENDING_TIME_RE.search(value)
        if match:
            hour = int(match.group(1))
            minute = match.group(2)
            if 0 <= hour <= 23:
                prefix = value[: match.start()].strip()
                suffix = value[match.end() :].strip()
                if suffix.lower() in {"am", "pm"}:
                    return format_clock_12(value)
                clock = format_clock_12(f"{hour:02d}:{minute}")
                parts = [part for part in (prefix, clock, suffix) if part]
                return " ".join(parts)
        lowered = value.lower()
        if "trend" in lowered or "now" in lowered:
            return "Now"
        converted = format_clock_12(value)
        if converted:
            return converted
        if len(value) <= 8:
            return value
    return None


def safe_number(value, default=None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if isinstance(number, float) and (math.isnan(number) or math.isinf(number)):
        return default
    return number


def format_clock(value):
    if not value or not isinstance(value, str):
        return None
    if "T" in value:
        return value.split("T", 1)[1][:5]
    if len(value) >= 5 and value[2] == ":":
        return value[:5]
    return value


def clock_to_minutes(value):
    if value is None:
        return None
    text = str(value).strip()
    if "T" in text:
        text = format_clock(text) or text
    match = re.match(r"^(\d{1,2}):(\d{2})(?:\s*(am|pm))?$", text, re.I)
    if not match:
        return None
    hours = int(match.group(1))
    minutes = int(match.group(2))
    period = (match.group(3) or "").lower()
    if period:
        hours = hours % 12
        if period == "pm":
            hours += 12
    if hours > 23 or minutes > 59:
        return None
    return hours * 60 + minutes


_SUN_PATH_CX = 120.0
_SUN_PATH_CY = 58.0
_SUN_PATH_RX = 100.0
_SUN_PATH_RY = 44.0
_SUN_PATH_NIGHT_Y = _SUN_PATH_CY + 8.0


def _sun_arc_point(progress, cx=_SUN_PATH_CX, cy=_SUN_PATH_CY, rx=_SUN_PATH_RX, ry=_SUN_PATH_RY):
    t = max(0.0, min(1.0, float(progress)))
    angle = math.pi * t
    return (
        round(cx - rx * math.cos(angle), 1),
        round(cy - ry * math.sin(angle), 1),
    )


def build_sun_path(sunrise, sunset, rain_peak=None, now=None):
    rise = clock_to_minutes(sunrise)
    set_at = clock_to_minutes(sunset)
    if rise is None or set_at is None or set_at <= rise:
        return None

    if hasattr(now, "hour"):
        now_minutes = now.hour * 60 + now.minute
    else:
        now_minutes = clock_to_minutes(now)
    if now_minutes is None:
        now_minutes = rise

    span = set_at - rise
    start_x = round(_SUN_PATH_CX - _SUN_PATH_RX, 1)
    end_x = round(_SUN_PATH_CX + _SUN_PATH_RX, 1)
    is_day = rise <= now_minutes <= set_at
    if now_minutes < rise:
        sun_x, sun_y = start_x, _SUN_PATH_NIGHT_Y
    elif now_minutes > set_at:
        sun_x, sun_y = end_x, _SUN_PATH_NIGHT_Y
    else:
        sun_x, sun_y = _sun_arc_point((now_minutes - rise) / span)

    hours, minutes = divmod(span, 60)
    daylight = f"{hours}h {minutes:02d}m" if minutes else f"{hours}h"
    rain_minutes = clock_to_minutes(rain_peak)
    rain_on_arc = rain_minutes is not None and rise <= rain_minutes <= set_at
    rain_x = rain_y = None
    if rain_minutes is not None:
        if rain_on_arc:
            rain_x, rain_y = _sun_arc_point((rain_minutes - rise) / span)
        elif rain_minutes < rise:
            rain_x, rain_y = round(start_x - 10, 1), _SUN_PATH_CY
        else:
            rain_x, rain_y = round(end_x + 10, 1), _SUN_PATH_CY

    arc_d = (
        f"M{start_x:.0f} {_SUN_PATH_CY:.0f} "
        f"A{_SUN_PATH_RX:.0f} {_SUN_PATH_RY:.0f} 0 0 1 {end_x:.0f} {_SUN_PATH_CY:.0f}"
    )
    return {
        "sunrise": format_clock_12(sunrise) or sunrise,
        "sunset": format_clock_12(sunset) or sunset,
        "sun_x": sun_x,
        "sun_y": sun_y,
        "is_day": is_day,
        "daylight": daylight,
        "rain_peak": rain_peak,
        "rain_on_arc": rain_on_arc,
        "rain_x": rain_x,
        "rain_y": rain_y,
        "cy": int(_SUN_PATH_CY),
        "start_x": start_x,
        "end_x": end_x,
        "arc_d": arc_d,
        "sky_d": f"{arc_d} L{end_x:.0f} {_SUN_PATH_CY:.0f} L{start_x:.0f} {_SUN_PATH_CY:.0f} Z",
        "horizon_x1": 8,
        "horizon_x2": 232,
        "tick_y1": int(_SUN_PATH_CY - 5),
        "tick_y2": int(_SUN_PATH_CY + 4),
        "sunrise_clock_x": round(start_x + 22, 1),
        "sunset_clock_x": round(end_x - 22, 1),
        "clock_y": int(_SUN_PATH_CY - 8),
    }


def format_hour_12(hour, with_minutes=False, with_period=True):
    try:
        hour_i = int(hour) % 24
    except (TypeError, ValueError):
        return None
    hour_12 = hour_i % 12
    if hour_12 == 0:
        hour_12 = 12
    text = f"{hour_12}:00" if with_minutes else str(hour_12)
    if with_period:
        suffix = "am" if hour_i < 12 else "pm"
        return f"{text} {suffix}"
    return text


def format_clock_12(value, with_period=True):
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return format_hour_12(int(value), with_minutes=False, with_period=with_period)
    text = str(value).strip()
    if not text:
        return None
    if "T" in text:
        text = format_clock(text) or text
    match = re.match(r"^(\d{1,2}):(\d{2})(?:\s*(am|pm))?$", text, re.I)
    if not match:
        return format_hour_12(text, with_minutes=False, with_period=with_period)
    hour_i = int(match.group(1))
    minute_i = int(match.group(2))
    period = (match.group(3) or "").lower()
    if period:
        hour_i = hour_i % 12
        if period == "pm":
            hour_i += 12
    hour_12 = hour_i % 12
    if hour_12 == 0:
        hour_12 = 12
    clock = f"{hour_12}:{minute_i:02d}"
    if with_period:
        suffix = "am" if (hour_i % 24) < 12 else "pm"
        return f"{clock} {suffix}"
    return clock


def weekday_label(iso_day, today_iso=None):
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
                return "In 2 Days"
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
    retries = Retry(**retry_kwargs, allowed_methods=frozenset(["GET", "POST"]))
    adapter = HTTPAdapter(max_retries=retries)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


HTTP_SESSION = build_retry_session()


class NoRetry(Exception):
    pass


def retry_call(label, func, attempts=RETRY_ATTEMPTS, base_delay=1.0, max_delay=8.0):
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except NoRetry as exc:
            logger.error("%s failed permanently: %s", label, exc)
            return None
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
    if not value:
        return ""
    text = HTML_TAG_RE.sub(" ", str(value))
    text = html.unescape(text)
    return WHITESPACE_RE.sub(" ", text).strip()


def json_safe(value):
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

    words = re.findall(r"[A-Za-z0-9][\w'#.-]*", name)
    if 1 <= len(words) <= 3 and len(name) <= 40:
        return name

    quoted = re.findall(r"['\"]([^'\"]{2,48})['\"]", name)
    if quoted:
        candidate = quoted[0].strip()
        if candidate:
            return candidate

    leading = re.match(
        r"^((?:[A-Z][\w'&.-]+)(?:\s+(?:[A-Z0-9][\w'&.-]*)){0,3})",
        name,
    )
    if leading:
        phrase = leading.group(1).strip(" -,:;")
        weak_starters = {
            "call", "calls", "new", "why", "how", "what", "when", "after",
            "before", "this", "that", "with", "from", "into", "over",
        }
        if phrase and phrase.lower() not in weak_starters and len(phrase) <= 48:
            return phrase

    stop = {"THE", "AND", "FOR", "WITH", "FROM", "THIS", "THAT", "INTO", "OVER", "AFTER", "ARE", "WAS"}
    caps = [token for token in re.findall(r"\b[A-Z]{3,}(?:\d+)?\b", name) if token not in stop]
    if caps:
        return caps[0]
    if not words:
        return name
    return " ".join(words[:4]).strip(" -,:;") or name


def x_search_link(term):
    query = (term or "").strip()
    if not query:
        return "https://x.com/explore"
    return f"https://x.com/search?q={quote(query)}&src=typed_query"


def fetch_json(url, params=None, extra_headers=None, label="JSON fetch"):
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
    return datetime.now(COPENHAGEN_TZ)


def as_utc(moment):
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
    now = copenhagen_now()
    evening = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if evening < now:
        return now
    return evening


def sky_when_label(moment, prefix="Launch"):
    raw = relative_local_label(moment)
    if not raw:
        return None
    if raw.startswith("Today "):
        return f"{prefix} Today At {raw[6:]}"
    if raw.startswith("Tomorrow "):
        return f"{prefix} Tomorrow At {raw[9:]}"
    parts = raw.split(" ", 1)
    if len(parts) == 2:
        return f"{prefix} {parts[0]} At {parts[1]}"
    return f"{prefix} {raw}"


def relative_local_label(moment):
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


def _peak_rain_time(times, values, day_iso):
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
    return format_clock_12(times[peak_index], with_period=True)


def _hourly_rain_points(times, values, day_iso, start_h=0, end_h=24):
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
    label_hours = {start_h, 9, 12, 15, 18, 21, end_h - 1}
    for hour in range(start_h, end_h):
        precip = by_hour.get(hour, 0)
        label = format_hour_12(hour, with_minutes=False) if hour in label_hours else None
        points.append(
            {
                "hour": hour,
                "precip": precip,
                "label": label,
                "clock": format_hour_12(hour, with_minutes=True),
            }
        )
    return points


def _rain_timeline(times, values, day_iso, start_h=0, end_h=24, rain_color_value=None):
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
        peak_time = format_hour_12(peak_point.get("hour") or 0, with_minutes=True, with_period=True)

    color_source = rain_color_value if rain_color_value is not None else max_precip
    return {
        "points": points,
        "max_precip": max_precip,
        "peak_time": peak_time,
        "color": rain_color(color_source),
    }


def _segment_stats(hourly, start_h, end_h):

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
    if percent is None:
        return "grey", "-"
    if percent >= 0:
        return "green", "↑"
    return "red", "↓"


def fetch_weather():
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
            "sunrise": format_clock_12((daily.get("sunrise") or [None])[0]),
            "sunset": format_clock_12((daily.get("sunset") or [None])[0]),
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
            "morning": _segment_stats(hourly, 6, 12),
            "afternoon": _segment_stats(hourly, 12, 18),
            "evening": _segment_stats(hourly, 18, 24),
        }
        today["sun_path"] = build_sun_path(
            (daily.get("sunrise") or [None])[0],
            (daily.get("sunset") or [None])[0],
            rain_peak=today.get("rain_peak_time"),
            now=copenhagen_now(),
        )
        today["dials"] = dial_metrics(
            rain_chance=today.get("rain_chance"),
            wind_max=today.get("wind_max"),
            uv_max=today.get("uv_max"),
            high=today.get("high"),
            low=today.get("low"),
        )
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
        }
    except Exception as exc:
        logger.error("Error parsing weather data: %s", exc)
        return None


def fetch_stocks():
    logger.info("Fetching stocks...")
    stock_data = {}

    for name, symbol in STOCK_TICKERS.items():
        def _fetch_history(period="3mo"):
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period=period, auto_adjust=True)
            if hist is None or hist.empty:
                raise ValueError("No price history returned")
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
            prev_close = closes[-2] if len(closes) > 1 else None
            change = current_close - prev_close if prev_close else None
            percent_change = (change / prev_close) * 100 if prev_close else None
            percent_7d = _percent_from_closes(closes, 5)
            percent_1m = _percent_from_closes(closes, 21)
            color_day, arrow_day = _change_style(percent_change)
            color_7d, arrow_7d = _change_style(percent_7d)
            color_1m, arrow_1m = _change_style(percent_1m)
            stock_data[name] = {
                "symbol": symbol,
                "price": current_close,
                "change": change,
                "percent": percent_change,
                "color": color_day,
                "arrow": arrow_day,
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
    stock_data["average_percent"] = sum(percents) / len(percents) if percents else None
    return stock_data


XAI_AVAILABLE = bool(XAI_API_KEY)


def mark_xai_unavailable(reason):
    global XAI_AVAILABLE
    if XAI_AVAILABLE:
        logger.error("Disabling xAI for this run: %s", reason)
    XAI_AVAILABLE = False


def raise_for_xai(response, label="xAI"):
    if response.status_code < 400:
        return
    body = (response.text or "")[:300]
    lower = body.lower()
    message = f"{label} {response.status_code}: {body}"
    if response.status_code in {401, 403} or "incorrect api key" in lower or "invalid api key" in lower:
        mark_xai_unavailable(message)
        raise NoRetry(message)
    raise RuntimeError(message)


XAI_USAGE = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "x_posts_fetched": 0, "x_users_fetched": 0}


def record_xai_usage(label, payload):
    usage = (payload or {}).get("usage") or {}
    details = usage.get("server_side_tool_usage_details") or {}
    counts = {
        "input_tokens": int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or usage.get("completion_tokens") or 0),
        "x_posts_fetched": int(details.get("x_posts_fetched") or 0),
        "x_users_fetched": int(details.get("x_users_fetched") or 0),
    }
    XAI_USAGE["calls"] += 1
    for key, value in counts.items():
        XAI_USAGE[key] += value
    logger.info(
        "%s usage: %s in / %s out tokens, %s posts, %s profiles",
        label, counts["input_tokens"], counts["output_tokens"], counts["x_posts_fetched"], counts["x_users_fetched"],
    )


def summarize_with_ai(text, prompt_prefix="Summarize this news item:"):
    clean_text = strip_html(text)
    if not clean_text or not XAI_AVAILABLE:
        return None

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
        if not XAI_AVAILABLE:
            raise NoRetry("xAI disabled for this run")
        response = HTTP_SESSION.post(
            "https://api.x.ai/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=AI_TIMEOUT,
        )
        raise_for_xai(response)
        body = response.json()
        record_xai_usage("AI summary", body)
        content = body["choices"][0]["message"]["content"].strip()
        if not content:
            raise ValueError("Empty summary response")
        return content

    summary = retry_call("AI summarization", _summarize)
    if not summary:
        logger.warning("AI summarization failed; using the feed title")
        return None
    return summary


CAPS_RUN_RE = re.compile(r"\b[A-Z][A-Z'’]{3,}(?:[\s-]+[A-Z][A-Z'’]{3,})+\b")


def tidy_headline(text):
    text = strip_html(text)
    letters = [ch for ch in text if ch.isalpha()]
    if letters and all(ch.isupper() for ch in letters):
        return text.title()
    return CAPS_RUN_RE.sub(lambda match: match.group(0).title(), text)


def _news_item(entry, prompt, summarize=True):
    summary = summarize_with_ai(entry["content_text"], prompt) if summarize and XAI_AVAILABLE else None
    if summary:
        headline = stylize_keywords(summary)
    else:
        headline = tidy_headline(entry["title"] or entry["content_text"][:220])
    return {
        "headline": headline,
        "ai_summary": summary,
        "link": entry["link"],
        "source": entry["source"],
    }


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


def _rss_entries(url, limit=5):
    feed = fetch_feed(url)
    if not feed or not getattr(feed, "entries", None):
        return []
    source = strip_html(getattr(getattr(feed, "feed", None), "title", "") or "") or url
    items = []
    for entry in feed.entries[:limit]:
        try:
            title = strip_html(getattr(entry, "title", "") or "")
            summary = strip_html(getattr(entry, "summary", getattr(entry, "description", "")) or "")
            content_text = f"{title}. {summary}".strip(". ").strip()
            if not content_text:
                continue
            items.append({
                "title": title,
                "content_text": content_text,
                "link": entry.get("link", ""),
                "source": source,
                "feed_url": url,
            })
        except Exception as exc:
            logger.warning("Error parsing feed entry from %s: %s", url, exc)
    return items


def fetch_news(urls, limit=6, prompt="Summarize this content:", label="feed", summarize=True):
    chosen = []
    seen = set()
    for url in urls:
        if len(chosen) >= limit:
            break
        logger.info("Trying %s feed: %s", label, url)
        for entry in _rss_entries(url, limit=limit * 2):
            key = _story_key(entry["title"]) or entry["link"]
            if not key or key in seen:
                continue
            seen.add(key)
            chosen.append(entry)
            if len(chosen) >= limit:
                break
    if not chosen:
        logger.warning("No items found for %s feeds", label)
        return []
    items = [_news_item(entry, prompt, summarize=summarize) for entry in chosen]
    logger.info("%s assembled %s items", label, len(items))
    return items


def fetch_space_news():
    logger.info("Fetching space news...")
    return fetch_news(
        SPACE_FEEDS,
        limit=6,
        label="space",
        summarize=False,
    )


def fetch_copenhagen_events():
    logger.info("Fetching Copenhagen events...")
    return fetch_news(
        COPENHAGEN_FEEDS,
        limit=6,
        prompt="Summarize this Copenhagen/Denmark news item.",
        label="copenhagen",
    )


def _story_key(title):
    return re.sub(r"[^a-z0-9]+", "", (title or "").lower())[:80]


def _is_sa_trusted_feed(url):
    host = urlparse(url or "").netloc.lower()
    return host.endswith("gov.za") or host.endswith("moneyweb.co.za")


def _sa_brief_score(entry):
    title = entry.get("title") or ""
    text = entry.get("content_text") or ""
    if not _is_sa_trusted_feed(entry.get("feed_url")):
        if not SOUTH_AFRICA_GEO_RE.search(f"{title} {text[:320]}"):
            return -1
    title_hits = {hit.lower() for hit in SOUTH_AFRICA_KEEP_RE.findall(title)}
    if SOUTH_AFRICA_PETTY_RE.search(title) and not title_hits:
        return -1
    if SOUTH_AFRICA_SKIP_RE.search(title) and not title_hits:
        return -1
    if title_hits:
        return 10 + len(title_hits)

    body_hits = {hit.lower() for hit in SOUTH_AFRICA_KEEP_RE.findall(text)}
    body_hits -= SOUTH_AFRICA_WEAK_TERMS
    if SOUTH_AFRICA_PETTY_RE.search(text) and len(body_hits) < 2:
        return -1
    if SOUTH_AFRICA_SKIP_RE.search(text) and not body_hits:
        return -1
    if len(body_hits) >= 2:
        return len(body_hits)
    return -1


def _pick_diverse_stories(scored_items, limit=6, per_source=2):
    selected = []
    counts = {}
    for score, item in scored_items:
        if score < 0:
            continue
        source = item.get("source") or item.get("link") or ""
        if counts.get(source, 0) >= per_source:
            continue
        selected.append(item)
        counts[source] = counts.get(source, 0) + 1
        if len(selected) >= limit:
            return selected
    selected_ids = {id(item) for item in selected}
    for score, item in scored_items:
        if score < 0 or id(item) in selected_ids:
            continue
        selected.append(item)
        selected_ids.add(id(item))
        if len(selected) >= limit:
            break
    return selected


def fetch_south_africa_news():
    logger.info("Fetching South Africa news...")
    seen = set()
    scored = []
    for url in SOUTH_AFRICA_FEEDS:
        logger.info("Trying south africa feed: %s", url)
        for entry in _rss_entries(url, limit=10):
            key = _story_key(entry["title"])
            if not key or key in seen:
                continue
            seen.add(key)
            score = _sa_brief_score(entry)
            scored.append((score, entry))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    chosen = _pick_diverse_stories(scored, limit=6, per_source=2)
    if not chosen:
        logger.warning("No items found for south africa feeds")
        return []

    prompt = (
        "Summarize this South Africa news item as one concise factual sentence. "
        "Prefer national government decisions, major financial developments, or international relations. "
        "Omit petty municipal, crime, celebrity, and sports detail."
    )
    news_items = [_news_item(entry, prompt) for entry in chosen]
    logger.info("South Africa feed assembled %s items", len(news_items))
    return news_items


def _format_post_count(value):
    if isinstance(value, int):
        return f"{value:,} posts"
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _trend_card(name, post_count=None, category="Personalized", trending_since=None, link=None):
    raw_name = strip_html(str(name or "")).strip()
    if not raw_name:
        return None
    search_term = extract_search_term(raw_name) or raw_name
    since = format_trending_since(trending_since) if trending_since else None
    if since and since[:1].isdigit():
        since = f"Since {since}"
    category = (strip_html(str(category or "")).strip()[:24] or "Personalized")
    return {
        "name": raw_name,
        "search_term": search_term,
        "post_count": _format_post_count(post_count) or "Live",
        "category": category,
        "category_style": keyword_style(category),
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
            "X personalized trends require Premium; leaving trends unavailable."
        )
        return None
    if response.status_code != 200:
        logger.warning("X personalized API error %s: %s", response.status_code, body)
        return None

    payload = response.json() if response.content else {}
    raw_items = payload.get("data", []) if isinstance(payload, dict) else []
    logger.info("X personalized API returned %s items.", len(raw_items or []))
    return _collect_trend_cards(raw_items, "Personalized", limit)


def fetch_x_trending(limit=5):
    logger.info("Fetching personalized X topics...")
    topics = _fetch_x_personalized_official(limit)
    if not topics:
        logger.warning("Personalized X topics unavailable.")
        return None
    return {"topics": topics, "source": "X Personalized Trends"}


def _moon_watch(now_utc):
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
        "post_count": f"Moon {lit}% Illuminated",
        "trending_since": "Visible Tonight Over Copenhagen",
        "link": "https://moon.nasa.gov/moon-in-motion/moon-phases/",
        "category": "Sky Watch",
        "sort_at": tonight_copenhagen().isoformat(),
    }


def _solar_flare_class(flux):
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
        logger.warning("No Kp reading; skipping aurora card")
        return None
    if kp >= 6 or (aurora is not None and aurora >= 20):
        chance = "Visible Aurora Possible Tonight"
        name = "Aurora Watch"
    elif kp >= 4:
        chance = "Possible On The North Horizon"
        name = "Unsettled Aurora"
    else:
        chance = "Visible Aurora Unlikely Tonight"
        name = "Quiet Aurora"

    observed = as_utc((latest or {}).get("time_tag")) or datetime.now(timezone.utc)
    return {
        "name": name,
        "post_count": f"Geomagnetic Index {kp_label}",
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
        if isinstance(row, dict)
        and row.get("energy") == "0.1-0.8nm"
        and (safe_number(row.get("flux")) or 0) > 0
    ]
    flux = longs[-1].get("flux") if longs else None
    flare = _solar_flare_class(flux)
    if not flare:
        logger.warning("No GOES X-ray reading; skipping solar card")
        return None
    if flare.startswith("X") or flare.startswith("M"):
        name, meta = f"Solar Class {flare}", "Active Sun, Major Flare Risk"
    elif flare.startswith("C"):
        name, meta = f"Solar Class {flare}", "Modest C-Class Activity"
    else:
        name, meta = f"Solar Class {flare}", "Quiet Sun, No Major Flares"
    observed = as_utc(longs[-1].get("time_tag") if longs else None) or datetime.now(timezone.utc)
    return {
        "name": name,
        "post_count": "Goes Satellite X-Ray Reading",
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
            "post_count": f"Mission {mission}" if mission else "Orbital Launch",
            "trending_since": sky_when_label(net, prefix="Launch"),
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
        when_label = "Visible This Morning" if chosen["when"] < now_utc else "Visible Today"
    elif delta == 1:
        when_label = "Visible Tomorrow"
    elif delta < 14:
        when_label = sky_when_label(chosen["when"], prefix="Visible")
    else:
        date_label = chosen["when"].astimezone(COPENHAGEN_TZ).strftime("%b %d, %Y").replace(" 0", " ")
        when_label = f"Next Visible On {date_label}"
    return {
        "name": chosen["name"],
        "post_count": f"Visible From {chosen['detail']}",
        "trending_since": when_label,
        "link": chosen["link"],
        "category": "Sky Watch",
        "sort_at": chosen["when"].isoformat(),
    }


def _moon_milestone_cards(now_utc):
    known_new = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
    synodic = 29.530588853
    phase = (((now_utc - known_new).total_seconds() / 86400.0) / synodic) % 1.0
    cards = []
    for name, target, link in (
        ("Next Full Moon", 0.5, "https://moon.nasa.gov/moon-in-motion/moon-phases/"),
        ("Next New Moon", 1.0, "https://moon.nasa.gov/moon-in-motion/moon-phases/"),
    ):
        days = ((target - phase) % 1.0) * synodic
        when = now_utc + timedelta(days=days)
        whole = int(round(days))
        detail = "Tonight" if whole == 0 else ("In 1 Day" if whole == 1 else f"In {whole} Days")
        date_label = when.astimezone(COPENHAGEN_TZ).strftime("%a %b %d").replace(" 0", " ")
        cards.append({
            "name": name,
            "post_count": f"{name.replace('Next ', '')} {detail}",
            "trending_since": f"On {date_label}",
            "link": link,
            "category": "Sky Watch",
            "sort_at": when.isoformat(),
        })
    return cards


def fetch_sky_watch(limit=5):
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

    for backup in _moon_milestone_cards(now_utc):
        if len(cards) >= limit:
            break
        cards.append(backup)

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


def scripture_gateway_url(ref, version="ESV"):
    query = quote((ref or "").strip(), safe="")
    if not query:
        return ""
    return f"https://www.biblegateway.com/passage/?search={query}&version={version}"


def _is_esv_scripture(verse):
    if not isinstance(verse, dict):
        return False
    translation = str(verse.get("translation") or "").strip().lower()
    label = str(verse.get("translation_label") or "").strip().lower()
    return translation == "esv" or "english standard" in label


def _scripture_payload(ref, book="", text="", translation="Esv"):
    ref = str(ref or "").strip()
    source = SCRIPTURE_TRANSLATIONS.get(translation) or SCRIPTURE_TRANSLATIONS["Esv"]
    payload = {
        "text": str(text or "").strip(),
        "focus": ref,
        "ref": ref,
        "book": str(book or "").strip(),
        "translation": translation,
        "translation_label": source["name"],
        "source": {"name": source["name"], "url": source["url"]},
    }
    link = scripture_gateway_url(ref, version=source["gateway"])
    if link:
        payload["link"] = link
    return payload


def _clean_esv_passage(text):
    cleaned = str(text or "")
    cleaned = re.sub(r"\[[0-9]+\]", "", cleaned)
    cleaned = cleaned.replace("(ESV)", "")
    return WHITESPACE_RE.sub(" ", cleaned).strip(" \n\t\"'")


def _fetch_esv_text(ref):
    if not ESV_API_KEY or not ref:
        return None
    payload = fetch_json(
        ESV_API_URL,
        params={
            "q": ref,
            "include-passage-references": "false",
            "include-verse-numbers": "false",
            "include-first-verse-numbers": "false",
            "include-footnotes": "false",
            "include-footnote-body": "false",
            "include-headings": "false",
            "include-short-copyright": "false",
            "include-copyright": "false",
            "include-selahs": "false",
            "indent-poetry": "false",
            "indent-paragraphs": "0",
        },
        extra_headers={"Authorization": f"Token {ESV_API_KEY}"},
        label=f"ESV {ref}",
    )
    if not isinstance(payload, dict):
        return None
    passages = payload.get("passages")
    if not isinstance(passages, list) or not passages:
        return None
    return _clean_esv_passage(passages[0]) or None


def _resolve_scripture_text(verse):
    ref = str((verse or {}).get("ref") or "").strip()
    book = str((verse or {}).get("book") or "").strip()
    esv_text = _fetch_esv_text(ref)
    if esv_text:
        return _scripture_payload(ref, book=book, text=esv_text, translation="Esv")
    kjv_text = str((verse or {}).get("text") or "").strip()
    if kjv_text:
        return _scripture_payload(ref, book=book, text=kjv_text, translation="Kjv")
    return None


def _load_scripture_verses():
    try:
        with open(SCRIPTURE_VERSES_PATH, encoding="utf-8") as file:
            verses = json.load(file)
    except Exception as exc:
        logger.warning("Could not load scripture verses: %s", exc)
        return list(SCRIPTURE_FALLBACK)

    cleaned = []
    for item in verses:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            ref, text = str(item[0] or "").strip(), str(item[1] or "").strip()
            book = ref.split()[0] if ref else ""
        elif isinstance(item, dict):
            ref = str(item.get("ref") or "").strip()
            text = str(item.get("text") or "").strip()
            book = str(item.get("book") or "").strip() or (ref.split()[0] if ref else "")
        else:
            continue
        if ref:
            cleaned.append({"ref": ref, "book": book, "text": text})
    return cleaned or list(SCRIPTURE_FALLBACK)


def _load_scripture_history():
    try:
        with open(SCRIPTURE_HISTORY_PATH, encoding="utf-8") as file:
            data = json.load(file)
        if isinstance(data, dict):
            return data
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning("Could not load scripture history: %s", exc)
    return {}


def _save_scripture_history(history):
    os.makedirs(os.path.dirname(SCRIPTURE_HISTORY_PATH), exist_ok=True)
    with open(SCRIPTURE_HISTORY_PATH, "w", encoding="utf-8") as file:
        json.dump(history, file, ensure_ascii=False, indent=2)
        file.write("\n")


def pick_scripture(seed_date=None):
    today = seed_date or date.today()
    today_iso = today.isoformat() if hasattr(today, "isoformat") else str(today)
    verses = _load_scripture_verses()
    by_ref = {verse["ref"]: verse for verse in verses}
    history = _load_scripture_history()
    current = history.get("current") if isinstance(history.get("current"), dict) else {}
    used = history.get("used_refs")
    used_refs = [ref for ref in used if isinstance(ref, str)] if isinstance(used, list) else []
    used_set = set(used_refs)

    if (
        current.get("date") == today_iso
        and current.get("ref")
        and current.get("text")
        and (_is_esv_scripture(current) or not ESV_API_KEY)
    ):
        logger.info("Reusing today's scripture: %s", current.get("ref"))
        return _scripture_payload(
            current.get("ref"),
            book=current.get("book") or "",
            text=current.get("text") or "",
            translation=current.get("translation") or "Esv",
        )

    chosen = None
    if current.get("date") == today_iso and current.get("ref") in by_ref:
        chosen = by_ref[current["ref"]]
        logger.info("Refreshing today's scripture text: %s", chosen["ref"])
    else:
        unused = [verse for verse in verses if verse["ref"] not in used_set]
        if not unused:
            logger.info("Scripture pool exhausted (%s verses); starting a new cycle.", len(verses))
            last_ref = current.get("ref")
            unused = [verse for verse in verses if verse["ref"] != last_ref] or list(verses)
            used_refs = []
            used_set = set()
        chosen = random.choice(unused)

    payload = _resolve_scripture_text(chosen)
    if payload and payload.get("text") and payload.get("ref") not in used_set:
        used_refs.append(payload["ref"])

    next_history = {
        "used_refs": used_refs,
        "current": {
            "date": today_iso,
            **(payload or _scripture_payload(chosen["ref"], book=chosen.get("book") or "")),
        },
    }
    try:
        _save_scripture_history(next_history)
    except Exception as exc:
        logger.warning("Could not save scripture history: %s", exc)
    if not payload or not payload.get("text"):
        logger.warning("Scripture text unavailable for %s; hiding scripture panel.", chosen["ref"])
        return None
    remaining = max(0, len(verses) - len(used_refs))
    logger.info(
        "Selected scripture %s (%s, %s remaining in cycle)",
        chosen["ref"],
        payload.get("translation_label") or "English Standard Version",
        remaining,
    )
    return payload


def render_html(data):
    env = Environment(
        loader=FileSystemLoader(os.path.dirname(__file__) or "."),
        autoescape=True,
    )
    template = env.get_template("brevity_template.html")
    return template.render(**data)


def write_site_html(data, path=SITE_HTML_PATH):
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
    today = today or copenhagen_now().date()
    weather = fetch_weather()
    stocks = fetch_stocks()
    space_news = fetch_space_news()
    copenhagen = fetch_copenhagen_events()
    south_africa = fetch_south_africa_news()
    x_trending = fetch_x_trending(limit=5)
    sky_watch = fetch_sky_watch(limit=5)
    scripture = pick_scripture(today)

    day_of_year = today.timetuple().tm_yday
    days_in_year = 366 if calendar.isleap(today.year) else 365
    year_percent = (day_of_year / days_in_year) * 100
    generated = copenhagen_now()

    return {
        "date": today.strftime("%A, %B %d").replace(" 0", " "),
        "iso_date": today.isoformat(),
        "generated_at": generated.strftime("%Y-%m-%d %H:%M %Z"),
        "generated_iso": generated.isoformat(timespec="seconds"),
        "generated_clock": format_clock_12(generated.strftime("%H:%M")),
        "year_percent": year_percent,
        "day_of_year": day_of_year,
        "days_in_year": days_in_year,
        "weather": weather,
        "stocks": stocks,
        "space_news": space_news,
        "copenhagen": copenhagen,
        "south_africa": south_africa,
        "scripture": scripture,
        "x_trending": x_trending,
        "sky_watch": sky_watch,
        "github_repo": GITHUB_REPO,
        "github_workflow": GITHUB_WORKFLOW,
    }


def write_brief_data(data, path=BRIEF_DATA_PATH):
    with open(path, "w", encoding="utf-8") as file:
        json.dump(json_safe(data), file, ensure_ascii=False, indent=1)
        file.write("\n")
    logger.info("Brief data written to %s", path)


def load_brief_data(path=BRIEF_DATA_PATH):
    with open(path, encoding="utf-8") as file:
        data = json.load(file)
    for key in NEWS_KEYS:
        for item in data.get(key) or []:
            if item.get("ai_summary"):
                item["headline"] = stylize_keywords(item["ai_summary"])
    return data


def main():
    if "--render-only" in sys.argv[1:]:
        logger.info("Rendering %s from %s (no API calls)...", SITE_HTML_PATH, BRIEF_DATA_PATH)
        write_site_html(load_brief_data())
        return

    logger.info("Starting Brevity generation...")
    if XAI_AVAILABLE:
        logger.info("Using xAI model: %s", XAI_MODEL)
    else:
        logger.warning("XAI_API_KEY missing; news will not be summarised.")
    if ESV_API_KEY:
        logger.info("Scripture will use the English Standard Version API.")
    else:
        logger.warning("ESV_API_KEY missing; scripture will use the bundled King James text.")

    data = build_brief_data()

    write_site_html(data)
    try:
        write_brief_data(data)
    except Exception as exc:
        logger.warning("Could not save brief data: %s", exc)

    pdf_path = None
    if SEND_TO_SLACK:
        if GENERATE_PDF:
            pdf_path = generate_pdf(data)
        if pdf_path:
            send_to_slack(pdf_path)
        else:
            logger.warning("SEND_TO_SLACK enabled but no PDF was generated.")

    logger.info(
        "xAI totals: %s calls, %s in / %s out tokens, %s posts, %s profiles",
        XAI_USAGE["calls"], XAI_USAGE["input_tokens"], XAI_USAGE["output_tokens"],
        XAI_USAGE["x_posts_fetched"], XAI_USAGE["x_users_fetched"],
    )
    logger.info("Done.")


if __name__ == "__main__":
    main()
