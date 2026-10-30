"""Maps MCP: OpenStreetMap place search, nearby places (Nominatim) and road distance (OSRM). Keyless, nothing written to disk."""
from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict

import httpx
from fastmcp import FastMCP

NOMINATIM = "https://nominatim.openstreetmap.org"
OSRM = "https://router.project-osrm.org/route/v1/driving/{a};{b}"
UA = {"User-Agent": "stackchan-bridge/1.0 (github.com/mohammedterryjack/stackchan-bridge)", "Accept-Language": "en"}
HTTP_TIMEOUT_S = 5.0
MAX_LIMIT = 5
CACHE_SIZE = 64

# Spoken kind -> Nominatim search phrase (searched inside a box around the place).
KINDS = {k: k for k in ("cafe", "restaurant", "fast food", "pub", "mosque", "church", "pharmacy", "hospital",
                        "atm", "bank", "parking", "toilets", "library", "school", "supermarket", "bakery",
                        "park", "gym", "hotel", "museum", "bus stop")}
KINDS.update({"fuel": "petrol station", "station": "railway station"})
ALIASES = {"coffee": "cafe", "masjid": "mosque", "chemist": "pharmacy", "petrol": "fuel",
           "gas station": "fuel", "petrol station": "fuel", "train station": "station",
           "railway station": "station", "toilet": "toilets", "shop": "supermarket"}
RADIUS_KM = 3.0

mcp = FastMCP("Maps")
_nom_lock = threading.Lock()
_nom_last = 0.0
_cache_lock = threading.Lock()
_cache: "OrderedDict[tuple, list]" = OrderedDict()


def _nominatim(path: str, params: dict) -> list | dict:
    """Nominatim usage policy: max 1 request per second, identifying User-Agent."""
    global _nom_last
    key = (path, tuple(sorted(params.items())))
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    with _nom_lock:
        wait = 1.0 - (time.time() - _nom_last)
        if wait > 0:
            time.sleep(wait)
        try:
            r = httpx.get(f"{NOMINATIM}/{path}", params={**params, "format": "jsonv2"}, headers=UA, timeout=HTTP_TIMEOUT_S)
        finally:
            _nom_last = time.time()
    r.raise_for_status()
    data = r.json()
    with _cache_lock:
        _cache[key] = data
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    return data


def _short(p: dict) -> str:
    a = p.get("address") or {}
    name = p.get("name") or a.get("road") or p.get("display_name", "").split(",")[0]
    road = " ".join(x for x in (a.get("house_number"), a.get("road")) if x)
    area = a.get("suburb") or a.get("neighbourhood") or a.get("village")
    town = a.get("city") or a.get("town") or a.get("county") or a.get("state")
    bits = [b for b in (road if road != name else None, area, town, a.get("country")) if b]
    seen, out = set(), []
    for b in bits:
        if b not in seen and b != name:
            seen.add(b); out.append(b)
    return f"{name}, {', '.join(out)}" if out else name


def _locate(place: str) -> dict:
    place = place.strip()
    if not place:
        raise ValueError("Say which place, e.g. 'Regent's Park London'.")
    res = _nominatim("search", {"q": place, "limit": 1, "addressdetails": 1})
    if not res:
        raise ValueError(f"Could not find '{place}' on the map. Try adding the town or country.")
    return res[0]


def _km(a, b) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (float(a[0]), float(a[1]), float(b[0]), float(b[1])))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def _dist_words(km: float) -> str:
    return f"{round(km * 1000, -1):.0f} metres" if km < 1 else f"{km:.1f} km"


@mcp.tool()
def map_search(query: str, limit: int = 3) -> str:
    """Find a place, address or landmark on the map (OpenStreetMap). Include the town or country, e.g. 'Big Ben London'."""
    q = query.strip()
    if not q:
        raise ValueError("Say what to find, e.g. 'British Museum London'.")
    limit = max(1, min(int(limit), MAX_LIMIT))
    res = _nominatim("search", {"q": q, "limit": limit, "addressdetails": 1})
    if not res:
        raise ValueError(f"Nothing found for '{q}'. Try adding the town or country.")
    lines = [f"{i}. {_short(p)} ({p.get('type', 'place').replace('_', ' ')})" for i, p in enumerate(res, 1)]
    return "\n".join(lines)


@mcp.tool()
def map_nearby(place: str, kind: str, limit: int = 3) -> str:
    """Closest places of one kind near a place, within 3 km. e.g. place='Kings Cross London', kind='mosque'.
    kind: cafe, restaurant, fast food, pub, mosque, church, pharmacy, hospital, atm, bank, fuel, parking,
    toilets, library, school, supermarket, bakery, park, gym, hotel, museum, station, bus stop."""
    k = kind.strip().lower()
    k = ALIASES.get(k, k)
    if k not in KINDS and k.endswith("s") and k[:-1] in KINDS:
        k = k[:-1]
    if k not in KINDS:
        raise ValueError(f"Unknown kind '{kind}'. Use one of: {', '.join(KINDS)}.")
    limit = max(1, min(int(limit), MAX_LIMIT))
    c = _locate(place)
    lat, lon = float(c["lat"]), float(c["lon"])
    dlat = RADIUS_KM / 111.0
    dlon = dlat / max(math.cos(math.radians(lat)), 0.1)
    box = f"{lon - dlon},{lat + dlat},{lon + dlon},{lat - dlat}"
    res = _nominatim("search", {"q": KINDS[k], "viewbox": box, "bounded": 1, "limit": 40, "addressdetails": 1})
    found = []
    for p in res:
        d = _km((lat, lon), (p["lat"], p["lon"]))
        if p.get("name") and d <= RADIUS_KM and p.get("place_id") != c.get("place_id"):
            a = p.get("address") or {}
            street = " ".join(x for x in (a.get("house_number"), a.get("road")) if x)
            found.append((d, p["name"], street))
    if not found:
        raise ValueError(f"No {KINDS[k]} found within {RADIUS_KM:.0f} km of {_short(c)}.")
    found.sort()
    lines = [f"{i}. {n}{', ' + s if s else ''}, {_dist_words(d)} away" for i, (d, n, s) in enumerate(found[:limit], 1)]
    return f"Nearest {KINDS[k]} to {_short(c)}:\n" + "\n".join(lines)


@mcp.tool()
def map_distance(origin: str, destination: str) -> str:
    """Road distance and rough driving time between two places, e.g. origin='Oxford', destination='Cambridge'."""
    a, b = _locate(origin), _locate(destination)
    r = httpx.get(OSRM.format(a=f"{a['lon']},{a['lat']}", b=f"{b['lon']},{b['lat']}"),
                  params={"overview": "false"}, headers=UA, timeout=HTTP_TIMEOUT_S)
    r.raise_for_status()
    j = r.json()
    if j.get("code") != "Ok" or not j.get("routes"):
        raise ValueError(f"No road route between {_short(a)} and {_short(b)}.")
    rt = j["routes"][0]
    straight = _km((a["lat"], a["lon"]), (b["lat"], b["lon"]))
    if rt["distance"] / 1000 < straight * 0.95:
        raise ValueError(f"No road route between {_short(a)} and {_short(b)}; they are {_dist_words(straight)} apart in a straight line.")
    mins = round(rt["duration"] / 60)
    t = f"{mins} minutes" if mins < 60 else f"{mins // 60} hours {mins % 60} minutes"
    return (f"From {_short(a)} to {_short(b)}: {_dist_words(rt['distance'] / 1000)} by road, "
            f"about {t} driving without traffic. Straight line {_dist_words(straight)}.")


if __name__ == "__main__":
    mcp.run()
