"""Prophet dreams MCP: accounts of people who saw the Prophet (saas) in a dream, original Arabic, from
hf://datasets/MTerryJack/prophet-dreams (DuckDB, in memory). dream_number is per source; dreams_sources lists them.
Every call logs one line to stderr (tool, args, row ids, ms, error) via mcp_tool_log; a failed dataset load exits at startup."""

from __future__ import annotations

import contextvars
import math
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

import duckdb
from fastmcp import FastMCP

import bookmarks

from mcp_tool_log import get_logger, hf_revision, logged, note

REPO = "MTerryJack/prophet-dreams"
DEFAULT_SOURCE = "al-Mara'i al-Hisan (Ibn Abi Jamrah)"  # dreams_read without source, kept for compatibility
MAX_LIMIT = 5
PAGE_CHARS = 1000
SNIPPET_CHARS = 160
CALL_TIMEOUT_S = 8.0
NOTE = "[Classical Arabic (al-Mara'i text is OCR): translate or explain it for the listener.]"
# Arabic normalisation (same in SQL and Python): drop tashkeel/tatweel, unify alif, ya, ta marbuta, hamza seats.
_DIAC = "[\u064B-\u0652\u0670\u0640]"
_FROM, _TO = "أإآٱىةؤئ", "اااايهوي"
NORM_SQL = "translate(regexp_replace({c}, '" + _DIAC + "', '', 'g'), '" + _FROM + "', '" + _TO + "')"
# Extra names a caller may use for a source (English or Arabic); matched after _key().
ALIASES = {
    "al-Mara'i al-Hisan (Ibn Abi Jamrah)": ["maraai", "marai", "ibn abi jamra", "ibn abi jamrah", "abi jamra", "المرائي الحسان", "ابن ابي جمره"],
    "Sa'adat al-Darayn (al-Nabhani)": ["saadat", "sa'adat al-darayn", "nabhani", "nabahani", "سعاده الدارين", "النبهاني"],
    "al-Mustaghithin billah (Ibn Bashkuwal)": ["mustaghithin", "bashkuwal", "ibn bashkuwal", "المستغيثين", "ابن بشكوال"],
    "al-Manamat (Ibn Abi al-Dunya)": ["manamat", "ibn abi al-dunya", "ibn abi dunya", "abi al dunya", "المنامات", "ابن ابي الدنيا"],
}
# English topic words -> Arabic search terms (normalised spelling), so English queries work for common topics.
EN_AR = {
    "abu bakr": "ابي بكر", "umar": "عمر", "uthman": "عثمان", "ali": "علي", "aisha": "عايشه", "husayn": "الحسين", "hussein": "الحسين",
    "hasan": "الحسن", "gabriel": "جبريل", "jibril": "جبريل", "quran": "القران", "prayer": "الصلاه", "salat": "الصلاه",
    "blessings": "الصلاه علي", "fasting": "صايم", "fast": "صايم", "hajj": "الحج", "pilgrimage": "الحج", "medina": "المدينه",
    "mecca": "مكه", "makkah": "مكه", "kaaba": "الكعبه", "grave": "القبر", "tomb": "القبر", "milk": "لبن", "water": "الماء",
    "honey": "عسل", "bread": "خبز", "debt": "دين", "illness": "مرض", "sick": "مرض", "deaf": "طرش", "blind": "اعمي",
    "poverty": "فاقه", "hunger": "جوع", "tobacco": "التنباك", "intercession": "شفاعه", "paradise": "الجنه", "heaven": "الجنه",
    "hell": "النار", "fire": "النار", "hadith": "حديث", "companions": "اصحاب", "dua": "الدعاء", "supplication": "الدعاء",
    "garlic": "الثوم", "onion": "البصل", "shirt": "قميص", "beard": "لحيته", "face": "وجهه", "smile": "تبسم",
}

log = get_logger("PROPHET_DREAMS")
mcp = FastMCP("ProphetDreams")
_lock = threading.Lock()
_con: duckdb.DuckDBPyConnection | None = None
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dreams")


