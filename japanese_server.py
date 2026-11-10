"""Japanese learning MCP for Stack-chan's English TTS voice: every reply is ASCII romaji, English first then Japanese.
Tools: japanese_word (live jisho.org lookup, small in-memory cache), japanese_phrase (hf://datasets/MTerryJack/japanese-phrases, Tatoeba eng-jpn CC BY 2.0 FR, read on demand, nothing
kept in RAM) and japanese_to_romaji (fugashi + unidic-lite baked into the image). Fails loudly; no fallbacks."""

from __future__ import annotations

import re
import time

import httpx
from app import mcp
from hf_data import HFTable

from japanese_romaji import has_japanese, kana_to_romaji, to_romaji
from mcp_tool_log import get_logger, logged, note

REPO = "MTerryJack/japanese-phrases"
MIN_ROWS = 100_000
JISHO = "https://jisho.org/api/v1/search/words"
TIMEOUT = httpx.Timeout(connect=4, read=6, write=4, pool=4)
CACHE_TTL, CACHE_MAX = 900, 200
MAX_LIMIT = 5
LEVELS = ("beginner", "intermediate")
U = "Japanese lessons, replies in romaji: say the English, then 'In Japanese:' and the romaji."

log = get_logger("JAPANESE")


def _check(q) -> None:
    if to_romaji("ありがとうございます") != "arigatou gozaimasu":
        raise RuntimeError("romaji converter self-test failed (fugashi/unidic-lite)")
    rows = q("SELECT count(*) FROM t")[0][0]
    if rows < MIN_ROWS:
        raise RuntimeError(f"{REPO} has only {rows} rows (expected >= {MIN_ROWS})")
    bad = q("SELECT count(*) FROM t WHERE NOT regexp_full_match(romaji, '[\\x20-\\x7E]+')")[0][0]
    if bad:
        raise RuntimeError(f"{REPO} has {bad} romaji rows with non-ASCII text")
    log.info(f"{REPO} rows={rows}")


_q = HFTable(REPO, "Japanese phrases", {"t": "SELECT id, english, romaji, topic, level, lower(english) AS le FROM {src}"}, on_ready=_check).q
_cache: dict[str, tuple[float, list]] = {}


def _say(english: str, romaji: str) -> str:
    en = english.strip()
    ro = romaji.strip()
    if ro and ro[-1] not in ".?!":
        ro += "."
    out = f"{en}{'' if en[-1:] in '.?!' else '.'} In Japanese: {ro}"
    if has_japanese(out):
        raise RuntimeError("reply would contain kana or kanji; refusing so the English voice is not garbled")
    return out


def _topics() -> list[str]:
    return [r[0] for r in _q("SELECT topic FROM t GROUP BY 1 ORDER BY count(*) DESC", [])]


def _level(level: str | None) -> str | None:
    if level is None or not str(level).strip():
        return None
    lv = str(level).strip().lower()
    if lv not in LEVELS:
        raise ValueError(f"level {level!r} is not known; use one of {', '.join(LEVELS)}")
    return lv


def _fmt_rows(rows: list) -> str:
    note(*[r[0] for r in rows])
    return "\n".join(_say(en, ro) for _, en, ro in rows)


def _phrase_random(topic: str | None = None, level: str | None = "beginner") -> str:
    lv = _level(level)
    where, params = [], []
    if topic and topic.strip():
        tp = topic.strip().lower()
        if tp not in _topics():
            raise ValueError(f"topic {topic!r} is not known; topics: {_phrase_topics()}")
        where.append("topic = ?")
        params.append(tp)
    if lv:
        where.append("level = ?")
        params.append(lv)
    rows = _q("SELECT id, english, romaji FROM t" + (" WHERE " + " AND ".join(where) if where else "")
              + " USING SAMPLE 1 ROWS", params) if not where else _q(
        "SELECT id, english, romaji FROM t WHERE " + " AND ".join(where) + " ORDER BY random() LIMIT 1", params)
    if not rows:
        raise ValueError(f"no phrase for topic={topic!r} level={level!r}")
    return _fmt_rows(rows)


def _phrase_search(query: str, limit: int = 3, level: str | None = None) -> str:
    if not 1 <= int(limit) <= MAX_LIMIT:
        raise ValueError(f"limit {limit} is out of range: use 1 to {MAX_LIMIT}")
    words = re.findall(r"[a-z0-9']+", (query or "").lower())
    if not words:
        raise ValueError("query must contain English or romaji words")
    lv = _level(level)
    lvl, lp = (" AND level = ?", [lv]) if lv else ("", [])
    pats = [r"\b" + re.escape(w) + r"\b" for w in words]
    for col in ("le", "romaji"):
        rows = _q(
            f"SELECT id, english, romaji FROM t WHERE " + " AND ".join(f"regexp_matches({col}, ?)" for _ in pats)
            + lvl + " ORDER BY length(english), id LIMIT ?", pats + lp + [int(limit)])
        if rows:
            return _fmt_rows(rows)
    return (f"No phrase contains all of {words}. Try fewer words, japanese_word for a single word, or "
            "translate it yourself and call japanese_to_romaji.")


