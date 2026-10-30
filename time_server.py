"""Time MCP: timezone/city is always required; no default location."""
from __future__ import annotations

from datetime import datetime
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from fastmcp import FastMCP

NO_LOC = "No location given. Ask the user which city they are in."
mcp = FastMCP("Time")


def _tz(name: str | None) -> ZoneInfo:
    n = (name or "").strip()
    if not n:
        raise ValueError(NO_LOC)
    try:
        return ZoneInfo(n)
    except (ZoneInfoNotFoundError, ValueError):
        pass
    # spoken city names: "Tokyo", "new york" -> Asia/Tokyo, America/New_York
    city = n.replace(" ", "_").lower()
    hits = sorted(z for z in available_timezones() if z.split("/")[-1].lower() == city)
    if len(hits) == 1:
        return ZoneInfo(hits[0])
    if len(hits) > 1:
        raise ValueError(f"ambiguous city {n!r}: {', '.join(hits)}")
    raise ValueError(f"unknown timezone or city {n!r}; use an IANA name like Asia/Tokyo")


@mcp.tool()
def clock_time(timezone: str) -> str:
    """Current time in a city or timezone. timezone: required, city or IANA name, e.g. Tokyo or Asia/Tokyo. If the user did not say where, ask which city they are in first. Use for 'what time is it', not calendars."""
    z = _tz(timezone)
    now = datetime.now(z)
    return f"{now.strftime('%A %d %B %Y, %H:%M')} ({z.key})"


@mcp.tool()
def convert_time(
    time: str,
    source_timezone: str,
    target_timezone: str,
) -> str:
    """Convert a time (15:00, 3pm, 3:30 pm) between timezones or cities. source_timezone and target_timezone are both required (city or IANA name). If the user did not say one, ask them which city they mean. Not for dates beyond today."""
    src = _tz(source_timezone)
    tgt = _tz(target_timezone)
    m = re.fullmatch(r"\s*(\d{1,2})(?:[:.](\d{2}))?\s*([ap]\.?m\.?)?\s*", time.lower())
    if not m:
        raise ValueError("time must be like 15:00, 3pm or 3:30 pm")
    hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ampm:
        if not 1 <= hour <= 12:
            raise ValueError("12-hour time needs hour 1-12")
        hour = hour % 12 + (12 if ampm.startswith("p") else 0)
    elif m.group(2) is None:
        raise ValueError("give minutes (15:00) or am/pm (3pm)")
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("time must be valid 24-hour HH:MM")
    today = datetime.now(src).date()
    dt = datetime(today.year, today.month, today.day, hour, minute, tzinfo=src)
    out = dt.astimezone(tgt)
    return f"{dt.strftime('%H:%M %a')} {src.key} = {out.strftime('%H:%M %a %d %b')} {tgt.key}"


if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
