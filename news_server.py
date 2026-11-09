"""Compact news MCP server: Google News / Al Jazeera / DW / Real News / Reuters headlines, Simon Willison's blog, security news feeds."""
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
    # Reuters has no official RSS: Google News restricted to reuters.com, last 24h.
    ("Reuters", "https://news.google.com/rss/search?q=when:24h+site:reuters.com&hl=en-GB&gl=GB&ceid=GB:en"),
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


def _source_items(name: str, url: str) -> list[dict]:
    items = _entries(_fetch(url))
    if name != "Reuters":
        return items
    # Google News titles end ' - Reuters' and descriptions just repeat the title.
    out = [dict(it, title=it["title"][: -len(" - Reuters")], summary="") for it in items if it["title"].endswith(" - Reuters")]
    if not out:
        raise RuntimeError("Reuters feed returned no Reuters headlines")
    return out


def _source_feed(source: str) -> tuple[str, str]:
    key = source.strip().lower()
    if key.startswith("simon"):
        return "Simon Willison's Weblog", SIMON_FEED
    for k, (title, url) in SECURITY_FEEDS.items():
        if key == title.lower() or key.startswith(k):
            return title, url
    raise ValueError(f"source must be one of: simon willison, {', '.join(SECURITY_FEEDS)}")


@mcp.tool(name="news_headlines", description="News headlines: top world news, a topic search, or one source (simon willison for AI blog posts; krebs, schneier, lwn or 2600 for security news).")
def news_headlines(topic: str | None = None, source: str | None = None, limit: int = 5) -> str:
    _check_limit(limit)
    topic = (topic or "").strip()
    if source and source.strip():
        if topic:
            raise ValueError("give either topic or source, not both")
        name, url = _source_feed(source)
        return _format(name, _entries(_fetch(url)), limit, summary=True)
    if topic:
        items = _entries(_fetch(GOOGLE_SEARCH.format(q=urllib.parse.quote(topic))))
        # Google News descriptions just repeat the title
        return _format(f"Google News: {topic}", items, limit, summary=False)
    feeds = [[dict(it, title=f"{name}: {it['title']}") for it in _source_items(name, url)] for name, url in TOP_FEEDS]
    mixed = [it for group in zip(*feeds) for it in group]
    return _format("Top headlines from Al Jazeera, DW, Real News and Reuters", mixed, limit, summary=True)


if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
