"""Web search MCP: DuckDuckGo HTML + page read. Spoken plain text; fail loudly.

DuckDuckGo shows a bot check to the whole IP when scripted searches come in bursts, and keeps it up longer if
retried. So: a 5 minute in-memory cache, at least 2 s between DuckDuckGo requests, and on a bot check (202 /
anomaly page) or a dropped connection (SSL EOF etc.) no retry: a 15 minute cooldown during which every search
fails at once with a spoken message and sends nothing. Cooldown and last-request time live in a small file in the
container's /tmp so every process in the container (live bridge and test runs) respects them. No other engine."""
from __future__ import annotations

import html as html_lib
import json
import math
import os
import re
import time
from collections import OrderedDict
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from app import mcp

from mcp_tool_log import get_logger

TIMEOUT = httpx.Timeout(connect=2.0, read=5.0, write=2.0, pool=2.0)
MAX_RESULTS = 8
DEFAULT_RESULTS = 5
DEFAULT_FETCH = 2500
MAX_FETCH = 4000
UA = "stackchan-web/1.0"
DDG_URL = "https://html.duckduckgo.com/html/"
CACHE_TTL_S = 300
CACHE_MAX = 64
MIN_GAP_S = 2.0
COOLDOWN_S = 15 * 60
STATE_FILE = os.environ.get("DDG_STATE_FILE", "/tmp/stackchan-ddg.json")

log = get_logger("WEB_SEARCH")
_cache: OrderedDict[tuple, tuple[float, object]] = OrderedDict()

_last_urls: list[str] = []  # in-memory only: URLs from the most recent web_search


def _client() -> httpx.Client:
    return httpx.Client(
        timeout=TIMEOUT,
        headers={"User-Agent": UA, "Accept-Language": "en-GB,en;q=0.9"},
        follow_redirects=True,
    )


def _cache_get(key: tuple):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL_S:
        _cache.move_to_end(key)
        return hit[1]
    _cache.pop(key, None)
    return None


def _cache_put(key: tuple, value) -> None:
    _cache[key] = (time.time(), value)
    _cache.move_to_end(key)
    while len(_cache) > CACHE_MAX:
        _cache.popitem(last=False)


def _state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _save_state(st: dict) -> None:
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, STATE_FILE)


def _limited_msg(remaining_s: float) -> str:
    n = max(1, math.ceil(remaining_s / 60))
    return f"DuckDuckGo is rate-limiting us, try again in {n} minute{'s' if n != 1 else ''}."


def _start_cooldown(st: dict, why: str) -> str:
    st["until"] = time.time() + COOLDOWN_S
    st["why"] = why  # kept in the state file so a cooldown can be diagnosed later
    _save_state(st)
    log.warning(f"DuckDuckGo cooldown start ({why}); no searches for {COOLDOWN_S // 60} min")
    return _limited_msg(COOLDOWN_S)


def _ddg_post(q: str) -> str:
    """One DuckDuckGo request, respecting cooldown and the minimum gap; bot check or dropped connection -> cooldown."""
    st = _state()
    now = time.time()
    until = float(st.get("until") or 0)
    if until > now:
        raise RuntimeError(_limited_msg(until - now))
    if until:
        st.pop("until")
        st.pop("why", None)
        log.info("DuckDuckGo cooldown end; searching again")
    wait = float(st.get("last") or 0) + MIN_GAP_S - now
    if wait > 0:
        time.sleep(wait)
    st["last"] = time.time()
    _save_state(st)
    try:
        with _client() as client:
            r = client.post(DDG_URL, data={"q": q, "b": "", "kl": "uk-en"})
    except httpx.TimeoutException:
        raise
    except httpx.TransportError as e:
        raise RuntimeError(_start_cooldown(st, f"connection dropped: {type(e).__name__}: {str(e)[:100]}")) from e
    body = r.text
    if r.status_code == 202 or (r.status_code == 200 and "anomaly" in body.lower() and "result__a" not in body):
        raise RuntimeError(_start_cooldown(st, f"bot check, HTTP {r.status_code}"))
    r.raise_for_status()
    return body


def _clean(text: str) -> str:
    t = html_lib.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", t).strip()