def _phrase_topics() -> str:
    rows = _q("SELECT topic, count(*), count(*) FILTER (WHERE level = 'beginner') FROM t GROUP BY 1 ORDER BY 2 DESC", [])
    return "Japanese phrase topics: " + "; ".join(f"{t} ({n}, {b} beginner)" for t, n, b in rows) + "."


@mcp.tool(name="japanese_phrase", description=f"{U} A practice phrase: random by topic (greetings, food, travel, shopping, time, weather, family, feelings, school and work, home, numbers, general) and level (beginner, intermediate), or search with query in English or romaji (e.g. 'thank you').")
@logged(log)
def japanese_phrase(query: str | None = None, topic: str | None = None, level: str | None = None, limit: int = 3) -> str:
    if query and query.strip():
        if topic:
            raise ValueError("give either query (search) or topic (random), not both")
        return _phrase_search(query, limit, level)
    return _phrase_random(topic, level or "beginner")


def _jisho(keyword: str) -> list:
    hit = _cache.get(keyword)
    if hit and time.monotonic() - hit[0] < CACHE_TTL:
        return hit[1]
    r = httpx.get(JISHO, params={"keyword": keyword}, timeout=TIMEOUT, headers={"User-Agent": "stackchan-japanese/1.0"})
    r.raise_for_status()
    data = r.json().get("data")
    if not isinstance(data, list):
        raise RuntimeError("jisho.org returned unexpected data")
    if len(_cache) >= CACHE_MAX:
        _cache.pop(next(iter(_cache)))
    _cache[keyword] = (time.monotonic(), data)
    return data


def _plain(d: str) -> str:
    return re.sub(r"\s*\([^)]*\)", "", d).strip()


@mcp.tool(name="japanese_word", description=f"{U} How to say an English word in Japanese (live dictionary).")
@logged(log)
def japanese_word(english: str, limit: int = 3) -> str:
    if not 1 <= int(limit) <= MAX_LIMIT:
        raise ValueError(f"limit {limit} is out of range: use 1 to {MAX_LIMIT}")
    kw = re.sub(r"\s+", " ", (english or "").strip().lower())
    if not kw or has_japanese(kw):
        raise ValueError("english must be an English word or phrase (use japanese_to_romaji for Japanese text)")
    cands = []
    for i, e in enumerate(_jisho(kw)):
        jp = (e.get("japanese") or [{}])[0]
        reading = jp.get("reading") or (jp.get("word") if jp.get("word") and re.fullmatch(r"[\u3040-\u30ffー]+", jp["word"]) else None)
        if not reading:
            continue
        try:
            ro = kana_to_romaji(reading)
        except ValueError:
            continue
        defs = [d for s in (e.get("senses") or [])[:2] for d in s.get("english_definitions", []) if not has_japanese(d)]
        if not defs:
            continue
        plain = list(dict.fromkeys(_plain(d) for d in defs if _plain(d)))
        exact = 0 if plain[:1] and plain[0].lower() in (kw, "to " + kw) else (1 if any(d.lower() in (kw, "to " + kw) for d in plain) else 2)
        jlpt = min([int(x[-1]) for x in e.get("jlpt") or [] if x[-1:].isdigit()] or [0])
        cands.append((exact, 0 if e.get("is_common") else 1, -jlpt, i, ro, plain[:2]))
    if not cands:
        return f"jisho.org has no Japanese word for {english!r}. Translate it yourself and call japanese_to_romaji."
    cands.sort()
    seen, out = set(), []
    for *_, ro, defs in cands:
        if ro not in seen:
            seen.add(ro)
            out.append(f"{ro} ({', '.join(defs)})")
        if len(out) >= int(limit):
            break
    en = english.strip()
    res = f"{en[:1].upper()}{en[1:]}. In Japanese: {out[0]}." + (f" Also: {'; '.join(out[1:])}." if out[1:] else "")
    if has_japanese(res):
        raise RuntimeError("reply would contain kana or kanji")
    return res


@mcp.tool(name="japanese_to_romaji", description=f"{U} After you translate a sentence into Japanese yourself, pass the Japanese text and the English to get speakable romaji; never speak kana or kanji.")
@logged(log)
def japanese_to_romaji(text: str, english: str | None = None) -> str:
    if not text or not has_japanese(text):
        raise ValueError("text must contain Japanese (kana or kanji)")
    if len(text) > 200:
        raise ValueError("text is too long: send at most 200 characters (one or two sentences)")
    ro = to_romaji(text)
    if english and english.strip():
        return _say(english, ro)
    return f"In Japanese: {ro}{'' if ro[-1:] in '.?!' else '.'}"
