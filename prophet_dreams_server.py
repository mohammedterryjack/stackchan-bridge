"""Prophet dreams MCP: dreams others saw of the Prophet (saas), Arabic, via DuckDB over hf://datasets/MTerryJack/prophet-dreams (sources: al-Mara'i al-Hisan, Sa'adat al-Darayn, al-Mustaghithin billah; dream_number is per source)."""

from __future__ import annotations

import math
import re
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

import duckdb
from fastmcp import FastMCP

DATASET_PARQUET = "hf://datasets/MTerryJack/prophet-dreams/data/train.parquet"
DEFAULT_SOURCE = "al-Mara'i al-Hisan (Ibn Abi Jamrah)"  # dreams_read without source, kept for compatibility
MAX_LIMIT = 5
PAGE_CHARS = 1000
SNIPPET_CHARS = 160
CALL_TIMEOUT_S = 8.0
LOAD_TIMEOUT_S = 60.0
NOTE = "[Classical Arabic (al-Mara'i text is OCR): translate or explain it for the listener.]"
# Arabic normalisation (same in SQL and Python): drop tashkeel/tatweel, unify alif, ya, ta marbuta, hamza seats.
_DIAC = "[\u064B-\u0652\u0670\u0640]"
_FROM, _TO = "أإآٱىةؤئ", "اااايهوي"
NORM_SQL = "translate(regexp_replace({c}, '" + _DIAC + "', '', 'g'), '" + _FROM + "', '" + _TO + "')"

mcp = FastMCP("ProphetDreams")

_lock = threading.Lock()
_con: duckdb.DuckDBPyConnection | None = None
_ready = threading.Event()
_load_error: BaseException | None = None
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dreams")


def _load() -> None:
    # ~0.2MB parquet, ~120 rows: one bounded in-memory table with a normalised search column.
    global _con, _load_error
    try:
        con = duckdb.connect(config={"extension_directory": "/opt/duckdb/extensions"})
        con.execute("LOAD httpfs")
        con.execute(
            "CREATE TABLE dreams AS SELECT source, book_ar, dream_number, covers, topic, ref_ar, text_ar, "
            + NORM_SQL.format(c="text_ar") + f" AS nt FROM read_parquet('{DATASET_PARQUET}')"
        )
        n = con.execute("SELECT count(*) FROM dreams").fetchone()[0]
        if n < 1:
            raise RuntimeError(f"Loaded 0 rows from {DATASET_PARQUET}")
        if not con.execute("SELECT count(*) FROM dreams WHERE source = ?", [DEFAULT_SOURCE]).fetchone()[0]:
            raise RuntimeError(f"Default source {DEFAULT_SOURCE!r} missing from {DATASET_PARQUET}")
        with _lock:
            _con = con
    except BaseException as e:
        _load_error = e
    finally:
        _ready.set()


threading.Thread(target=_load, name="dreams-load", daemon=True).start()


def _conn() -> duckdb.DuckDBPyConnection:
    if not _ready.wait(timeout=LOAD_TIMEOUT_S):
        raise RuntimeError(f"Dreams dataset load still running after {LOAD_TIMEOUT_S:.0f}s")
    if _load_error is not None:
        raise RuntimeError(f"Dreams dataset load failed: {_load_error}") from _load_error
    with _lock:
        if _con is None:
            raise RuntimeError("Dreams dataset connection missing after load")
        return _con


def _run(fn, *args):
    fut = _pool.submit(fn, *args)
    try:
        return fut.result(timeout=CALL_TIMEOUT_S)
    except FuturesTimeout:
        fut.cancel()
        raise TimeoutError(f"dreamst tool exceeded {CALL_TIMEOUT_S:.0f}s") from None


def _q(sql: str, params: list) -> list:
    con = _conn()
    with _lock:
        return con.execute(sql, params).fetchall()


def _norm(s: str) -> str:
    s = re.sub(_DIAC, "", s)
    return s.translate(str.maketrans(_FROM, _TO))


def _speak(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _cut(t: str, n: int) -> str:
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + "…"


def _terms(q: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"[\u0621-\u064Aa-z0-9]+", _norm(q.lower()))))


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


def _src_filter(source: str | None) -> tuple[str, list]:
    s = (source or "").strip()
    if not s:
        return "", []
    known = [r[0] for r in _q("SELECT DISTINCT source FROM dreams ORDER BY 1", [])]
    hit = [k for k in known if s.lower() in k.lower()]
    if not hit:
        raise LookupError(f"Unknown source {s!r}. Sources: {'; '.join(known)}.")
    if len(hit) > 1:
        raise LookupError(f"Source {s!r} is ambiguous: {'; '.join(hit)}.")
    return " AND source = ?", [hit[0]]


def _ref(src: str, n: int, covers: str, ref: str | None = None) -> str:
    return f"({src.split(' (')[0]}, dream {covers}{', ' + ref if ref and src != DEFAULT_SOURCE else ''})"


def _arg(src: str) -> str:
    return "" if src == DEFAULT_SOURCE else f', source="{src.split(" (")[0]}"'


