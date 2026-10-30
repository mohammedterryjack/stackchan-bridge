"""Tanakh (Hebrew Bible) MCP in English: short spoken plain-text replies. Keyless Sefaria API. Fail loudly."""
from __future__ import annotations

import html
import re
import urllib.parse
from collections import OrderedDict

import httpx
from fastmcp import FastMCP

API = "https://www.sefaria.org/api"
MAX_VERSES = 8
MAX_CHARS = 1000
TIMEOUT = httpx.Timeout(connect=3, read=5, write=3, pool=3)
HDR = {"User-Agent": "stackchan-tanakh/1.0"}
CACHE_MAX = 64
_cache: OrderedDict[str, dict] = OrderedDict()

mcp = FastMCP("Tanakh")


def _clean(t: str) -> str:
    t = re.sub(r"<sup[^>]*>.*?</sup>", "", t, flags=re.S)
    t = re.sub(r"<i class=\"footnote\">.*?</i>", "", t, flags=re.S)
    t = re.sub(r"<[^>]+>", "", t)
    return re.sub(r"\s+", " ", html.unescape(t)).strip()


def _chapter(book: str, chapter: int) -> dict:
    """Whole chapter in English: {'book', 'verses': [str], 'chapters'}; cached (bounded)."""
    b = re.sub(r"\s+", " ", (book or "").strip())
    if not b:
        raise ValueError("book required, e.g. Genesis or Psalms")
    if int(chapter) < 1:
        raise ValueError("chapter must be 1 or more")
    key = f"{b.lower()}|{int(chapter)}"
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    r = httpx.get(f"{API}/v3/texts/{urllib.parse.quote(f'{b} {int(chapter)}')}?version=english&return_format=text_only", timeout=TIMEOUT, headers=HDR)
    if r.status_code >= 500:
        r.raise_for_status()
    d = r.json()
    if "error" in d:
        raise ValueError(f"unknown book or chapter: {b} {chapter}")
    if d.get("primary_category") != "Tanakh":
        raise ValueError(f"{d.get('book', b)} is not a Tanakh book; try Genesis, Isaiah or Psalms")
    vs = [v for v in d.get("versions", []) if v.get("actualLanguage", v.get("language")) == "en" and v.get("text")]
    if not vs:
        raise LookupError(f"no English text for {d.get('ref', b)}")
    text = vs[0]["text"]
    if isinstance(text, str):
        text = [text]
    sec = d.get("sections") or [chapter]
    if str(sec[0]) != str(int(chapter)):
        raise ValueError(f"{d.get('book', b)} has no chapter {chapter}")
    out = {"book": d.get("book", b), "verses": [_clean(t) for t in text], "chapter": int(chapter)}
    _cache[key] = out
    while len(_cache) > CACHE_MAX:
        _cache.popitem(last=False)
    return out


def _render(c: dict, start: int, end: int) -> str:
    vs = c["verses"]
    n = len(vs)
    if not 1 <= start <= n:
        raise ValueError(f"{c['book']} {c['chapter']} has {n} verses")
    end = min(max(end, start), n, start + MAX_VERSES - 1)
    lines, last = [], start - 1
    for i in range(start, end + 1):
        line = f"{i}. {vs[i - 1]}"
        if lines and sum(len(x) + 1 for x in lines) + len(line) > MAX_CHARS - 80:
            break
        if not lines and len(line) > MAX_CHARS - 80:
            line = line[: MAX_CHARS - 100].rsplit(" ", 1)[0] + "... (cut short)"
        lines.append(line)
        last = i
    ref = f"({c['book']} {c['chapter']}:{start}" + (f"-{last})" if last > start else ")")
    more = f"Next: verse {last + 1}" if last < n else "End of chapter."
    return "\n".join([*lines, ref, more])


@mcp.tool()
def tanakh_verse(book: str, chapter: int, verse: str = "1") -> str:
    """Read Torah/Tanakh verses in English. book: e.g. Genesis, Exodus, Psalms, Isaiah. verse: '1' or a range '1-5' (max 8). Reply ends with a reference like (Genesis 1:1)."""
    m = re.fullmatch(r"\s*(\d{1,3})\s*(?:-\s*(\d{1,3}))?\s*", str(verse))
    if not m:
        raise ValueError("verse must be a number like 3 or a range like 1-5")
    a = int(m.group(1))
    b = int(m.group(2) or a)
    if b < a:
        raise ValueError("range end must not be before start")
    return _render(_chapter(book, chapter), a, b)


@mcp.tool()
def tanakh_chapter(book: str, chapter: int, start: int = 1) -> str:
    """Read a Tanakh chapter in English, a few verses at a time. start: verse to begin from (default 1); to continue, call again with the 'Next' verse."""
    return _render(_chapter(book, chapter), int(start), int(start) + MAX_VERSES - 1)


@mcp.tool()
def tanakh_search(query: str, limit: int = 3) -> str:
    """Find Torah/Tanakh verses by English words (e.g. 'shepherd', 'love your neighbor'). limit 1-5 (default 3). One line per verse ending (Book c:v); use tanakh_verse to read more."""
    q = (query or "").strip()
    if not q:
        raise ValueError("query required")
    if not 1 <= int(limit) <= 5:
        raise ValueError("limit must be 1..5")
    body = {"query": q, "type": "text", "size": 30, "filters": ["Tanakh"], "filter_fields": ["path"], "field": "naive_lemmatizer"}
    r = httpx.post(f"{API}/search-wrapper", json=body, timeout=TIMEOUT, headers=HDR)
    r.raise_for_status()
    hits = r.json()["hits"]["hits"]
    seen, lines = set(), []
    for h in hits:
        ref, _, ver = h["_id"].partition(" (")
        if ref in seen or "[en]" not in ver:
            continue
        seen.add(ref)
        mm = re.fullmatch(r"(.+) (\d+):(\d+)", ref)
        if not mm:
            continue
        c = _chapter(mm.group(1), int(mm.group(2)))
        v = int(mm.group(3))
        if not 1 <= v <= len(c["verses"]):
            continue
        t = c["verses"][v - 1]
        t = t if len(t) <= 220 else t[:217] + "..."
        lines.append(f"{t} ({ref})")
        if len(lines) >= int(limit):
            break
    if not lines:
        return f"No Tanakh verses found for {q!r}."
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
