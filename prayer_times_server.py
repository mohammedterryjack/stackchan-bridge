"""Compact Islamic prayer times MCP server via Aladhan (no API key).

Default location: London, UK. Default method: Moonsighting Committee Worldwide
(Aladhan method 15) — commonly used for UK timings.
"""
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from fastmcp import FastMCP

mcp = FastMCP("PrayerTimes")

TIMEOUT = httpx.Timeout(connect=3, read=5, write=3, pool=3)
TZ = ZoneInfo("Europe/London")
DEFAULT_CITY = "London"
DEFAULT_COUNTRY = "UK"
# Moonsighting Committee Worldwide — common for UK; Aladhan id 15
DEFAULT_METHOD = 15
DEFAULT_METHOD_NAME = "Moonsighting Committee Worldwide"
PRAYER_ORDER = ("Fajr", "Sunrise", "Dhuhr", "Asr", "Maghrib", "Isha")
# next_prayer skips Sunrise (not a salah)
SALAH_ORDER = ("Fajr", "Dhuhr", "Asr", "Maghrib", "Isha")


def _parse_hhmm(s: str) -> tuple[int, int]:
    # Aladhan may append timezone notes like "05:12 (BST)" — take first token
    token = (s or "").strip().split()[0]
    hh, mm = token.split(":")[:2]
    return int(hh), int(mm)


def _fetch_timings(date_str: str, city: str, country: str, method: int) -> dict:
    """Fetch timings for DD-MM-YYYY. Fail loudly on non-200 / missing data."""
    url = f"https://api.aladhan.com/v1/timingsByCity/{date_str}"
    params = {"city": city, "country": country, "method": method}
    resp = httpx.get(url, params=params, timeout=TIMEOUT, follow_redirects=True)
    resp.raise_for_status()
    body = resp.json()
    if body.get("code") != 200:
        raise RuntimeError(f"Aladhan error code={body.get('code')} status={body.get('status')}")
    data = body.get("data") or {}
    timings = data.get("timings")
    if not timings:
        raise RuntimeError("Aladhan response missing timings")
    meta = data.get("meta") or {}
    method_meta = meta.get("method") or {}
    return {
        "timings": timings,
        "date": (data.get("date") or {}).get("readable") or date_str,
        "hijri": ((data.get("date") or {}).get("hijri") or {}).get("date") or "",
        "method_name": method_meta.get("name") or DEFAULT_METHOD_NAME,
        "method_id": method_meta.get("id") if method_meta.get("id") is not None else method,
        "timezone": meta.get("timezone") or "Europe/London",
    }


def _today_str(now: datetime | None = None) -> str:
    now = now or datetime.now(TZ)
    return now.strftime("%d-%m-%Y")


@mcp.tool()
def prayer_times(
    date: str = "today",
    city: str = DEFAULT_CITY,
    country: str = DEFAULT_COUNTRY,
    method: int = DEFAULT_METHOD,
) -> str:
    """All prayer times for a day. Defaults: today, London UK. date: today, tomorrow or YYYY-MM-DD."""
    d = (date or "").strip().lower()
    if d in ("", "today"):
        date_str = _today_str()
    elif d == "tomorrow":
        date_str = (datetime.now(TZ) + timedelta(days=1)).strftime("%d-%m-%Y")
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
        date_str = datetime.strptime(d, "%Y-%m-%d").strftime("%d-%m-%Y")
    else:
        # validate DD-MM-YYYY
        datetime.strptime(date, "%d-%m-%Y")
        date_str = date
    info = _fetch_timings(date_str, city, country, method)
    t = info["timings"]
    parts = []
    for name in PRAYER_ORDER:
        if name not in t:
            raise RuntimeError(f"Aladhan missing timing for {name}")
        hh, mm = _parse_hhmm(t[name])
        parts.append(f"{name} {hh:02d}:{mm:02d}")
    return f"{city}, {info['date']}: " + ", ".join(parts) + "."


@mcp.tool()
def next_prayer(
    city: str = DEFAULT_CITY,
    country: str = DEFAULT_COUNTRY,
    method: int = DEFAULT_METHOD,
) -> str:
    """When the next prayer is and how long until it. Default London UK."""
    info = _fetch_timings(_today_str(), city, country, method)
    tz = ZoneInfo(info["timezone"])
    now = datetime.now(tz)
    if _today_str(now) != _today_str():
        info = _fetch_timings(_today_str(now), city, country, method)
    tzname = info["timezone"]
    t = info["timings"]
    for name in SALAH_ORDER:
        if name not in t:
            raise RuntimeError(f"Aladhan missing timing for {name}")
        hh, mm = _parse_hhmm(t[name])
        when = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if when > now:
            mins = int((when - now).total_seconds() // 60)
            return (
                f"Next: {name} at {hh:02d}:{mm:02d} {tzname} "
                f"(in {mins}m) — {city}, {country}; {info['method_name']}"
            )
    tomorrow = now + timedelta(days=1)
    info2 = _fetch_timings(tomorrow.strftime("%d-%m-%Y"), city, country, method)
    t2 = info2["timings"]
    if "Fajr" not in t2:
        raise RuntimeError("Aladhan missing timing for Fajr")
    hh, mm = _parse_hhmm(t2["Fajr"])
    when = tomorrow.replace(hour=hh, minute=mm, second=0, microsecond=0)
    mins = int((when - now).total_seconds() // 60)
    return (
        f"Next: Fajr at {hh:02d}:{mm:02d} {tzname} tomorrow "
        f"(in {mins}m) — {city}, {country}; {info2['method_name']}"
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
