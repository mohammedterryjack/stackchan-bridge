"""Aesop's Fables MCP: DuckDB over hf://datasets/MTerryJack/aesop-fables (Townsend translation, 313 fables)."""
from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

import duckdb
from fastmcp import FastMCP

DATASET_PARQUET = "hf://datasets/MTerryJack/aesop-fables/data/train.parquet"
MAX_LIMIT = 5
SNIPPET_CHARS = 160
CALL_TIMEOUT_S = 8.0
LOAD_TIMEOUT_S = 60.0

mcp = FastMCP("Aesop")

_lock = threading.Lock()
_con: duckdb.DuckDBPyConnection | None = None
_ready = threading.Event()
_load_error: BaseException | None = None
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="aesop")


def _load() -> None:
    # Whole dataset is ~100KB parquet (313 fables): one bounded in-memory table.
    global _con, _load_error
    try:
        con = duckdb.connect(config={"extension_directory": "/opt/duckdb/extensions"})
        con.execute("LOAD httpfs")
        con.execute(f"CREATE TABLE aesop AS SELECT id, title, text, moral FROM read_parquet('{DATASET_PARQUET}')")
        n = con.execute("SELECT count(*) FROM aesop").fetchone()[0]
        if n < 1:
            raise RuntimeError(f"Loaded 0 rows from {DATASET_PARQUET}")
        with _lock:
            _con = con
    except BaseException as e:
        _load_error = e
    finally:
        _ready.set()


threading.Thread(target=_load, name="aesop-load", daemon=True).start()


def _conn() -> duckdb.DuckDBPyConnection:
    if not _ready.wait(timeout=LOAD_TIMEOUT_S):
        raise RuntimeError(f"Aesop dataset load still running after {LOAD_TIMEOUT_S:.0f}s")
    if _load_error is not None:
        raise RuntimeError(f"Aesop dataset load failed: {_load_error}") from _load_error
    with _lock:
        if _con is None:
            raise RuntimeError("Aesop connection missing after load")
        return _con


def _run(fn, *args):
    fut = _pool.submit(fn, *args)
    try:
        return fut.result(timeout=CALL_TIMEOUT_S)
    except FuturesTimeout:
        fut.cancel()
        raise TimeoutError(f"aesop tool exceeded {CALL_TIMEOUT_S:.0f}s") from None


def _q(sql: str, params: list) -> list:
    con = _conn()
    with _lock:
        return con.execute(sql, params).fetchall()


def _speak(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _snippet(text: str, q: str) -> str:
    t = _speak(text)
    pos = t.lower().find(q.lower())
    start = max(0, pos - 50) if pos >= 0 else 0
    snip = t[start:start + SNIPPET_CHARS]
    if start > 0:
        snip = "…" + snip.split(" ", 1)[-1]
    if start + SNIPPET_CHARS < len(t):
        snip = snip.rsplit(" ", 1)[0] + "…"
    return snip


def _terms(q: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"[a-z0-9]+", q.lower())))


def _search_impl(query: str, limit: int) -> str:
    # BM25-style ranking (k1=1.2, b=0.75) over whole-word matches; all terms must appear.
    q = query.strip()
    if not q:
        raise ValueError("query must be non-empty.")
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be 1 to {MAX_LIMIT}.")
    terms = _terms(q)
    if not terms:
        raise ValueError("query must contain letters or numbers.")
    pats = [r"\b" + re.escape(t) + r"\b" for t in terms]
    tf_cols = ", ".join(f"len(regexp_extract_all(lt, ?)) AS tf{i}" for i in range(len(terms)))
    where = " AND ".join(f"tf{i} > 0" for i in range(len(terms)))
    df_cols = ", ".join(f"count(*) FILTER (WHERE tf{i} > 0)" for i in range(len(terms)))
    base = f"WITH d AS (SELECT id, title, txt, lt, len(string_split(lt, ' ')) AS dl, {tf_cols} FROM (SELECT id, title, title || '. ' || text || ' ' || moral AS txt, lower(title || '. ' || text || ' ' || moral) AS lt FROM aesop))"
    stats = _q(f"{base} SELECT count(*), avg(dl), {df_cols} FROM d", pats)[0]
    n, avgdl, dfs = stats[0], stats[1], stats[2:]
    idf = [__import__("math").log(1 + (n - df + 0.5) / (df + 0.5)) for df in dfs]
    score = " + ".join(f"{idf[i]!r} * tf{i} * 2.2 / (tf{i} + 1.2 * (0.25 + 0.75 * dl / {avgdl!r}))" for i in range(len(terms)))
    rows = _q(
        f"{base} SELECT id, title, txt FROM d WHERE {where} ORDER BY ({score}) + CASE WHEN contains(lt, ?) THEN 1.0 ELSE 0 END DESC, id LIMIT ?",
        pats + [q.lower(), limit],
    )
    if not rows:
        return f"No fables mention {q!r}."
    def snip(t: str) -> str:
        return _snippet(t, q) if q.lower() in t.lower() else _snippet(t, terms[0])
    return "\n".join(f"{ti}: {snip(t[len(ti) + 2:])} (fable {i})" for i, ti, t in rows)


def _fmt(row) -> str:
    i, title, text, moral = row
    m = f" Moral: {_speak(moral)}" if moral else ""
    return f"{title}. {_speak(text)}{m} (fable {i})"


def _read_impl(fable_id: int | None, title: str | None) -> str:
    t = (title or "").strip()
    if (fable_id is None) == (not t):
        raise ValueError("Give exactly one of fable_id or title.")
    if fable_id is not None:
        n = _q("SELECT count(*) FROM aesop", [])[0][0]
        rows = _q("SELECT id, title, text, moral FROM aesop WHERE id = ?", [int(fable_id)])
        if not rows:
            raise LookupError(f"fable_id must be 1 to {n}.")
        return _fmt(rows[0])
    rows = _q("SELECT id, title, text, moral FROM aesop WHERE contains(lower(title), ?) ORDER BY lower(title) <> ?, length(title), id LIMIT 4", [t.lower(), t.lower()])
    if not rows:
        raise LookupError(f"No fable title contains {t!r}. Try aesop_search.")
    others = "; ".join(f"{r[1]} (fable {r[0]})" for r in rows[1:])
    return _fmt(rows[0]) + (f"\nOther matches: {others}" if others else "")


def _random_impl() -> str:
    return _fmt(_q("SELECT id, title, text, moral FROM aesop USING SAMPLE 1", [])[0])


@mcp.tool()
def aesop_read(fable_id: int | None = None, title: str | None = None) -> str:
    """Use when the user asks for a specific Aesop fable, e.g. 'tell me the tortoise and the hare'. fable_id from '(fable N)', or title: part of the title. Returns the whole fable and its moral."""
    return _run(_read_impl, fable_id, title)


@mcp.tool()
def aesop_search(query: str, limit: int = 3) -> str:
    """Use when the user wants an Aesop fable about an animal, theme or lesson, e.g. 'a fable about greed'. limit 1-5. Returns titles with snippets and (fable N) ids for aesop_read."""
    return _run(_search_impl, query, limit)


@mcp.tool()
def aesop_random() -> str:
    """Use when the user asks for any fable, a bedtime story or a story with a moral. Returns one random Aesop fable and its moral."""
    return _run(_random_impl)


if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
