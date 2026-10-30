"""Compact Islamic prayer times MCP server via Aladhan (no API key).

No default location: city and country are required. Default method: Moonsighting Committee Worldwide
(Aladhan method 15) — commonly used for UK timings.
"""
import re
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from fastmcp import FastMCP

mcp = FastMCP("PrayerTimes")

TIMEOUT = httpx.Timeout(connect=3, read=5, write=3, pool=3)
NO_LOC = "No location given. Ask the user which city and country they are in."
# Moonsighting Committee Worldwide — common for UK; Aladhan id 15
DEFAULT_METHOD = 15
DEFAULT_METHOD_NAME = "Moonsighting Committee Worldwide"
PRAYER_ORDER = ("Fajr", "Sunrise", "Dhuhr", "Asr", "Maghrib", "Isha")
# next_prayer skips Sunrise (not a salah)
SALAH_ORDER = ("Fajr", "Dhuhr", "Asr", "Maghrib", "Isha")
UA = {"User-Agent": "stackchan-bridge/1.0 (github.com/mohammedterryjack/stackchan-bridge)", "Accept-Language": "en"}
BAD_DATE = "Date must be today, tomorrow, or YYYY-MM-DD."
_geo_lock = threading.Lock()
_geo_last = 0.0
_geo_cache: "OrderedDict[tuple, tuple[float, float]]" = OrderedDict()


def _geocode(city: str, country: str) -> tuple[float, float]:
    """Nominatim lookup: 1 request/second, identifying User-Agent, 64-entry in-memory cache."""
    global _geo_last
    key = (city.lower(), country.lower())
    with _geo_lock:
        if key in _geo_cache:
            _geo_cache.move_to_end(key)
            return _geo_cache[key]
        wait = 1.0 - (time.monotonic() - _geo_last)
        if wait > 0:
            time.sleep(wait)
        try:
            r = httpx.get("https://nominatim.openstreetmap.org/search", params={"q": f"{city}, {country}", "format": "jsonv2", "limit": 1}, headers=UA, timeout=TIMEOUT)
        finally:
            _geo_last = time.monotonic()
        r.raise_for_status()
        hits = r.json()
        if not hits:
            raise ValueError(f"I couldn't find {city}, {country}. Ask the user to check the city name.")
        ll = (float(hits[0]["lat"]), float(hits[0]["lon"]))
        _geo_cache[key] = ll
        if len(_geo_cache) > 64:
            _geo_cache.popitem(last=False)
        return ll


def _parse_hhmm(s: str) -> tuple[int, int]:
    # Aladhan may append timezone notes like "05:12 (BST)" — take first token
    token = (s or "").strip().split()[0]
    hh, mm = token.split(":")[:2]
    return int(hh), int(mm)


def _fetch_timings(date_str: str, city: str, country: str, method: int) -> dict:
    """Fetch timings for DD-MM-YYYY. Fail loudly on non-200 / missing data."""
    lat, lon = _geocode(city, country)
    url = f"https://api.aladhan.com/v1/timings/{date_str}"
    params = {"latitude": lat, "longitude": lon, "method": method}
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
        "timezone": meta["timezone"],
    }


def _need(city: str, country: str) -> tuple[str, str]:
    city, country = (city or "").strip(), (country or "").strip()
    if not city or not country:
        raise ValueError(NO_LOC)
    return city, country


def _local_now(city: str, country: str, method: int) -> datetime:
    """Current time in the city's own timezone (from Aladhan meta)."""
    probe = _fetch_timings(datetime.utcnow().strftime("%d-%m-%Y"), city, country, method)
    return datetime.now(ZoneInfo(probe["timezone"]))


def _parse_date(s: str, fmt: str) -> datetime:
    try:
        return datetime.strptime(s, fmt)
    except ValueError:
        raise ValueError(BAD_DATE) from None


def _today_str(now: datetime) -> str:
    return now.strftime("%d-%m-%Y")


@mcp.tool()
def prayer_times(
    city: str,
    country: str,
    date: str = "today",
    method: int = DEFAULT_METHOD,
) -> str:
    """All prayer times for a day. city and country are required (e.g. Leeds, UK); if the user did not say where, ask which city they are in first. date: today, tomorrow or YYYY-MM-DD (default today)."""
    city, country = _need(city, country)
    d = (date or "").strip().lower()
    if d in ("", "today"):
        date_str = _today_str(_local_now(city, country, method))
    elif d == "tomorrow":
        date_str = (_local_now(city, country, method) + timedelta(days=1)).strftime("%d-%m-%Y")
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
        date_str = _parse_date(d, "%Y-%m-%d").strftime("%d-%m-%Y")
    else:
        # validate DD-MM-YYYY
        _parse_date(date.strip(), "%d-%m-%Y")
        date = date.strip()
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
    city: str,
    country: str,
    method: int = DEFAULT_METHOD,
) -> str:
    """When the next prayer is and how long until it. city and country are required (e.g. Leeds, UK); if the user did not say where, ask which city they are in first."""
    city, country = _need(city, country)
    now = _local_now(city, country, method)
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


@mcp.tool()
def prayer_alarms(city: str, country: str, prayer: str, method: int = DEFAULT_METHOD) -> str:
    """Seconds until a prayer, for setting a prayer reminder/alarm. city and country required (e.g. Leeds, UK); if the user did not say where, ask which city they are in first. prayer: fajr, dhuhr, asr, maghrib, isha, or 1-5 (1 Fajr, 2 Dhuhr, 3 Asr, 4 Maghrib, 5 Isha), or all. Then call self.robot.create_reminder once per line with that duration_seconds, message and repeat=false. Reminders are one-off and lost if the robot restarts."""
    city, country = _need(city, country)
    p = (prayer or "").strip().lower()
    names = {n.lower(): n for n in SALAH_ORDER}
    names.update({"zuhr": "Dhuhr", "dhuhur": "Dhuhr", "duhr": "Dhuhr", "asar": "Asr", "esha": "Isha", "isya": "Isha", "subh": "Fajr"})
    if p == "all":
        wanted = list(SALAH_ORDER)
    elif p in ("1", "2", "3", "4", "5"):
        wanted = [SALAH_ORDER[int(p) - 1]]
    elif p in names:
        wanted = [names[p]]
    else:
        raise ValueError(f"Unknown prayer '{prayer}'. Use fajr, dhuhr, asr, maghrib, isha, a number 1-5, or all.")
    now = _local_now(city, country, method)
    tz = now.tzinfo
    days = [_fetch_timings(_today_str(now + timedelta(days=i)), city, country, method)["timings"] for i in (0, 1)]
    rows = []
    for name in wanted:
        for i, t in enumerate(days):
            if name not in t:
                raise RuntimeError(f"Aladhan missing timing for {name}")
            hh, mm = _parse_hhmm(t[name])
            d = (now + timedelta(days=i)).date()
            when = datetime(d.year, d.month, d.day, hh, mm, tzinfo=tz)
            if when > now:
                secs = int((when - now).total_seconds())
                if secs > 86400:
                    raise RuntimeError(f"{name} is {secs}s away, over the device's 86400s reminder limit")
                rows.append((secs, name, f"{hh:02d}:{mm:02d}", i == 1))
                break
    rows.sort()
    lines = []
    for secs, name, hm, tom in rows:
        h, m = divmod(secs // 60, 60)
        lines.append(f'{name} {hm}{" tomorrow" if tom else ""}, in {h}h {m}m: duration_seconds={secs} message="Time for {name} prayer" repeat=false')
    lines.append(f"Times for {city}, {country} ({tz.key}). Call self.robot.create_reminder once for each line above.")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="stdio")
