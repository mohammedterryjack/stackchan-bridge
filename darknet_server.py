"""Darknet Diaries MCP: live feed + transcripts from darknetdiaries.com. Nothing written to disk."""
from __future__ import annotations

import html
import re
import threading
import time
import xml.etree.ElementTree as ET
from collections import OrderedDict
from email.utils import parsedate_to_datetime

import httpx
from fastmcp import FastMCP

FEED_URL = "https://feeds.megaphone.fm/darknetdiaries"
TRANSCRIPT_URL = "https://darknetdiaries.com/transcript/{n}/"
ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
FEED_REFRESH_S = 3 * 3600
HTTP_TIMEOUT_S = 6.0
PART_CHARS = 900
TRANSCRIPT_CACHE = 5
MAX_LIMIT = 5
UA = {"User-Agent": "stackchan-bridge/1.0 (+darknet-diaries reader)"}

mcp = FastMCP("DarknetDiaries")

_feed_lock = threading.Lock()
_episodes: dict[int, dict] = {}
_feed_at = 0.0
_feed_error: BaseException | None = None
_refreshing = threading.Lock()
_first_load = threading.Event()
FIRST_LOAD_WAIT_S = 6.0
_tx_lock = threading.Lock()
_tx: "OrderedDict[int, list[str]]" = OrderedDict()


def _clean(s: str) -> str:
    s = re.sub(r"<!--.*?-->", " ", s, flags=re.S)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _fetch_feed() -> None:
    global _episodes, _feed_at, _feed_error
    if not _refreshing.acquire(blocking=False):
        return
    try:
        r = httpx.get(FEED_URL, headers=UA, timeout=30.0, follow_redirects=True)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        eps: dict[int, dict] = {}
        for it in root.iter("item"):
            title = it.findtext("title") or ""
            num = it.findtext(ITUNES + "episode")
            m = re.match(r"\s*(\d+)\s*:\s*(.*)", title)
            if not num and not m:
                continue  # unnumbered promo item, not an episode
            n = int(num or m.group(1))
            name = (it.findtext(ITUNES + "title") or (m.group(2) if m else title)).strip()
            desc = _clean(it.findtext("description") or it.findtext(ITUNES + "summary") or "")
            desc = re.split(r"\s(?:Sponsors?|Sources?|Attribution|Transcript)\b", desc)[0].strip()
            d = parsedate_to_datetime(it.findtext("pubDate"))
            eps[n] = {"n": n, "title": name, "date": d.strftime("%b %Y"), "desc": desc}
        if len(eps) < 100:
            raise RuntimeError(f"feed parsed only {len(eps)} episodes")
        with _feed_lock:
            _episodes, _feed_at, _feed_error = eps, time.time(), None
    except BaseException as e:
        with _feed_lock:
            _feed_error = e
    finally:
        _refreshing.release()
        _first_load.set()


threading.Thread(target=_fetch_feed, name="dd-feed", daemon=True).start()


def _feed() -> dict[int, dict]:
    _first_load.wait(timeout=FIRST_LOAD_WAIT_S)
    with _feed_lock:
        eps, at, err = _episodes, _feed_at, _feed_error
    if not eps:
        if _refreshing.locked():
            raise RuntimeError("Darknet Diaries episode list is still loading, try again in a few seconds")
        raise RuntimeError(f"Darknet Diaries feed could not be loaded: {err}")
    if time.time() - at > FEED_REFRESH_S:
        threading.Thread(target=_fetch_feed, name="dd-feed", daemon=True).start()
    return eps


def _ep_num(episode) -> int:
    m = re.fullmatch(r"\s*(?:ddiaries|darknet diaries|episode|ep)?\s*\.?\s*#?(\d+)\s*", str(episode), re.I)
    if not m:
        raise ValueError(f"Not an episode number: {episode!r}. Use e.g. 140, 'ep 140' or 'ddiaries 140'.")
    return int(m.group(1))


def _episode(episode) -> dict:
    n = _ep_num(episode)
    eps = _feed()
    if n not in eps:
        raise ValueError(f"No Darknet Diaries episode {n}. Episodes run 1 to {max(eps)}.")
    return eps[n]


def _split(text: str) -> list[str]:
    """Deterministic ~PART_CHARS chunks on sentence boundaries."""
    sents = re.split(r"(?<=[.!?])\s+", text)
    out, cur = [], ""
    for s in sents:
        while len(s) > PART_CHARS:
            cut = s.rfind(" ", 0, PART_CHARS)
            cut = cut if cut > 0 else PART_CHARS
            if cur:
                out.append(cur); cur = ""
            out.append(s[:cut]); s = s[cut:].strip()
        if cur and len(cur) + 1 + len(s) > PART_CHARS:
            out.append(cur); cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        out.append(cur)
    return out


