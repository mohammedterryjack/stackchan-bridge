"""Rumi Mathnawi MCP: DuckDB over hf://datasets/MTerryJack/rumi-mathnawi (Nicholson English, Books I-VI)."""
from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

import duckdb
from fastmcp import FastMCP

DATASET_PARQUET = "hf://datasets/MTerryJack/rumi-mathnawi/data/train.parquet"
BOOKS = (1, 2, 3, 4, 5, 6)
MAX_LIMIT = 5
PAGE_CHARS = 1400
SNIPPET_CHARS = 160
CALL_TIMEOUT_S = 8.0
LOAD_TIMEOUT_S = 60.0

mcp = FastMCP("Rumi")

_lock = threading.Lock()
_con: duckdb.DuckDBPyConnection | None = None
_ready = threading.Event()
_load_error: BaseException | None = None
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="rumi")


def _load() -> None:
    # Whole dataset is ~2MB parquet (~5.7k passages): one bounded in-memory table.
    global _con, _load_error
    try:
        con = duckdb.connect(config={"extension_directory": "/opt/duckdb/extensions"})
        con.execute("LOAD httpfs")
        con.execute(f"CREATE TABLE rumi AS SELECT book, section, verse_start, verse_end, text_en FROM read_parquet('{DATASET_PARQUET}')")
        n = con.execute("SELECT count(*) FROM rumi").fetchone()[0]
        if n < 1:
            raise RuntimeError(f"Loaded 0 rows from {DATASET_PARQUET}")
        with _lock:
            _con = con
    except BaseException as e:
        _load_error = e
    finally:
        _ready.set()


threading.Thread(target=_load, name="rumi-load", daemon=True).start()


def _conn() -> duckdb.DuckDBPyConnection:
    if not _ready.wait(timeout=LOAD_TIMEOUT_S):
        raise RuntimeError(f"Rumi dataset load still running after {LOAD_TIMEOUT_S:.0f}s")
    if _load_error is not None:
        raise RuntimeError(f"Rumi dataset load failed: {_load_error}") from _load_error
    with _lock:
        if _con is None:
            raise RuntimeError("Rumi connection missing after load")
        return _con


def _run(fn, *args):
    fut = _pool.submit(fn, *args)
    try:
        return fut.result(timeout=CALL_TIMEOUT_S)
    except FuturesTimeout:
        fut.cancel()
        raise TimeoutError(f"rumi tool exceeded {CALL_TIMEOUT_S:.0f}s") from None


def _q(sql: str, params: list) -> list:
    con = _conn()
    with _lock:
        return con.execute(sql, params).fetchall()


def _ref(book: int, a: int, b: int) -> str:
    return f"(Mathnawi {book}:{a})" if a == b else f"(Mathnawi {book}:{a}-{b})"


def _speak(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _page(book: int, start_verse: int) -> str:
    rows = _q(
        "SELECT section, verse_start, verse_end, text_en FROM rumi WHERE book = ? AND verse_end >= ? ORDER BY verse_start LIMIT 8",
        [book, start_verse],
    )
    if not rows:
        last = _q("SELECT max(verse_end) FROM rumi WHERE book = ?", [book])[0][0]
        raise LookupError(f"Book {book} has verses 1 to {last}.")
    section = rows[0][0]
    parts, used, a, b = [], 0, rows[0][1], rows[0][2]
    for sec, vs, ve, txt in rows:
        t = _speak(txt)
        if parts and (used + len(t) > PAGE_CHARS or sec != section):
            break
        parts.append(t)
        used += len(t)
        b = ve
    head = f"{section}. " if section else ""
    nxt = _q("SELECT min(verse_start) FROM rumi WHERE book = ? AND verse_start > ?", [book, b])[0][0]
    tail = f"\nNext: rumi_read(book={book}, verse={nxt})" if nxt is not None else f"\nEnd of Book {book}."
    return f"{head}{' '.join(parts)} {_ref(book, a, b)}{tail}"


def _read_impl(book: int, verse: int | None, section: str | None) -> str:
    if book not in BOOKS:
        raise ValueError("book must be 1 to 6.")
    s = (section or "").strip()
    if verse is not None and s:
        raise ValueError("Give verse or section, not both.")
    if s:
        hits = _q("SELECT min(verse_start) FROM rumi WHERE book = ? AND contains(lower(section), ?)", [book, s.lower()])
        if hits[0][0] is None:
            raise LookupError(f"No section in Book {book} matching {s!r}.")
        return _page(book, hits[0][0])
    v = 1 if verse is None else int(verse)
    if v < 1:
        raise ValueError("verse must be 1 or more.")
    return _page(book, v)


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


def _search_impl(query: str, limit: int) -> str:
    q = query.strip()
    if not q:
        raise ValueError("query must be non-empty.")
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be 1 to {MAX_LIMIT}.")
    rows = _q(
        "SELECT book, verse_start, verse_end, text_en FROM rumi WHERE contains(lower(text_en), ?) ORDER BY book, verse_start LIMIT ?",
        [q.lower(), limit],
    )
    if not rows:
        return f"No passages mention {q!r}."
    return "\n".join(f"{_snippet(t, q)} {_ref(b, a, e)}" for b, a, e, t in rows)


def _random_impl() -> str:
    b, a, e, sec, t = _q("SELECT book, verse_start, verse_end, section, text_en FROM rumi WHERE length(text_en) > 200 USING SAMPLE 1", [])[0]
    head = f"{sec}. " if sec else ""
    return f"{head}{_speak(t)} {_ref(b, a, e)}\nNext: rumi_read(book={b}, verse={e + 1})"


@mcp.tool()
def rumi_read(book: int, verse: int | None = None, section: str | None = None) -> str:
    """Read Rumi's Mathnawi (Nicholson's English, Books 1-6) from a verse number or a section-title phrase. Returns about a page and a Next: hint to continue."""
    return _run(_read_impl, book, verse, section)


@mcp.tool()
def rumi_search(query: str, limit: int = 3) -> str:
    """Search the Mathnawi (English, Books 1-6) for a word or phrase. limit 1-5. Each line ends with its reference; use rumi_read to read on."""
    return _run(_search_impl, query, limit)


@mcp.tool()
def rumi_random() -> str:
    """A random passage from Rumi's Mathnawi (English, Books 1-6) with its reference."""
    return _run(_random_impl)


if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
