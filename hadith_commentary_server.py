"""Hadith commentary MCP: Ibn Abi Jamrah's Bahjat al-Nufus (Arabic) via on-demand reads of hf://datasets/MTerryJack/bahjat-al-nufus.
Every call logs one line to stderr (tool, args, row ids, ms, error) via mcp_tool_log; if Hugging Face cannot be reached the call fails with a spoken error."""
from __future__ import annotations

import math
import re

from app import mcp
from hf_data import HFTable

import bookmarks

from mcp_tool_log import get_logger, logged, note

REPO = "MTerryJack/bahjat-al-nufus"
MAX_LIMIT = 5
HADITH_CHARS = 500
PAGE_CHARS = 1000
SNIPPET_CHARS = 160
NOTE = "[Classical Arabic, OCR text: translate or explain it for the listener.]"
# Arabic normalisation (same in SQL and Python): drop tashkeel/tatweel, unify alif, ya, ta marbuta, hamza seats.
_DIAC = "[\u064B-\u0652\u0670\u0640]"
_FROM, _TO = "أإآٱىةؤئ", "اااايهوي"
# English topic words -> Arabic search terms (normalised spelling).
EN_AR = {
    "dream": "الرويا", "dreams": "الرويا", "vision": "الرويا", "prayer": "الصلاه", "salat": "الصلاه", "fasting": "الصوم", "ramadan": "رمضان",
    "charity": "الصدقه", "zakat": "الزكاه", "intention": "النيه", "intentions": "النيه", "faith": "الايمان", "revelation": "الوحي",
    "knowledge": "العلم", "patience": "الصبر", "hajj": "الحج", "paradise": "الجنه", "heaven": "الجنه", "hell": "النار", "ablution": "الوضوء",
    "wudu": "الوضوء", "angel": "الملك", "angels": "الملايكه", "gabriel": "جبريل", "jibril": "جبريل", "quran": "القران", "mercy": "الرحمه",
    "repentance": "التوبه", "sin": "الذنب", "sins": "الذنوب", "prophet": "النبي", "mosque": "المسجد", "death": "الموت", "jihad": "الجهاد",
    "marriage": "النكاح", "neighbour": "الجار", "neighbor": "الجار", "honesty": "الصدق", "truth": "الصدق", "friday": "الجمعه",
}
NORM_SQL = "translate(regexp_replace({c}, '" + _DIAC + "', '', 'g'), '" + _FROM + "', '" + _TO + "')"

log = get_logger("HADITH_COMMENTARY")


def _check(q) -> None:
    n, lo, hi = q("SELECT count(*), min(hadith_number), max(hadith_number) FROM bahja")[0]
    if n < 1:
        raise RuntimeError(f"{REPO} has 0 rows")
    log.info(f"{REPO} rows={n} hadith_number={lo}-{hi}")


_q = HFTable(REPO, "Bahjat al-Nufus", {"bahja": lambda src, cols: "SELECT hadith_number, covers, bukhari_number, hadith_ar, commentary_ar, volume, alignment_confidence, "
             + NORM_SQL.format(c="hadith_ar || ' ' || commentary_ar") + " AS nt FROM " + src}, on_ready=_check).q


def _norm(s: str) -> str:
    s = re.sub(_DIAC, "", s)
    return s.translate(str.maketrans(_FROM, _TO))