def _load() -> None:
    # ~0.3MB parquet, ~200 rows, pinned to the current HF revision: one in-memory table with a normalised search column.
    global _con
    t0 = time.monotonic()
    rev = hf_revision(REPO)
    url = f"hf://datasets/{REPO}@{rev}/data/train.parquet"
    con = duckdb.connect(config={"extension_directory": "/opt/duckdb/extensions"})
    con.execute("LOAD httpfs")
    con.execute(
        "CREATE TABLE dreams AS SELECT id, source, book_ar, dream_number, covers, topic, ref_ar, text_ar, licence, "
        + NORM_SQL.format(c="text_ar") + f" AS nt FROM read_parquet('{url}')"
    )
    counts = con.execute("SELECT source, count(*) FROM dreams GROUP BY 1 ORDER BY 1").fetchall()
    if not counts:
        raise RuntimeError(f"Loaded 0 rows from {url}")
    if DEFAULT_SOURCE not in dict(counts):
        raise RuntimeError(f"Default source {DEFAULT_SOURCE!r} missing from {url}")
    unknown = set(dict(counts)) - set(ALIASES)
    if unknown:
        raise RuntimeError(f"Sources without ALIASES entry: {sorted(unknown)}")
    with _lock:
        _con = con
    log.info(f"loaded {REPO}@{rev[:12]} rows={sum(c for _, c in counts)} " + " ".join(f"[{s}]={c}" for s, c in counts) + f" ms={(time.monotonic() - t0) * 1000:.0f}")


def _conn() -> duckdb.DuckDBPyConnection:
    with _lock:
        if _con is None:
            raise RuntimeError("Dreams dataset not loaded (server started without _load)")
        return _con


def _run(fn, *args):
    fut = _pool.submit(contextvars.copy_context().run, fn, *args)
    try:
        return fut.result(timeout=CALL_TIMEOUT_S)
    except FuturesTimeout:
        fut.cancel()
        raise TimeoutError(f"dreams tool exceeded {CALL_TIMEOUT_S:.0f}s") from None


def _q(sql: str, params: list) -> list:
    con = _conn()
    with _lock:
        return con.execute(sql, params).fetchall()


def _norm(s: str) -> str:
    s = re.sub(_DIAC, "", s)
    return s.translate(str.maketrans(_FROM, _TO))


def _key(s: str) -> str:
    return re.sub(r"[\s'`\u2019\-_.()]+", "", _norm(s.lower())).replace("ال", "")


