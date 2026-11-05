"""Habib Omar bin Hafiz updates MCP: live fetch only (no permanent storage) of latest news and teachings from
official sources — alhabibumar.com (Arabic news) and muwasala.org (English answers/teachings of Sayyidi Habib Umar).
Tools: habib_omar_latest, habib_omar_read. Fail loudly if a source cannot be fetched or parsed. Short spoken parts."""

from __future__ import annotations

import html
import re
import time
import urllib.parse
from html.parser import HTMLParser

import httpx
from fastmcp import FastMCP

from mcp_tool_log import get_logger, logged, note

mcp = FastMCP("HabibOmar")
log = get_logger("HABIB_OMAR")

UA = "Mozilla/5.0 (compatible; stackchan-habib-omar/1.0)"
TIMEOUT = httpx.Timeout(connect=4, read=8, write=4, pool=4)
CACHE_TTL = 300
MAX_LIMIT = 10
PART_CHARS = 800
NEWS_INDEX = "https://alhabibumar.com/" + urllib.parse.quote("إعلاميات/أخبار-الحبيب-عمر")
NEWS_MARKER = "أخبار-الحبيب-عمر"
MUWASALA_API = (
    "https://muwasala.org/wp-json/wp/v2/posts"
    "?search=Answered%20by%20Sayyidi&per_page={n}&_fields=id,date,title,link,content,excerpt"
)
LABEL = "Habib Omar bin Hafiz (الحبيب عمر بن حفيظ / Habib Umar bin Hafiz)"
A = (
    "Habib Omar bin Hafiz (الحبيب عمر بن حفيظ, also Habib Umar bin Hafiz / Sayyidi al-Habib): "
    "use when the user asks for Habib Omar / Habib Umar updates, latest news, talks, teachings, "
    "or what he has been doing recently."
)
H = "Arabic replies: explain or translate in the user's language. Keep speech short; use part=N+1 for more."

_cache: dict[str, tuple[float, object]] = {}
_latest_cache: list[dict] | None = None
_latest_at = 0.0