def _speak(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _cut(t: str, n: int) -> str:
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + "…"


def _ref(n: int, covers: str, vol: int, conf: str) -> str:
    h = f"hadith {covers}" if covers != str(n) else f"hadith {n}"
    c = "" if conf == "high" else f", alignment {conf}"
    return f"(Bahjat al-Nufus, {h}, vol {vol}{c})"


def _read_impl(hadith_number: int, part: int, mark: bool = False) -> str:
    n, p = int(hadith_number), int(part)
    lo, hi = _q("SELECT min(hadith_number), max(hadith_number) FROM bahja", [])[0]
    if not lo <= n <= hi:
        raise ValueError(f"hadith_number {n} is out of range: Bahjat al-Nufus has hadith {lo} to {hi}.")
    rows = _q("SELECT hadith_number, covers, bukhari_number, hadith_ar, commentary_ar, volume, alignment_confidence FROM bahja WHERE hadith_number <= ? ORDER BY hadith_number DESC LIMIT 1", [n])
    hn, cov, bk, had, com, vol, conf = rows[0]
    a, b = (int(x) for x in (cov.split("-") + [cov])[:2])
    if not a <= n <= b:
        raise LookupError(f"Hadith {n} is not in the dataset (nearest below is hadith {cov}). Try bahjat_read(hadith_number={hn}).")
    note(hn)
    com = _speak(com)
    pages = max(1, math.ceil(len(com) / PAGE_CHARS))
    if not 1 <= p <= pages:
        raise ValueError(f"part {p} is out of range: hadith {cov} commentary has parts 1 to {pages}.")
    chunk = com[(p - 1) * PAGE_CHARS: p * PAGE_CHARS]
    head = f"Hadith (Bukhari {bk}): {_cut(_speak(had), HADITH_CHARS)}\n" if p == 1 else ""
    nxt = f"\nNext: bahjat_read(hadith_number={hn}, part={p + 1})" if p < pages else f"\nEnd of commentary on hadith {cov}. Next hadith: bahjat_read(hadith_number={b + 1})." if b < hi else f"\nEnd of commentary on hadith {cov}, the last in the book."
    if mark:
        bookmarks.record("bahjat", f"hadith {cov}, commentary part {p} of {pages}",
                         {"hadith_number": hn, "part": p + 1} if p < pages else ({"hadith_number": b + 1} if b < hi else None))
    return f"{NOTE}\n{head}Commentary part {p}/{pages}: {chunk} {_ref(hn, cov, vol, conf)}{nxt}"


def _terms(q: str) -> list[str]:
    s = _norm(q.lower())
    for en in sorted(EN_AR, key=len, reverse=True):
        s = re.sub(rf"\b{re.escape(en)}\b", " " + EN_AR[en] + " ", s)
    left = re.findall(r"[a-z]{2,}", s)
    if left:
        raise ValueError(f"query words {left} are not known in English; use Arabic keywords (e.g. الرؤيا, الصلاة) or one of: {', '.join(sorted(EN_AR))}.")
    return list(dict.fromkeys(re.findall(r"[\u0621-\u064A0-9]+", s)))


def _snippet(nt: str, term: str) -> str:
    t = _speak(nt)
    pos = t.find(term)
    start = max(0, pos - 60) if pos >= 0 else 0
    snip = t[start:start + SNIPPET_CHARS]
    if start > 0:
        snip = "…" + snip.split(" ", 1)[-1]
    if start + SNIPPET_CHARS < len(t):
        snip = snip.rsplit(" ", 1)[0] + "…"
    return snip


def _search_impl(query: str, limit: int) -> str:
    # BM25-style ranking (k1=1.2, b=0.75) over normalised Arabic; all terms must appear (substring, since OCR splits words).
    q = (query or "").strip()
    if not q:
        raise ValueError("query must be non-empty: Arabic keywords (e.g. الرؤيا) or a listed English topic word.")
    if not 1 <= int(limit) <= MAX_LIMIT:
        raise ValueError(f"limit {limit} is out of range: use 1 to {MAX_LIMIT}.")
    terms = _terms(q)
    if not terms:
        raise ValueError(f"query {q!r} has no Arabic letters, numbers or known English topic words.")
    pats = [re.escape(t) for t in terms]
    tf_cols = ", ".join(f"len(regexp_extract_all(nt, ?)) AS tf{i}" for i in range(len(terms)))
    where = " AND ".join(f"tf{i} > 0" for i in range(len(terms)))
    df_cols = ", ".join(f"count(*) FILTER (WHERE tf{i} > 0)" for i in range(len(terms)))
    base = f"WITH d AS (SELECT hadith_number, covers, volume, alignment_confidence, nt, len(string_split(nt, ' ')) AS dl, {tf_cols} FROM bahja)"
    stats = _q(f"{base} SELECT count(*), avg(dl), {df_cols} FROM d", pats)[0]
    n, avgdl, dfs = stats[0], stats[1], stats[2:]
    idf = [math.log(1 + (n - df + 0.5) / (df + 0.5)) for df in dfs]
    score = " + ".join(f"{idf[i]!r} * tf{i} * 2.2 / (tf{i} + 1.2 * (0.25 + 0.75 * dl / {avgdl!r}))" for i in range(len(terms)))
    phrase = " ".join(terms)
    rows = _q(
        f"{base} SELECT hadith_number, covers, volume, alignment_confidence, nt FROM d WHERE {where} ORDER BY ({score}) + CASE WHEN contains(nt, ?) THEN 1.0 ELSE 0 END DESC, hadith_number LIMIT ?",
        pats + [phrase, int(limit)],
    )
    note(*[r[0] for r in rows])
    if not rows:
        return f"No hadith or commentary contains all of {terms} (searched as {phrase!r}). Try fewer or other Arabic words, or bahjat_read()."
    out = [f"{_snippet(t, phrase if phrase in t else terms[0])} {_ref(h, c, v, cf)} -> bahjat_read(hadith_number={h})" for h, c, v, cf, t in rows]
    return f"{NOTE}\nSearched for {phrase!r}:\n" + "\n".join(out)


def _random_impl() -> str:
    hn = _q("SELECT hadith_number FROM bahja WHERE alignment_confidence = 'high' ORDER BY random() LIMIT 1", [])[0][0]
    return _read_impl(hn, 1)


def _read_or_random(hadith_number: int | None, part: int) -> str:
    return _random_impl() if hadith_number is None else _read_impl(hadith_number, part, True)


@mcp.tool(name="bahjat_read", description="Ibn Abi Jamrah's Bahjat al-Nufus (Arabic commentary on Bukhari hadith): read hadith 1-287 with its commentary in parts, or a random one with no number. Translate the Arabic when speaking.")
@logged(log)
def bahjat_read(hadith_number: int | None = None, part: int = 1) -> str:
    return _read_or_random(hadith_number, part)


@mcp.tool(name="bahjat_search", description="Bahjat al-Nufus: find what Ibn Abi Jamrah says about a topic (Arabic or English words, e.g. dreams, intention).")
@logged(log)
def bahjat_search(query: str, limit: int = 3) -> str:
    return _search_impl(query, limit)