def _unwrap_ddg(href: str) -> str:
    # DDG redirect links: /l/?uddg=<url>
    if "uddg=" in href:
        qs = parse_qs(urlparse(href).query)
        if "uddg" in qs:
            return unquote(qs["uddg"][0])
    if href.startswith("//"):
        return "https:" + href
    return href


@mcp.tool()
def web_search(query: str, max_results: int = DEFAULT_RESULTS) -> str:
    """Search the web for facts and questions no other tool covers."""
    q = (query or "").strip()
    if not q:
        raise ValueError("query required")
    if not 1 <= max_results <= MAX_RESULTS:
        raise ValueError(f"max_results must be 1..{MAX_RESULTS}")
    key = ("search", re.sub(r"\s+", " ", q.lower()), max_results)
    hit = _cache_get(key)
    if hit is not None:
        _last_urls[:] = hit[1]
        return hit[0]
    body = _ddg_post(q)
    # Parse result blocks
    results = []
    # Each result: <a class="result__a" href="...">title</a> ... <a class="result__snippet"> or <td class="result-snippet">
    for m in re.finditer(
        r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>(.*?)<(?:a class="result__snippet"|td class="result-snippet")[^>]*>(.*?)</',
        body,
        flags=re.I | re.S,
    ):
        url = _unwrap_ddg(html_lib.unescape(m.group(1)))
        title = _clean(m.group(2))
        snippet = _clean(m.group(4))
        if not title or not url or url.startswith("https://duckduckgo.com"):
            continue
        results.append((title, url, snippet))
        if len(results) >= max_results:
            break
    if not results:
        # alternate markup
        for m in re.finditer(
            r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            body,
            flags=re.I | re.S,
        ):
            url = _unwrap_ddg(html_lib.unescape(m.group(1)))
            title = _clean(m.group(2))
            if title and url and "duckduckgo.com" not in url:
                results.append((title, url, ""))
            if len(results) >= max_results:
                break
    if not results:
        raise RuntimeError(f"DuckDuckGo returned no results for {q!r} (blocked or empty)")
    _last_urls[:] = [u for _, u, _ in results]
    lines = []
    for i, (title, url, snip) in enumerate(results, 1):
        if len(snip) > 160:
            snip = snip[:160].rsplit(" ", 1)[0] + "..."
        lines.append(f"{title} – {snip} (result {i})" if snip else f"{title} (result {i})")
    out = "\n".join(lines)
    _cache_put(key, (out, list(_last_urls)))
    return out


@mcp.tool()
def web_read(url: str, max_length: int = DEFAULT_FETCH) -> str:
    """Read a web page in full: a web_search result number, a web address or a site name."""
    u = str(url or "").strip()
    m = re.fullmatch(r"(?:result\s*)?(\d{1,2})", u, re.I)
    if m:
        n = int(m.group(1))
        if not _last_urls:
            raise ValueError("no recent web_search; search first or give a web address")
        if not 1 <= n <= len(_last_urls):
            raise ValueError(f"result must be 1..{len(_last_urls)}")
        u = _last_urls[n - 1]
    if re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}(/\S*)?", u, re.I):
        u = "https://" + u  # spoken bare domain, e.g. simonwillison.net
    if not u.startswith(("http://", "https://")):
        raise ValueError("url must be http(s)")
    if not 200 <= max_length <= MAX_FETCH:
        raise ValueError(f"max_length must be 200..{MAX_FETCH}")
    key = ("read", u, max_length)
    hit = _cache_get(key)
    if hit is not None:
        return hit
    with _client() as client:
        r = client.get(u)
        r.raise_for_status()
        ctype = (r.headers.get("content-type") or "").lower()
        if "html" not in ctype and "text" not in ctype and "json" not in ctype:
            raise RuntimeError(f"unsupported content-type: {ctype or 'unknown'}")
        body = re.sub(r"(?is)<(script|style|noscript|svg|head)\b.*?</\1>", " ", r.text)
        text = _clean(body)
    if len(text) > max_length:
        text = text[:max_length] + "…"
    if not text:
        raise RuntimeError("empty body")
    _cache_put(key, text)
    return text
