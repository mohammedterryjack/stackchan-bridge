"""Compact news MCP server: Google News / Al Jazeera / DW / Real News headlines, Simon Willison's blog, security news feeds."""
import html
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET

import httpx
from fastmcp import FastMCP

mcp = FastMCP("News")

TOP_FEEDS = [
    ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
    ("DW", "https://rss.dw.com/rdf/rss-en-all"),
    ("Real News", "https://therealnews.com/feed"),
]
GOOGLE_SEARCH = "https://news.google.com/rss/search?q={q}&hl=en-GB&gl=GB&ceid=GB:en"
SIMON_FEED = "https://simonwillison.net/atom/everything/"
SECURITY_FEEDS = {
    "krebs": ("Krebs on Security", "https://krebsonsecurity.com/feed/"),
    "schneier": ("Schneier on Security", "https://www.schneier.com/feed/atom/"),
    "lwn": ("LWN.net", "https://lwn.net/headlines/rss"),
    "2600": ("2600: The Hacker Quarterly", "https://www.2600.com/rss.xml"),
}
ATOM = "{http://www.w3.org/2005/Atom}"
RSS1 = "{http://purl.org/rss/1.0/}"
MAX_LIMIT = 10
SUMMARY_CHARS = 100
TIMEOUT = httpx.Timeout(connect=3, read=5, write=3, pool=3)
CACHE_TTL = 300  # seconds; successful fetches only, never served past TTL
_cache: dict[str, tuple[float, ET.Element]] = {}


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html.unescape(text or ""))
    text = re.sub(r"https?://\S+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _check_limit(limit: int) -> None:
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")


def _fetch(url: str) -> ET.Element:
    hit = _cache.get(url)
    if hit and time.monotonic() - hit[0] < CACHE_TTL:
        return hit[1]
    resp = httpx.get(url, timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": "stackchan-news/1.0"})
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    _cache[url] = (time.monotonic(), root)
    return root


def _entries(root: ET.Element) -> list[dict]:
    """Normalise RSS <item> or Atom <entry> into dicts. Raises if the feed has none."""
    out = []
    for it in root.findall("./channel/item"):
        out.append({"title": _clean(it.findtext("title")), "date": _clean(it.findtext("pubDate"))[:22],
                    "link": (it.findtext("link") or "").strip(), "summary": _clean(it.findtext("description"))})
    for it in root.findall(f"{RSS1}item"):  # RSS 1.0 / RDF (e.g. DW)
        out.append({"title": _clean(it.findtext(f"{RSS1}title")), "date": "",
                    "link": (it.findtext(f"{RSS1}link") or "").strip(), "summary": _clean(it.findtext(f"{RSS1}description"))})
    for e in root.findall(f"{ATOM}entry"):
        link_el = e.find(f"{ATOM}link[@rel='alternate']")
        if link_el is None:
            link_el = e.find(f"{ATOM}link")
        if link_el is None:
            raise RuntimeError(f"Atom entry {e.findtext(f'{ATOM}title')!r} has no link")
        out.append({"title": _clean(e.findtext(f"{ATOM}title")),
                    "date": (e.findtext(f"{ATOM}published") or e.findtext(f"{ATOM}updated") or "")[:10],
                    "link": link_el.get("href"),
                    "summary": _clean(e.findtext(f"{ATOM}summary") or e.findtext(f"{ATOM}content"))})
    if not out:
        raise RuntimeError("Feed contained no items")
    return out


def _format(heading: str, items: list[dict], limit: int, summary: bool) -> str:
    lines = [heading]
    for i, it in enumerate(items[:limit], 1):
        line = f"{i}. {it['title']}"
        if summary and it["summary"]:
            sm = it["summary"]
            line += f" - {sm if len(sm) <= SUMMARY_CHARS else sm[:SUMMARY_CHARS].rsplit(' ', 1)[0] + '...'}"
        lines.append(line)
    return "\n".join(lines)


@mcp.tool()
def news_headlines(topic: str | None = None, limit: int = 5) -> str:
    """Use when the user asks for the news, headlines or what is happening in the world or on a topic. topic: optional search term (Google News); omit for top world headlines from Al Jazeera, DW and Real News. limit 1-10 (default 5). Returns one headline per line."""
    _check_limit(limit)
    topic = (topic or "").strip()
    if topic:
        items = _entries(_fetch(GOOGLE_SEARCH.format(q=urllib.parse.quote(topic))))
        # Google News descriptions just repeat the title
        return _format(f"Google News: {topic}", items, limit, summary=False)
    feeds = [[dict(it, title=f"{name}: {it['title']}") for it in _entries(_fetch(url))] for name, url in TOP_FEEDS]
    mixed = [it for group in zip(*feeds) for it in group]
    return _format("Top headlines from Al Jazeera, DW and Real News", mixed, limit, summary=True)


@mcp.tool()
def news_simon_willison(limit: int = 5) -> str:
    """Use when the user asks about Simon Willison or his blog (AI and coding). limit 1-10 (default 5). Returns his latest posts, one per line."""
    _check_limit(limit)
    return _format("Simon Willison's Weblog", _entries(_fetch(SIMON_FEED)), limit, summary=True)


@mcp.tool()
def news_security(source: str, limit: int = 5) -> str:
    """Use when the user asks for cybersecurity or hacking news. source: krebs|schneier|lwn|2600. limit 1-10 (default 5). Returns one headline per line."""
    _check_limit(limit)
    key = source.strip().lower()
    for k, (title, _u) in SECURITY_FEEDS.items():
        if key == title.lower() or key.startswith(k):
            key = k
            break
    if key not in SECURITY_FEEDS:
        raise ValueError(f"source must be one of: {', '.join(SECURITY_FEEDS)}")
    name, url = SECURITY_FEEDS[key]
    return _format(name, _entries(_fetch(url)), limit, summary=True)


if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