def _speak(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _short(src: str) -> str:
    return src.split(" (")[0]


def _terms(q: str) -> list[str]:
    s = _norm(q.lower())
    for en in sorted(EN_AR, key=len, reverse=True):
        s = re.sub(rf"\b{re.escape(en)}\b", " " + EN_AR[en] + " ", s)
    left = re.findall(r"[a-z]{2,}", s)
    if left:
        raise ValueError(f"query words {left} are not known in English; use Arabic keywords (e.g. الحج, جبريل) or one of: {', '.join(sorted(EN_AR))}.")
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


def _resolve(source: str | None) -> str | None:
    s = (source or "").strip()
    if not s:
        return None
    k = _key(s)
    known = [r[0] for r in _q("SELECT DISTINCT source FROM dreams ORDER BY 1", [])]
    hit = [src for src in known if k and any(k in _key(n) for n in [src, *ALIASES[src]])]
    if not hit:
        raise LookupError(f"Unknown source {s!r}. Valid sources (any part of the name works): {'; '.join(known)}. See dreams_sources().")
    if len(hit) > 1:
        raise LookupError(f"Source {s!r} matches several: {'; '.join(hit)}. Use more of the name.")
    return hit[0]


def _ref(src: str, covers: str, ref: str | None = None) -> str:
    return f"({_short(src)}, dream {covers}{', ' + ref if ref and src != DEFAULT_SOURCE else ''})"


def _arg(src: str) -> str:
    return "" if src == DEFAULT_SOURCE else f', source="{_short(src)}"'


def _read_impl(dream_number: int, part: int, source: str | None, mark: bool = False) -> str:
    n, p = int(dream_number), int(part)
    src = _resolve(source) or DEFAULT_SOURCE
    lo, last = _q("SELECT min(dream_number), max(CAST(split_part(covers, '-', -1) AS INT)) FROM dreams WHERE source = ?", [src])[0]
    if not lo <= n <= last:
        raise ValueError(f"dream_number {n} is out of range: {src} has dreams {lo} to {last}. See dreams_sources() for other sources.")
    rid, dn, cov, topic, ref, txt = _q("SELECT id, dream_number, covers, topic, ref_ar, text_ar FROM dreams WHERE source = ? AND dream_number <= ? ORDER BY dream_number DESC LIMIT 1", [src, n])[0]
    note(rid)
    txt = _speak(txt)
    pages = max(1, math.ceil(len(txt) / PAGE_CHARS))
    if not 1 <= p <= pages:
        raise ValueError(f"part {p} is out of range: dream {cov} of {_short(src)} has parts 1 to {pages}.")
    merged = f" Dream {n} is inside dream {cov} (OCR lost the heading)." if cov != str(n) else ""
    head = f"Topic: {topic}. " if topic and p == 1 else ""
    end = int(cov.split("-")[-1])
    nxt = (f"\nNext: dreams_read(dream_number={dn}, part={p + 1}{_arg(src)})" if p < pages
           else f"\nNext: dreams_read(dream_number={end + 1}{_arg(src)})" if end < last else "\nEnd of this source. Others: dreams_sources().")
    if mark:
        bookmarks.record("dreams", f"{_short(src)}, dream {cov}, part {p} of {pages}",
                         {"dream_number": dn, "part": p + 1, "source": _short(src)} if p < pages else ({"dream_number": end + 1, "source": _short(src)} if end < last else None))
    return f"{NOTE}{merged}\n{head}Part {p}/{pages}: {txt[(p - 1) * PAGE_CHARS: p * PAGE_CHARS]} {_ref(src, cov, ref)}{nxt}"


def _search_impl(query: str, limit: int, source: str | None) -> str:
    q = (query or "").strip()
    if not q:
        raise ValueError("query must be non-empty: Arabic keywords (e.g. الحج) or a listed English topic word.")
    if not 1 <= int(limit) <= MAX_LIMIT:
        raise ValueError(f"limit {limit} is out of range: use 1 to {MAX_LIMIT}.")
    terms = _terms(q)
    if not terms:
        raise ValueError(f"query {q!r} has no Arabic letters, numbers or known English topic words.")
    src = _resolve(source)
    f, fp = (" AND source = ?", [src]) if src else ("", [])
    pats = [re.escape(t) for t in terms]
    tf_cols = ", ".join(f"len(regexp_extract_all(nt, ?)) AS tf{i}" for i in range(len(terms)))
    where = " AND ".join(f"tf{i} > 0" for i in range(len(terms)))
    df_cols = ", ".join(f"count(*) FILTER (WHERE tf{i} > 0)" for i in range(len(terms)))
    base = f"WITH d AS (SELECT id, source, dream_number, covers, nt, len(string_split(nt, ' ')) AS dl, {tf_cols} FROM dreams WHERE true{f})"
    stats = _q(f"{base} SELECT count(*), avg(dl), {df_cols} FROM d", pats + fp)[0]
    n, avgdl, dfs = stats[0], stats[1], stats[2:]
    idf = [math.log(1 + (n - df + 0.5) / (df + 0.5)) for df in dfs]
    score = " + ".join(f"{idf[i]!r} * tf{i} * 2.2 / (tf{i} + 1.2 * (0.25 + 0.75 * dl / {avgdl!r}))" for i in range(len(terms)))
    phrase = " ".join(terms)
    rows = _q(
        f"{base} SELECT id, source, dream_number, covers, nt FROM d WHERE {where} ORDER BY ({score}) + CASE WHEN contains(nt, ?) THEN 1.0 ELSE 0 END DESC, source, dream_number LIMIT ?",
        pats + fp + [phrase, int(limit)],
    )
    note(*[r[0] for r in rows])
    scope = f" in {_short(src)}" if src else ""
    if not rows:
        return f"No dreams{scope} contain all of {terms} (searched as {phrase!r}). Try fewer or other Arabic words, or dreams_random()."
    out = [f"{_snippet(t, phrase if phrase in t else terms[0])} {_ref(s, c)} -> dreams_read(dream_number={d}{_arg(s)})" for _, s, d, c, t in rows]
    return f"{NOTE}\nSearched{scope} for {phrase!r}:\n" + "\n".join(out)


def _random_impl(source: str | None) -> str:
    src = _resolve(source)
    f, fp = (" AND source = ?", [src]) if src else ("", [])
    s, dn = _q(f"SELECT source, dream_number FROM dreams WHERE length(text_ar) > 300{f} ORDER BY random() LIMIT 1", fp)[0]
    return _read_impl(dn, 1, s)


def _sources_impl() -> str:
    rows = _q("SELECT source, any_value(book_ar), count(*), min(dream_number), max(CAST(split_part(covers, '-', -1) AS INT)), any_value(licence) FROM dreams GROUP BY 1 ORDER BY count(*) DESC", [])
    out = [f"- {s} [{b}]: {c} dreams, dream_number {lo}-{hi}; licence {lic}. Read: dreams_read(dream_number={lo}{_arg(s)})" for s, b, c, lo, hi, lic in rows]
    return f"{sum(r[2] for r in rows)} dreams of the Prophet (saas) seen by others, original Arabic. Default source when none is given: {_short(DEFAULT_SOURCE)}.\n" + "\n".join(out)


@mcp.tool()
@logged(log)
def dreams_read(dream_number: int, part: int = 1, source: str | None = None) -> str:
    """Use when the user wants to hear a specific dream in which someone saw the Prophet (saas), e.g. "read dream 12", "the next one", or a dream_number from dreams_search. dream_number counts within one source (ranges: dreams_sources). part pages long dreams (1, 2, ...). Text is classical Arabic: translate or explain it when speaking. Returns one part plus a Next: call to continue. Sources (source= takes any part of the name, English or Arabic): al-Mara'i al-Hisan (Ibn Abi Jamrah, default), Sa'adat al-Darayn (Nabhani), al-Manamat (Ibn Abi al-Dunya), al-Mustaghithin (Ibn Bashkuwal)."""
    return _run(_read_impl, dream_number, part, source, True)


@mcp.tool()
@logged(log)
def dreams_search(query: str, limit: int = 3, source: str | None = None) -> str:
    """Use when the user asks for dreams of the Prophet (saas) about something, e.g. "a dream where the Prophet spoke about Abu Bakr", "someone saw the Prophet and was cured", "dreams about Hajj". query: Arabic keywords (الحج, جبريل, الحسين) or common English topic words (hajj, gabriel, husayn, grave, illness, quran); all words must match. limit 1-5. Returns snippets, each with the dreams_read call that opens it. Sources (source= takes any part of the name, English or Arabic): al-Mara'i al-Hisan (Ibn Abi Jamrah, default), Sa'adat al-Darayn (Nabhani), al-Manamat (Ibn Abi al-Dunya), al-Mustaghithin (Ibn Bashkuwal)."""
    return _run(_search_impl, query, limit, source)


@mcp.tool()
@logged(log)
def dreams_random(source: str | None = None) -> str:
    """Use when the user asks for any dream of the Prophet (saas), e.g. "tell me a dream someone had of the Prophet", "someone dreamt of the Prophet", "a vision of the Prophet in sleep". Optional source. Text is classical Arabic: translate or explain it. Returns one dream with its reference and a Next: call. Sources (source= takes any part of the name, English or Arabic): al-Mara'i al-Hisan (Ibn Abi Jamrah, default), Sa'adat al-Darayn (Nabhani), al-Manamat (Ibn Abi al-Dunya), al-Mustaghithin (Ibn Bashkuwal)."""
    return _run(_random_impl, source)


@mcp.tool()
@logged(log)
def dreams_sources() -> str:
    """Use when the user asks which books the Prophet dreams come from, how many there are, or before reading by number from a source. Returns each source with its Arabic title, count, dream_number range and licence."""
    return _run(_sources_impl)


if __name__ == "__main__":
    try:
        _load()
    except BaseException as e:
        log.critical(f"startup failed: dataset {REPO} did not load: {type(e).__name__}: {e}")
        sys.exit(1)
    mcp.run(transport="stdio", show_banner=False)