def _read_impl(dream_number: int, part: int, source: str | None) -> str:
    n, p = int(dream_number), int(part)
    if p < 1:
        raise ValueError("part must be 1 or more.")
    f, fp = _src_filter(source)
    src = fp[0] if fp else DEFAULT_SOURCE
    lo, hi = _q("SELECT min(dream_number), max(dream_number) FROM dreams WHERE source = ?", [src])[0]
    last = int(_q("SELECT covers FROM dreams WHERE source = ? ORDER BY dream_number DESC LIMIT 1", [src])[0][0].split("-")[-1])
    if not lo <= n <= last:
        raise ValueError(f"dream_number must be {lo} to {last} for {src}.")
    dn, cov, topic, ref, txt = _q("SELECT dream_number, covers, topic, ref_ar, text_ar FROM dreams WHERE source = ? AND dream_number <= ? ORDER BY dream_number DESC LIMIT 1", [src, n])[0]
    txt = _speak(txt)
    pages = max(1, math.ceil(len(txt) / PAGE_CHARS))
    if p > pages:
        raise ValueError(f"Dream {cov} has {pages} parts.")
    merged = f" Dream {n} is inside dream {cov} (OCR lost the heading)." if cov != str(n) else ""
    head = f"Topic: {topic}. " if topic and p == 1 else ""
    nxt = f"\nNext: dreams_read(dream_number={dn}, part={p + 1}{_arg(src)})" if p < pages else (f"\nNext: dreams_read(dream_number={int(cov.split('-')[-1]) + 1}{_arg(src)})" if int(cov.split('-')[-1]) < last else "\nEnd of the collection.")
    return f"{NOTE}{merged}\n{head}Part {p}/{pages}: {txt[(p - 1) * PAGE_CHARS: p * PAGE_CHARS]} {_ref(src, dn, cov, ref)}{nxt}"


def _search_impl(query: str, limit: int, source: str | None) -> str:
    q = query.strip()
    if not q:
        raise ValueError("query must be non-empty.")
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be 1 to {MAX_LIMIT}.")
    terms = _terms(q)
    if not terms:
        raise ValueError("query must contain Arabic letters or numbers.")
    f, fp = _src_filter(source)
    pats = [re.escape(t) for t in terms]
    tf_cols = ", ".join(f"len(regexp_extract_all(nt, ?)) AS tf{i}" for i in range(len(terms)))
    where = " AND ".join(f"tf{i} > 0" for i in range(len(terms)))
    df_cols = ", ".join(f"count(*) FILTER (WHERE tf{i} > 0)" for i in range(len(terms)))
    base = f"WITH d AS (SELECT source, dream_number, covers, nt, len(string_split(nt, ' ')) AS dl, {tf_cols} FROM dreams WHERE true{f})"
    stats = _q(f"{base} SELECT count(*), avg(dl), {df_cols} FROM d", pats + fp)[0]
    n, avgdl, dfs = stats[0], stats[1], stats[2:]
    if not n:
        return f"No dreams mention {q!r}."
    idf = [math.log(1 + (n - df + 0.5) / (df + 0.5)) for df in dfs]
    score = " + ".join(f"{idf[i]!r} * tf{i} * 2.2 / (tf{i} + 1.2 * (0.25 + 0.75 * dl / {avgdl!r}))" for i in range(len(terms)))
    phrase = " ".join(terms)
    rows = _q(
        f"{base} SELECT source, dream_number, covers, nt FROM d WHERE {where} ORDER BY ({score}) + CASE WHEN contains(nt, ?) THEN 1.0 ELSE 0 END DESC, dream_number LIMIT ?",
        pats + fp + [phrase, limit],
    )
    if not rows:
        return f"No dreams mention {q!r}."
    out = [f"{_snippet(t, phrase if phrase in t else terms[0])} {_ref(s, d, c)} -> dreams_read(dream_number={d}{_arg(s)})" for s, d, c, t in rows]
    return NOTE + "\n" + "\n".join(out)


def _random_impl(source: str | None) -> str:
    f, fp = _src_filter(source)
    src, dn = _q(f"SELECT source, dream_number FROM (SELECT * FROM dreams WHERE length(text_ar) > 300{f}) ORDER BY random() LIMIT 1", fp)[0]
    return _read_impl(dn, 1, src)


@mcp.tool()
def dreams_read(dream_number: int, part: int = 1, source: str | None = None) -> str:
    """Use when the user asks to hear a dream in which someone saw the Prophet (saas), by number. dream_number is per source; without source it reads al-Mara'i al-Hisan (Ibn Abi Jamrah, 1-70). Other sources: Sa'adat al-Darayn (Nabhani), al-Mustaghithin (Ibn Bashkuwal); pass part of the name. Text is classical Arabic: translate or explain it when speaking. Returns one part and a Next: hint."""
    return _run(_read_impl, dream_number, part, source)


@mcp.tool()
def dreams_search(query: str, limit: int = 3, source: str | None = None) -> str:
    """Use when the user asks about dreams of the Prophet (saas) seen by others on a topic. query in Arabic keywords (e.g. الشرح, جبريل); limit 1-5; optional source book filter. Text is classical Arabic: translate or explain it. Returns snippets with dream numbers."""
    return _run(_search_impl, query, limit, source)


@mcp.tool()
def dreams_random(source: str | None = None) -> str:
    """Use when the user asks for any dream of the Prophet (saas), or a vision someone had of him. Optional source book filter. Text is classical Arabic: translate or explain it. Returns one dream with its reference."""
    return _run(_random_impl, source)


if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