def _get(url: str) -> str:
    hit = _cache.get("GET:" + url)
    if hit and time.monotonic() - hit[0] < CACHE_TTL:
        return hit[1]  # type: ignore[return-value]
    resp = httpx.get(url, timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": UA})
    resp.raise_for_status()
    text = resp.text
    if not text or len(text) < 200:
        raise RuntimeError(f"empty or tiny response from {url} ({len(text)} bytes)")
    _cache["GET:" + url] = (time.monotonic(), text)
    return text


def _clean(text: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", text or "", flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", html.unescape(text))
    return re.sub(r"\s+", " ", text).strip()


class _NewsIndexParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._cur: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href") or ""
        raw = urllib.parse.unquote(href)
        if NEWS_MARKER not in raw:
            return
        # skip the index page itself
        tail = raw.rstrip("/").split("/")[-1]
        if tail == NEWS_MARKER or not tail:
            return
        if not raw.startswith("http"):
            href = urllib.parse.urljoin("https://alhabibumar.com/", href)
        self._cur = ["", href]

    def handle_data(self, data: str) -> None:
        if self._cur is not None:
            self._cur[0] += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._cur is not None:
            title = re.sub(r"\s+", " ", self._cur[0]).strip()
            if len(title) > 5:
                self.links.append((title, self._cur[1]))
            self._cur = None


def _news_items() -> list[dict]:
    page = _get(NEWS_INDEX)
    if NEWS_MARKER not in urllib.parse.unquote(page) and NEWS_MARKER not in page:
        # marker is URL-encoded in page; accept either
        if urllib.parse.quote(NEWS_MARKER) not in page and "أخبار" not in page:
            raise RuntimeError("alhabibumar.com news index did not contain expected Habib Omar news links")
    p = _NewsIndexParser()
    p.feed(page)
    seen: set[str] = set()
    out: list[dict] = []
    for title, link in p.links:
        if link in seen:
            continue
        seen.add(link)
        out.append({"source": "news", "title": title, "link": link, "date": "", "summary": ""})
    if not out:
        raise RuntimeError("alhabibumar.com news index parsed zero Habib Omar articles (layout may have changed)")
    return out


def _muwasala_items(limit: int) -> list[dict]:
    url = MUWASALA_API.format(n=max(limit, 5))
    hit = _cache.get("JSON:" + url)
    if hit and time.monotonic() - hit[0] < CACHE_TTL:
        data = hit[1]
    else:
        resp = httpx.get(url, timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": UA})
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list) or not data:
            raise RuntimeError("muwasala.org returned no Sayyidi Habib Umar posts")
        _cache["JSON:" + url] = (time.monotonic(), data)
    out = []
    for p in data:
        title = _clean(p.get("title", {}).get("rendered", ""))
        excerpt = _clean(p.get("excerpt", {}).get("rendered", ""))
        body = _clean(p.get("content", {}).get("rendered", ""))
        if "Habib Umar" not in body and "Habib Omar" not in body and "Sayyidi" not in body:
            # keep Answered-by-Sayyidi search hits anyway; API already filtered
            pass
        out.append({
            "source": "muwasala",
            "title": title,
            "link": p.get("link") or "",
            "date": (p.get("date") or "")[:10],
            "summary": excerpt[:200],
            "body": body,
            "id": str(p.get("id")),
        })
    if not out:
        raise RuntimeError("muwasala.org Sayyidi search returned no usable posts")
    return out


def _combined(source: str | None, limit: int) -> list[dict]:
    global _latest_cache, _latest_at
    src = (source or "all").strip().lower()
    if src in ("", "all", "both"):
        src = "all"
    if src not in ("all", "news", "muwasala", "teachings"):
        raise ValueError("source must be one of: all, news, muwasala (teachings)")
    if src == "teachings":
        src = "muwasala"
    # short in-memory cache of the merged list for habib_omar_read by index
    if _latest_cache and time.monotonic() - _latest_at < CACHE_TTL and src == "all":
        return _latest_cache[:limit]
    items: list[dict] = []
    errors: list[str] = []
    if src in ("all", "news"):
        try:
            items.extend(_news_items())
        except Exception as e:
            errors.append(f"news: {type(e).__name__}: {e}")
            if src == "news":
                raise
    if src in ("all", "muwasala"):
        try:
            items.extend(_muwasala_items(limit))
        except Exception as e:
            errors.append(f"muwasala: {type(e).__name__}: {e}")
            if src == "muwasala":
                raise
    if not items:
        raise RuntimeError("Habib Omar sources failed: " + "; ".join(errors) if errors else "no items")
    if src == "all":
        _latest_cache = items
        _latest_at = time.monotonic()
    return items[:limit]


def _parts(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        raise RuntimeError("article body was empty after cleaning")
    if len(text) <= PART_CHARS:
        return [text]
    out: list[str] = []
    i = 0
    while i < len(text):
        j = min(len(text), i + PART_CHARS)
        if j < len(text):
            k = text.rfind(" ", i + PART_CHARS // 2, j)
            if k > i:
                j = k
        out.append(text[i:j].strip())
        i = j
    return [p for p in out if p]


def _article_body(item: dict) -> str:
    if item.get("body"):
        return item["body"]
    page = _get(item["link"])
    m = re.search(r'class="[^"]*field--name-body[^"]*"[^>]*>([\s\S]*?)</div>\s*</div>', page, re.I)
    if m:
        body = _clean(m.group(1))
        if len(body) > 80:
            return body
    m = re.search(r"<article[^>]*>([\s\S]*?)</article>", page, re.I)
    if m:
        body = _clean(m.group(1))
        if len(body) > 80:
            return body
    raise RuntimeError(f"could not extract article body from {item['link']} (layout may have changed)")


@mcp.tool(
    name="habib_omar_latest",
    description=f"{A} Lists the latest updates. source: all (default)|news (alhabibumar.com Arabic news)|muwasala (English teachings answered by Sayyidi Habib Umar on muwasala.org). limit 1-10 (default 5). Returns numbered titles; open one with habib_omar_read(index=N) or habib_omar_read(url=...). Live fetch only. {H}",
)
@logged(log)
def habib_omar_latest(source: str | None = None, limit: int = 5) -> str:
    if not 1 <= int(limit) <= MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    items = _combined(source, int(limit))
    note(*[it.get("id") or it["link"] for it in items])
    lines = [f"{LABEL} — latest ({(source or 'all')}):"]
    for i, it in enumerate(items, 1):
        bit = f"{i}. [{it['source']}] {it['title']}"
        if it.get("date"):
            bit += f" ({it['date']})"
        if it.get("summary"):
            sm = it["summary"]
            bit += f" — {sm if len(sm) <= 120 else sm[:117] + '...'}"
        lines.append(bit)
    lines.append("Open with habib_omar_read(index=N) or habib_omar_read(url='...').")
    return "\n".join(lines)


@mcp.tool(
    name="habib_omar_read",
    description=f"{A} Reads one update. Pass index from habib_omar_latest (1-based, same source=all list) OR url of an alhabibumar.com / muwasala.org page. part 1.. for short spoken chunks (~800 chars). Live fetch; nothing stored. {H}",
)
@logged(log)
def habib_omar_read(index: int | None = None, url: str | None = None, part: int = 1, source: str | None = None) -> str:
    p = int(part)
    if p < 1:
        raise ValueError("part must be 1 or more")
    item: dict | None = None
    if url and str(url).strip():
        u = str(url).strip()
        if "alhabibumar.com" not in u and "muwasala.org" not in u and "alhabibomar.com" not in u:
            raise ValueError("url must be on alhabibumar.com or muwasala.org")
        item = {"source": "url", "title": u.rstrip("/").split("/")[-1][:80], "link": u, "date": "", "summary": ""}
    elif index is not None:
        items = _combined(source or "all", MAX_LIMIT)
        i = int(index)
        if not 1 <= i <= len(items):
            raise ValueError(f"index {i} out of range: call habib_omar_latest first (1 to {len(items)})")
        item = items[i - 1]
    else:
        raise ValueError("pass index= from habib_omar_latest, or url=")
    assert item is not None
    body = _article_body(item)
    parts = _parts(body)
    if p > len(parts):
        raise ValueError(f"part {p} out of range: this update has parts 1 to {len(parts)}")
    note(item.get("id") or item["link"])
    lang_note = "[May be Arabic: explain or translate in the user's language.]" if item["source"] == "news" else ""
    if index is not None and p < len(parts):
        src_bit = f", source={source!r}" if source else ""
        nxt = f"habib_omar_read(index={int(index)}, part={p + 1}{src_bit})"
    elif p < len(parts):
        nxt = f"habib_omar_read(url=..., part={p + 1})"
    else:
        nxt = "end of update"
    head = f"{LABEL}. [{item['source']}] {item['title']}"
    if item.get("date"):
        head += f" ({item['date']})"
    head += f". Part {p} of {len(parts)}:"
    return "\n".join(x for x in [lang_note, head, parts[p - 1], f"Next: {nxt}"] if x)


if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