def _parts(n: int) -> list[str]:
    with _tx_lock:
        if n in _tx:
            _tx.move_to_end(n)
            return _tx[n]
    r = httpx.get(TRANSCRIPT_URL.format(n=n), headers=UA, timeout=HTTP_TIMEOUT_S, follow_redirects=True)
    if r.status_code == 404:
        raise ValueError(f"No transcript published for episode {n}.")
    r.raise_for_status()
    m = re.search(r"<pre[^>]*>(.*?)</pre>", r.text, re.S)
    if not m:
        raise RuntimeError(f"Transcript page for episode {n} has no <pre> block; site layout changed")
    body = re.sub(r"<!--.*?-->", " ", m.group(1), flags=re.S)
    paras = re.findall(r"<p[^>]*>(.*?)</p>", body, re.S)
    turns: list[str] = []
    for p in paras:
        t = _clean(p)
        t = re.sub(r"\[[A-Za-z0-9 ,'’._-]+\]", " ", t)  # [MUSIC], [AD BREAK], [START OF RECORDING]
        t = re.sub(r"\s+", " ", t).strip()
        sm = re.match(r"([A-Z][A-Z0-9 .'’&-]{0,30}):\s*(.*)", t)
        if sm:
            if not sm.group(2):
                continue
            t = f"{sm.group(1).strip().title()}: {sm.group(2)}"
        if t:
            turns.append(t)
    if not turns:
        raise RuntimeError(f"Transcript for episode {n} parsed to nothing; site layout changed")
    parts: list[str] = []
    cur = ""
    for t in turns:
        for piece in (_split(t) if len(t) > PART_CHARS else [t]):
            if cur and len(cur) + 1 + len(piece) > PART_CHARS:
                parts.append(cur); cur = piece
            else:
                cur = f"{cur} {piece}".strip()
    if cur:
        parts.append(cur)
    with _tx_lock:
        _tx[n] = parts
        _tx.move_to_end(n)
        while len(_tx) > TRANSCRIPT_CACHE:
            _tx.popitem(last=False)
    return parts


def _line(e: dict) -> str:
    return f"Ep {e['n']}, {e['title']}, {e['date']} (ddiaries {e['n']})"


@mcp.tool()
def darknet_episodes(query: str = "", limit: int = 3) -> str:
    """List Darknet Diaries podcast episodes: the latest ones, or ones matching a topic (e.g. 'xbox', 'ransomware')."""
    limit = max(1, min(int(limit), MAX_LIMIT))
    eps = sorted(_feed().values(), key=lambda e: -e["n"])
    q = query.strip().lower()
    if q:
        words = q.split()
        def score(e):
            t, d = e["title"].lower(), e["desc"].lower()
            return sum(3 * (w in t) + (w in d) for w in words) + (5 if q in t else 0) + (2 if q in d else 0)
        eps = [e for e in sorted(eps, key=lambda e: -score(e)) if score(e) > 0]
        if not eps:
            return f"No Darknet Diaries episodes match '{query}'."
    return "\n".join(_line(e) for e in eps[:limit])


@mcp.tool()
def darknet_episode(episode: str) -> str:
    """Short spoken summary of one Darknet Diaries episode. episode: number like 140 or 'ddiaries 140'."""
    e = _episode(episode)
    sents = re.split(r"(?<=[.!?])\s+", e["desc"])
    summ = ""
    for s in sents[:3]:
        if len(summ) + len(s) > 480:
            break
        summ = f"{summ} {s}".strip()
    if not summ:
        summ = e["desc"][:480].rsplit(" ", 1)[0] + "..."
    return f"Episode {e['n']}, {e['title']}, {e['date']}. {summ} (ddiaries {e['n']})"


@mcp.tool()
def darknet_transcript(episode: str, part: int = 1) -> str:
    """Read a Darknet Diaries episode transcript aloud in short parts. Say the next part number to continue."""
    n = _ep_num(episode)
    parts = _parts(n)
    part = int(part)
    if not 1 <= part <= len(parts):
        raise ValueError(f"Episode {n} has parts 1 to {len(parts)}.")
    return f"{parts[part - 1]} (ddiaries {n} part {part} of {len(parts)})"


@mcp.tool()
def darknet_find(episode: str, phrase: str) -> str:
    """Find where a word or phrase comes up in a Darknet Diaries transcript; gives part numbers to read."""
    n = _ep_num(episode)
    parts = _parts(n)
    q = phrase.strip().lower()
    if not q:
        raise ValueError("phrase is empty")
    words = [w for w in re.findall(r"\w+", q) if len(w) > 2] or [q]
    hits = []
    for i, p in enumerate(parts, 1):
        low = p.lower()
        sc = (10 if q in low else 0) + sum(w in low for w in words)
        if sc >= max(1, len(words)) or q in low:
            hits.append((-sc, i, p, low))
    if not hits:
        return f"'{phrase}' not found in episode {n}."
    out = []
    for _, i, p, low in sorted(hits)[:3]:
        pos = low.find(q)
        if pos < 0:
            pos = min(low.find(w) for w in words if w in low)
        s = max(0, pos - 60)
        snip = p[s:s + 140].strip()
        out.append(f"Part {i}: ...{snip}...")
    return "\n".join(out) + f"\n(ddiaries {n}, {len(parts)} parts)"


if __name__ == "__main__":
    mcp.run()
