"""Hadith commentary MCP: Ibn Abi Jamrah's Bahjat al-Nufus (Arabic) via DuckDB over hf://datasets/MTerryJack/bahjat-al-nufus."""
from __future__ import annotations

import math
import re
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

import duckdb
from fastmcp import FastMCP

DATASET_PARQUET = "hf://datasets/MTerryJack/bahjat-al-nufus/data/train.parquet"
MAX_LIMIT = 5
HADITH_CHARS = 500
PAGE_CHARS = 1000
SNIPPET_CHARS = 160
CALL_TIMEOUT_S = 8.0
LOAD_TIMEOUT_S = 60.0
NOTE = "[Classical Arabic, OCR text: translate or explain it for the listener.]"
# Arabic normalisation (same in SQL and Python): drop tashkeel/tatweel, unify alif, ya, ta marbuta, hamza seats.
_DIAC = "[\u064B-\u0652\u0670\u0640]"
_FROM, _TO = "أإآٱىةؤئ", "اااايهوي"
NORM_SQL = "translate(regexp_replace({c}, '" + _DIAC + "', '', 'g'), '" + _FROM + "', '" + _TO + "')"

mcp = FastMCP("HadithCommentary")

_lock = threading.Lock()
_con: duckdb.DuckDBPyConnection | None = None
_ready = threading.Event()
_load_error: BaseException | None = None
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="bahja")


def _load() -> None:
    # ~2MB parquet, 268 rows: one bounded in-memory table with a normalised search column.
    global _con, _load_error
    try:
        con = duckdb.connect(config={"extension_directory": "/opt/duckdb/extensions"})
        con.execute("LOAD httpfs")
        con.execute(
            "CREATE TABLE bahja AS SELECT hadith_number, covers, bukhari_number, hadith_ar, commentary_ar, volume, alignment_confidence, "
            + NORM_SQL.format(c="hadith_ar || ' ' || commentary_ar") + f" AS nt FROM read_parquet('{DATASET_PARQUET}')"
        )
        n = con.execute("SELECT count(*) FROM bahja").fetchone()[0]
        if n < 1:
            raise RuntimeError(f"Loaded 0 rows from {DATASET_PARQUET}")
        with _lock:
            _con = con
    except BaseException as e:
        _load_error = e
    finally:
        _ready.set()


threading.Thread(target=_load, name="bahja-load", daemon=True).start()


def _conn() -> duckdb.DuckDBPyConnection:
    if not _ready.wait(timeout=LOAD_TIMEOUT_S):
        raise RuntimeError(f"Bahjat al-Nufus load still running after {LOAD_TIMEOUT_S:.0f}s")
    if _load_error is not None:
        raise RuntimeError(f"Bahjat al-Nufus load failed: {_load_error}") from _load_error
    with _lock:
        if _con is None:
            raise RuntimeError("Bahjat al-Nufus connection missing after load")
        return _con


def _run(fn, *args):
    fut = _pool.submit(fn, *args)
    try:
        return fut.result(timeout=CALL_TIMEOUT_S)
    except FuturesTimeout:
        fut.cancel()
        raise TimeoutError(f"bahjat tool exceeded {CALL_TIMEOUT_S:.0f}s") from None


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


def _ref(n: int, covers: str, vol: int, conf: str) -> str:
    h = f"hadith {covers}" if covers != str(n) else f"hadith {n}"
    c = "" if conf == "high" else f", alignment {conf}"
    return f"(Bahjat al-Nufus, {h}, vol {vol}{c})"


def _read_impl(hadith_number: int, part: int) -> str:
    n, p = int(hadith_number), int(part)
    lo, hi = _q("SELECT min(hadith_number), max(hadith_number) FROM bahja", [])[0]
    if not lo <= n <= hi:
        raise ValueError(f"hadith_number must be {lo} to {hi}.")
    if p < 1:
        raise ValueError("part must be 1 or more.")
    rows = _q("SELECT hadith_number, covers, bukhari_number, hadith_ar, commentary_ar, volume, alignment_confidence FROM bahja WHERE hadith_number <= ? ORDER BY hadith_number DESC LIMIT 1", [n])
    hn, cov, bk, had, com, vol, conf = rows[0]
    a, b = (int(x) for x in (cov.split("-") + [cov])[:2])
    if not a <= n <= b:
        raise LookupError(f"Hadith {n} is not in the dataset.")
    com = _speak(com)
    pages = max(1, math.ceil(len(com) / PAGE_CHARS))
    if p > pages:
        raise ValueError(f"Hadith {hn} commentary has {pages} parts.")
    chunk = com[(p - 1) * PAGE_CHARS: p * PAGE_CHARS]
    head = f"Hadith (Bukhari {bk}): {_cut(_speak(had), HADITH_CHARS)}\n" if p == 1 else ""
    nxt = f"\nNext: bahjat_read(hadith_number={hn}, part={p + 1})" if p < pages else f"\nEnd of commentary on hadith {cov}."
    return f"{NOTE}\n{head}Commentary part {p}/{pages}: {chunk} {_ref(hn, cov, vol, conf)}{nxt}"


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


def _search_impl(query: str, limit: int) -> str:
    # BM25-style ranking (k1=1.2, b=0.75) over normalised Arabic; all terms must appear (substring, since OCR splits words).
    q = query.strip()
    if not q:
        raise ValueError("query must be non-empty.")
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be 1 to {MAX_LIMIT}.")
    terms = _terms(q)
    if not terms:
        raise ValueError("query must contain Arabic letters or numbers.")
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
        pats + [phrase, limit],
    )
    if not rows:
        return f"No hadith or commentary mentions {q!r}."
    out = [f"{_snippet(t, phrase if phrase in t else terms[0])} {_ref(h, c, v, cf)}" for h, c, v, cf, t in rows]
    return NOTE + "\n" + "\n".join(out) + "\nRead one with bahjat_read(hadith_number=N)."


def _random_impl() -> str:
    hn = _q("SELECT hadith_number FROM bahja WHERE alignment_confidence = 'high' USING SAMPLE 1", [])[0][0]
    return _read_impl(hn, 1)


@mcp.tool()
def bahjat_read(hadith_number: int, part: int = 1) -> str:
    """Use when the user asks for Ibn Abi Jamrah's commentary (Bahjat al-Nufus) on a hadith of his Bukhari abridgement, by hadith number 1-287. Text is classical Arabic (OCR): translate or explain it when speaking. Returns the hadith, one part of commentary and a Next: hint."""
    return _run(_read_impl, hadith_number, part)


@mcp.tool()
def bahjat_search(query: str, limit: int = 3) -> str:
    """Use when the user asks what Ibn Abi Jamrah / Bahjat al-Nufus says about a topic. query in Arabic keywords (e.g. الرؤيا, الصلاة); limit 1-5. Text is classical Arabic: translate or explain it. Returns snippets with hadith numbers; bahjat_read reads on."""
    return _run(_search_impl, query, limit)


@mcp.tool()
def bahjat_random() -> str:
    """Use when the user asks for any hadith with commentary, or a random lesson from Bahjat al-Nufus. Text is classical Arabic: translate or explain it. Returns one hadith and the start of its commentary."""
    return _run(_random_impl)


if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
