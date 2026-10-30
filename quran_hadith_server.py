"""Quran & Hadith MCP: short spoken plain-text replies. Keyless APIs: api.alquran.cloud, fawazahmed0/hadith-api (jsDelivr). Fail loudly."""
from __future__ import annotations

import random
import urllib.parse
import re

import httpx
from fastmcp import FastMCP

QURAN = "https://api.alquran.cloud/v1"
HADITH = "https://cdn.jsdelivr.net/gh/fawazahmed0/hadith-api@1/editions"
EN, AR = "en.sahih", "quran-simple-clean"
MAX_AYAT = 5
MAX_CHARS = 1000
TIMEOUT = httpx.Timeout(connect=3, read=5, write=3, pool=3)
# collection -> (spoken name, last hadith number)
COLLECTIONS = {
    "bukhari": ("Sahih al-Bukhari", 7563), "muslim": ("Sahih Muslim", 7563),
    "abudawud": ("Sunan Abu Dawud", 5274), "tirmidhi": ("Jami at-Tirmidhi", 3956),
    "nasai": ("Sunan an-Nasai", 5758), "ibnmajah": ("Sunan Ibn Majah", 4341),
    "malik": ("Muwatta Malik", 1858), "nawawi": ("Forty Hadith of an-Nawawi", 42),
    "qudsi": ("Forty Hadith Qudsi", 40),
}

mcp = FastMCP("QuranHadith")
_surahs: list[dict] = []


