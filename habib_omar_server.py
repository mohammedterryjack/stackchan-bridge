"""Habib Omar bin Hafiz updates MCP: live fetch only (no permanent storage) from his official, login-free sources:
the Telegram channel t.me/HabibOmar (public web preview, posted several times a day), the official YouTube channel
HabibOmarCom (RSS), alhabibumar.com news (Arabic, Hijri-dated) and muwasala.org (English answers by Sayyidi Habib
Umar; an archive that is rarely updated). Every item carries its date (news also its Hijri date) so staleness is
audible; source=all merges every source newest first. Tools: habib_omar_latest, habib_omar_read. Fails loudly: any
source that cannot be fetched or parsed raises (no silent skipping). Short spoken parts."""

from __future__ import annotations

import html
import re
import time
import unicodedata
import urllib.parse
from datetime import datetime, timezone

import httpx
from app import mcp
from hijridate import Hijri

from mcp_tool_log import get_logger, logged, note

log = get_logger("HABIB_OMAR")

UA = "Mozilla/5.0 (compatible; stackchan-habib-omar/2.0)"
TIMEOUT = httpx.Timeout(connect=4, read=6, write=4, pool=4)
CACHE_TTL = 300
MAX_LIMIT = 10
PART_CHARS = 800
SITE = "https://alhabibumar.com"
NEWS_INDEX = SITE + "/" + urllib.parse.quote("إعلاميات/أخبار-الحبيب-عمر")
TELEGRAM = "https://t.me/s/HabibOmar"
YOUTUBE_RSS = "https://www.youtube.com/feeds/videos.xml?channel_id=UCg4WWlji1lydn6Op8m7tWkw"  # youtube.com/HabibOmarCom
MUWASALA_API = ("https://muwasala.org/wp-json/wp/v2/posts?search=Answered%20by%20Sayyidi&orderby=date&order=desc"
                "&per_page=10&_fields=id,date,title,link,content,excerpt")
SOURCES = ("telegram", "youtube", "news", "muwasala")
LABEL = "Habib Omar bin Hafiz"
A = "Habib Omar bin Hafiz (Habib Umar):"
H = "Say each item's date; translate Arabic when speaking."

_cache: dict[str, tuple[float, object]] = {}
_last_list: dict[str, list[dict]] = {}

_HMONTHS = {
    "محرم": 1, "صفر": 2, "ربيع الاول": 3, "ربيع الثاني": 4, "ربيع الاخر": 4, "جمادى الاول": 5, "جمادى الاولى": 5,
    "جمادى الاخر": 6, "جمادى الاخرة": 6, "جمادى الثانية": 6, "رجب": 7, "شعبان": 8, "رمضان": 9, "شوال": 10,
    "ذو القعدة": 11, "ذو الحجة": 12,
}
_HNAMES = ["Muharram", "Safar", "Rabi al-Awwal", "Rabi al-Thani", "Jumada al-Ula", "Jumada al-Akhira", "Rajab",
           "Shaban", "Ramadan", "Shawwal", "Dhul-Qadah", "Dhul-Hijjah"]


