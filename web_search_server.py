"""Web search MCP: DuckDuckGo HTML + page read. Spoken plain text; fail loudly."""
from __future__ import annotations

import html as html_lib
import re
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from fastmcp import FastMCP

TIMEOUT = httpx.Timeout(connect=2.0, read=5.0, write=2.0, pool=2.0)
MAX_RESULTS = 8
DEFAULT_RESULTS = 5
DEFAULT_FETCH = 2500
MAX_FETCH = 4000
UA = "stackchan-web/1.0"

mcp = FastMCP("WebSearch")
_last_urls: list[str] = []  # in-memory only: URLs from the most recent web_search


def _client() -> httpx.Client:
    return httpx.Client(
        timeout=TIMEOUT,
        headers={"User-Agent": UA, "Accept-Language": "en-GB,en;q=0.9"},
        follow_redirects=True,
    )


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
    """Use for general questions and facts no other tool covers, e.g. 'search the web for...'. max_results 1-8 (default 5). Returns one line per result: title - snippet (result N); web_read with N reads one."""
    q = (query or "").strip()
    if not q:
        raise ValueError("query required")
    if not 1 <= max_results <= MAX_RESULTS:
        raise ValueError(f"max_results must be 1..{MAX_RESULTS}")
    with _client() as client:
        r = client.post(
            "https://html.duckduckgo.com/html/",
            data={"q": q, "b": "", "kl": "uk-en"},
        )
        r.raise_for_status()
        body = r.text
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
    return "\n".join(lines)


@mcp.tool()
def web_read(url: str, max_length: int = DEFAULT_FETCH) -> str:
    """Use to read a web page, or a web_search result in full. url: result number from the last web_search (e.g. 2), a web address, or a site name like wikipedia.org. Returns the page as plain text."""
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
    return text


if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