def _get(url: str) -> dict:
    r = httpx.get(url, timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": "stackchan-quran/1.0"})
    r.raise_for_status()
    return r.json()


def _norm(s: str) -> str:
    s = re.sub(r"[^a-z]", "", s.lower().replace("surah", "").replace("sura", ""))
    for p in ("al", "an", "ar", "as", "at", "ad", "az", "ash", "adh"):
        if s.startswith(p) and len(s) > len(p) + 2:
            s = s[len(p):]
            break
    return s.rstrip("h")


def _skel(s: str) -> str:
    """Consonant skeleton so spoken spellings match (Yasin/Yaseen, Kahf/Kahaf)."""
    return re.sub(r"(.)\1+", r"\1", re.sub(r"[aeiouyw]", "", s))


def _surah_list() -> list[dict]:
    global _surahs
    if not _surahs:
        _surahs = _get(f"{QURAN}/surah")["data"]
    return _surahs


def _surah_num(surah: str | int) -> int:
    s = str(surah).strip()
    if s.isdigit():
        n = int(s)
        if not 1 <= n <= 114:
            raise ValueError("surah number must be 1..114")
        return n
    key = _norm(s)
    for x in _surah_list():
        if key in (_norm(x["englishName"]), _norm(x["englishNameTranslation"])):
            return x["number"]
    hits = [x["number"] for x in _surah_list() if _skel(key) == _skel(_norm(x["englishName"]))]
    if len(hits) == 1:
        return hits[0]
    raise ValueError(f"unknown surah {s!r}; give a number 1-114 or a name like Al-Baqarah")


@mcp.tool()
def read_quran(surah: str, ayah: int = 1, count: int = 3, arabic: bool = True) -> str:
    """Read Quran verses aloud. surah: name (Al-Baqarah, Yasin, The Cow) or number, or 'surah:ayah' like '2:255' (then ayah is ignored). count 1-5 verses (default 3). arabic: include Arabic text if it fits. Reply ends '(quran 2:255)'; to continue, call again with the next ayah."""
    s = str(surah).strip()
    m = re.fullmatch(r"(\d{1,3})\s*[:.]\s*(\d{1,3})", s)
    if m:
        s, ayah = m.group(1), int(m.group(2))
    if not 1 <= count <= MAX_AYAT:
        raise ValueError(f"count must be 1..{MAX_AYAT}")
    n = _surah_num(s)
    info = _surah_list()[n - 1]
    total = info["numberOfAyahs"]
    if not 1 <= ayah <= total:
        raise ValueError(f"surah {n} has {total} ayat")
    count = min(count, total - ayah + 1)
    data = _get(f"{QURAN}/surah/{n}/editions/{AR},{EN}?offset={ayah - 1}&limit={count}")["data"]
    ar = {a["numberInSurah"]: a["text"].lstrip("\ufeff") for a in data[0]["ayahs"]}
    en = {a["numberInSurah"]: a["text"] for a in data[1]["ayahs"]}
    def build(k: int, with_ar: bool) -> str:
        keys = sorted(en)[:k]
        last = ayah + k - 1
        ref = f"(quran {n}:{ayah}" + (f"-{last})" if k > 1 else ")")
        more = f"Next: {n}:{last + 1}" if last < total else "End of surah."
        body = ([f"{i}. {ar[i]}" for i in keys] if with_ar else []) + [f"{i}. {en[i]}" for i in keys]
        return "\n".join([head, *body, ref, more])

    head = f"Surah {info['englishName']} ({info['englishNameTranslation']})"
    if arabic:
        for k in range(count, 0, -1):
            out = build(k, True)
            if len(out) <= MAX_CHARS:
                return out
    for k in range(count, 0, -1):
        out = build(k, False)
        if len(out) <= MAX_CHARS or k == 1:
            return out


@mcp.tool()
def search_quran(query: str, limit: int = 3) -> str:
    """Find Quran verses by English words (e.g. 'patience', 'mercy'). limit 1-5 (default 3). One line per verse ending '(quran s:a)'; use read_quran to read more."""
    q = (query or "").strip()
    if not q:
        raise ValueError("query required")
    if not 1 <= limit <= 5:
        raise ValueError("limit must be 1..5")
    r = httpx.get(f"{QURAN}/search/{urllib.parse.quote(q)}/all/{EN}", timeout=TIMEOUT)
    if r.status_code == 404:
        return f"No Quran verses found for {q!r}."
    r.raise_for_status()
    d = r.json()["data"]
    lines = [f"{d['count']} verses mention {q!r}. First {min(limit, d['count'])}:"]
    for m in d["matches"][:limit]:
        t = m["text"] if len(m["text"]) <= 220 else m["text"][:217] + "..."
        lines.append(f"{t} ({m['surah']['englishName']}, quran {m['surah']['number']}:{m['numberInSurah']})")
    return "\n".join(lines)


def _coll(collection: str) -> str:
    c = re.sub(r"[^a-z]", "", (collection or "").lower()).replace("sahih", "").replace("sunan", "")
    alias = {"abudaud": "abudawud", "dawud": "abudawud", "ibnmaja": "ibnmajah", "muwatta": "malik", "nisai": "nasai", "nawawi40": "nawawi", "forty": "nawawi", "qudsi40": "qudsi"}
    c = alias.get(c, c)
    if c not in COLLECTIONS:
        raise ValueError(f"collection must be one of: {', '.join(COLLECTIONS)}")
    return c


def _hadith(c: str, number: int, arabic: bool) -> str:
    name, last = COLLECTIONS[c]
    if not 1 <= number <= last:
        raise ValueError(f"{c} hadith number must be 1..{last}")
    en = _get(f"{HADITH}/eng-{c}/{number}.min.json")["hadiths"]
    if not en or not en[0]["text"].strip():
        raise LookupError(f"{c} {number} has no English text in this source; try a nearby number")
    h = en[0]
    text = re.sub(r"\s+", " ", h["text"].replace("\ufdfa", "peace be upon him")).strip()
    grade = ", ".join(f"{g['grade']} ({g['name']})" for g in (h.get("grades") or [])[:1])
    ref = f"({c} {number})"
    cut = len(text) > MAX_CHARS - 150
    if cut:
        text = text[: MAX_CHARS - 150].rsplit(" ", 1)[0] + "... (cut short)"
    parts = [f"{name}, hadith {number}:", text]
    if grade:
        parts.append(f"Grade: {grade}")
    if arabic and not cut:
        ar = re.sub(r"\s+", " ", _get(f"{HADITH}/ara-{c}/{number}.min.json")["hadiths"][0]["text"]).strip()
        if len("\n".join(parts)) + len(ar) <= MAX_CHARS:
            parts.insert(1, ar)
    parts.append(ref)
    return "\n".join(parts)


@mcp.tool()
def get_hadith(collection: str, number: int, arabic: bool = False) -> str:
    """One hadith by collection and number. collection: bukhari|muslim|abudawud|tirmidhi|nasai|ibnmajah|malik|nawawi|qudsi. arabic: add Arabic if short. Reply ends '(bukhari 1)'."""
    return _hadith(_coll(collection), int(number), arabic)


@mcp.tool()
def random_hadith(collection: str = "nawawi") -> str:
    """A random hadith to read aloud. collection default nawawi (Forty Hadith); also bukhari, muslim, qudsi, etc."""
    c = _coll(collection)
    tried = []
    for n in random.sample(range(1, COLLECTIONS[c][1] + 1), 5):
        try:
            return _hadith(c, n, False)
        except LookupError:
            tried.append(n)
    raise LookupError(f"{c} numbers {tried} all lacked English text")


if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