def _fetch(url: str) -> httpx.Response:
    hit = _cache.get(url)
    if hit and time.monotonic() - hit[0] < CACHE_TTL:
        return hit[1]  # type: ignore[return-value]
    resp = httpx.get(url, timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": UA})
    resp.raise_for_status()
    if len(resp.content) < 200:
        raise RuntimeError(f"empty or tiny response from {url} ({len(resp.content)} bytes)")
    _cache[url] = (time.monotonic(), resp)
    return resp


def _clean(text: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", text or "", flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = "".join(c for c in text if ord(c) < 0x10000 and unicodedata.category(c) not in ("So", "Cs", "Co"))
    text = text.replace("*", "").replace("\u200d", "").replace("\ufe0f", "")
    return re.sub(r"[ \t]+", " ", re.sub(r"\s*\n\s*", "\n", text)).strip()


def _hijri(s: str) -> tuple[datetime, str]:
    """'25 جمادى الأول 1448' -> (Gregorian datetime UTC, '25 Jumada al-Ula 1448')."""
    t = re.sub(r"[\u064B-\u0652\u0640]", "", s).translate(str.maketrans("أإآ", "ااا")).strip()
    m = re.fullmatch(r"(\d{1,2})\s+(.+?)\s+(\d{4})", t)
    if not m:
        raise RuntimeError(f"alhabibumar.com date {s!r} is not 'day month year' Hijri")
    mon = _HMONTHS.get(re.sub(r"\s+", " ", m.group(2)).replace("ة", "ة").strip())
    if mon is None:
        raise RuntimeError(f"alhabibumar.com date {s!r} has an unknown Hijri month")
    d, y = int(m.group(1)), int(m.group(3))
    g = Hijri(y, mon, d).to_gregorian()
    return datetime(g.year, g.month, g.day, tzinfo=timezone.utc), f"{d} {_HNAMES[mon - 1]} {y}"


def _news() -> list[dict]:
    page = _fetch(NEWS_INDEX).text
    blocks = re.split(r'class="row border-radius node node--type-update', page)[1:]
    if not blocks:
        raise RuntimeError("alhabibumar.com news index has no update blocks (layout may have changed)")
    out = []
    for b in blocks:
        d = re.search(r'field--name-field-date[^>]*>\s*(?:<p>)?\s*([^<]+?)\s*<', b)
        t = re.search(r'<h1[^>]*>\s*<a href="([^"]+)"[^>]*>\s*([^<]+?)\s*</a>', b)
        if not d or not t:
            raise RuntimeError("alhabibumar.com news block without date or title (layout may have changed)")
        when, hij = _hijri(html.unescape(d.group(1)))
        out.append({"source": "news", "title": html.unescape(t.group(2)).strip(), "when": when,
                    "date": f"{when:%Y-%m-%d}, {hij} AH", "link": urllib.parse.urljoin(SITE, t.group(1)), "body": ""})
    return out


def _telegram() -> list[dict]:
    page = _fetch(TELEGRAM).text
    blocks = re.split(r'<div class="tgme_widget_message_wrap', page)[1:]
    if not blocks:
        raise RuntimeError("t.me/s/HabibOmar has no messages (layout may have changed)")
    out = []
    for b in blocks:
        post = re.search(r'data-post="(HabibOmar/\d+)"', b)
        when = re.search(r'<time datetime="([^"]+)"', b)
        if not post or not when:
            raise RuntimeError("t.me/s/HabibOmar message without id or time (layout may have changed)")
        txt = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>([\s\S]*?)</div>', b)
        body = _clean(txt.group(1)) if txt else ""
        if not body:
            continue  # photo/video post with no caption: nothing to speak
        dt = datetime.fromisoformat(when.group(1)).astimezone(timezone.utc)
        flat = re.sub(r"\s+", " ", body)
        title = flat if len(flat) <= 140 else flat[:140].rsplit(" ", 1)[0] + "..."
        out.append({"source": "telegram", "title": title, "when": dt, "date": f"{dt:%Y-%m-%d %H:%M} UTC",
                    "link": "https://t.me/" + post.group(1), "body": body})
    if not out:
        raise RuntimeError("t.me/s/HabibOmar returned no text messages")
    return out


def _youtube() -> list[dict]:
    xml = _fetch(YOUTUBE_RSS).text
    entries = re.findall(r"<entry>([\s\S]*?)</entry>", xml)
    if not entries:
        raise RuntimeError("Habib Omar YouTube RSS has no entries")
    out = []
    for e in entries:
        t = re.search(r"<title>([^<]*)</title>", e)
        p = re.search(r"<published>([^<]+)</published>", e)
        v = re.search(r"<yt:videoId>([^<]+)</yt:videoId>", e)
        if not t or not p or not v:
            raise RuntimeError("Habib Omar YouTube RSS entry without title, date or id")
        desc = re.search(r"<media:description>([\s\S]*?)</media:description>", e)
        dt = datetime.fromisoformat(p.group(1)).astimezone(timezone.utc)
        title = html.unescape(t.group(1)).strip()
        out.append({"source": "youtube", "title": title, "when": dt, "date": f"{dt:%Y-%m-%d %H:%M} UTC",
                    "link": f"https://www.youtube.com/watch?v={v.group(1)}", "vid": v.group(1),
                    "body": title + "\n" + (_clean(desc.group(1)) if desc else "")})
    return out


def _muwasala() -> list[dict]:
    data = _fetch(MUWASALA_API).json()
    if not isinstance(data, list) or not data:
        raise RuntimeError("muwasala.org returned no Sayyidi Habib Umar posts")
    out = []
    for p in data:
        dt = datetime.fromisoformat(p["date"]).replace(tzinfo=timezone.utc)
        out.append({"source": "muwasala", "title": _clean(p["title"]["rendered"]), "when": dt,
                    "date": f"{dt:%Y-%m-%d}", "link": p.get("link") or "", "id": str(p.get("id")),
                    "summary": _clean(p["excerpt"]["rendered"])[:200], "body": _clean(p["content"]["rendered"])})
    return out


_FETCH = {"telegram": _telegram, "youtube": _youtube, "news": _news, "muwasala": _muwasala}


def _source(source: str | None) -> str:
    s = (source or "all").strip().lower()
    s = {"": "all", "both": "all", "teachings": "muwasala", "site": "news", "alhabibumar": "news", "tg": "telegram",
         "video": "youtube", "videos": "youtube"}.get(s, s)
    if s != "all" and s not in SOURCES:
        raise ValueError(f"source must be all or one of: {', '.join(SOURCES)}")
    return s


def _items(src: str) -> list[dict]:
    names = SOURCES if src == "all" else (src,)
    items = [it for n in names for it in _FETCH[n]()]
    if src == "all":
        tg_text = " ".join(it["body"] for it in items if it["source"] == "telegram")
        items = [it for it in items if not (it["source"] == "youtube" and it["vid"] in tg_text)]
    items.sort(key=lambda it: it["when"], reverse=True)
    _last_list[src] = items
    return items


def _age(when: datetime) -> str:
    hours = (datetime.now(timezone.utc) - when).total_seconds() / 3600
    if hours < 1:
        return "under an hour ago"
    return f"{int(hours)} hours ago" if hours < 48 else f"{int(hours // 24)} days ago"


@mcp.tool(name="habib_omar_latest", description=f"{A} his latest lessons, talks, live streams and news, newest first; source all (default), telegram, youtube, news or muwasala. {H}")
@logged(log)
def habib_omar_latest(source: str | None = None, limit: int = 5) -> str:
    if not 1 <= int(limit) <= MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    src = _source(source)
    items = _items(src)[: int(limit)]
    note(*[it["link"] for it in items])
    lines = [f"{LABEL}, latest ({src}), newest first:"]
    for i, it in enumerate(items, 1):
        bit = f"{i}. [{it['source']}, {it['date']}, {_age(it['when'])}] {it['title']}"
        if it.get("summary"):
            sm = it["summary"]
            bit += f" - {sm if len(sm) <= 120 else sm[:117] + '...'}"
        lines.append(bit)
    lines.append(f"Open with habib_omar_read(index=N{'' if src == 'all' else f', source={src!r}'}).")
    return "\n".join(lines)


def _article_body(link: str) -> str:
    page = _fetch(link).text
    for pat in (r'class="[^"]*field--name-body[^"]*"[^>]*>([\s\S]*?)</div>\s*</div>', r"<article[^>]*>([\s\S]*?)</article>"):
        m = re.search(pat, page, re.I)
        if m and len(_clean(m.group(1))) > 80:
            return _clean(m.group(1))
    raise RuntimeError(f"could not extract article body from {link} (layout may have changed)")


def _parts(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        raise RuntimeError("update body was empty after cleaning")
    out, i = [], 0
    while i < len(text):
        j = min(len(text), i + PART_CHARS)
        if j < len(text):
            k = text.rfind(" ", i + PART_CHARS // 2, j)
            j = k if k > i else j
        out.append(text[i:j].strip())
        i = j
    return [p for p in out if p]


@mcp.tool(name="habib_omar_read", description=f"{A} read one update in full by its number from habib_omar_latest (same source) or a page url, in parts. {H}")
@logged(log)
def habib_omar_read(index: int | None = None, url: str | None = None, part: int = 1, source: str | None = None) -> str:
    p = int(part)
    if p < 1:
        raise ValueError("part must be 1 or more")
    if url and str(url).strip():
        u = str(url).strip()
        host = urllib.parse.urlparse(u).netloc.lower()
        if host not in ("alhabibumar.com", "www.alhabibumar.com", "muwasala.org", "www.muwasala.org"):
            raise ValueError("url must be on alhabibumar.com or muwasala.org")
        item = {"source": "url", "title": urllib.parse.unquote(u.rstrip("/").split("/")[-1])[:80], "link": u, "date": "", "body": ""}
    elif index is not None:
        src = _source(source)
        items = _last_list.get(src) or _items(src)
        i = int(index)
        if not 1 <= i <= len(items):
            raise ValueError(f"index {i} out of range: 1 to {len(items)} (call habib_omar_latest first)")
        item = items[i - 1]
    else:
        raise ValueError("pass index= from habib_omar_latest, or url=")
    parts = _parts(item["body"] or _article_body(item["link"]))
    if p > len(parts):
        raise ValueError(f"part {p} out of range: this update has parts 1 to {len(parts)}")
    note(item["link"])
    more = "end of update"
    if p < len(parts):
        more = (f"habib_omar_read(index={int(index)}, part={p + 1}" + (f", source={source!r})" if source else ")")
                if index is not None else f"habib_omar_read(url=..., part={p + 1})")
    head = f"{LABEL}. [{item['source']}" + (f", {item['date']}" if item.get("date") else "") + f"] {item['title']}. Part {p} of {len(parts)}:"
    return "\n".join([head, parts[p - 1], f"Next: {more}"])
