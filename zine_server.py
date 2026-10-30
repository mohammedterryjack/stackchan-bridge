"""Hacker-zine search MCP: DuckDB over hf://datasets/MTerryJack/hacker-zines."""
from __future__ import annotations

import re
from typing import Literal
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

import duckdb
from fastmcp import FastMCP

DATASET_PARQUET = "hf://datasets/MTerryJack/hacker-zines/data/train.parquet"
ALLOWED_ZINES = frozenset({"phrack", "pocorgtfo", "tmpout"})
MAX_LIMIT = 5
SNIPPET_CHARS = 80
READ_CHARS = 3000
CALL_TIMEOUT_S = 8.0
LOAD_TIMEOUT_S = 60.0

mcp = FastMCP("Zines")

_lock = threading.Lock()
_con: duckdb.DuckDBPyConnection | None = None
_ready = threading.Event()
_load_error: BaseException | None = None
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="zine")


def _load() -> None:
    global _con, _load_error
    try:
        con = duckdb.connect(config={"extension_directory": "/opt/duckdb/extensions"})
        con.execute("LOAD httpfs")
        con.execute(
            f"CREATE TABLE zines AS SELECT * FROM read_parquet('{DATASET_PARQUET}')"
        )
        n = con.execute("SELECT count(*) FROM zines").fetchone()[0]
        if n < 1:
            raise RuntimeError(f"Loaded 0 rows from {DATASET_PARQUET}")
        with _lock:
            _con = con
    except BaseException as e:
        _load_error = e
    finally:
        _ready.set()


threading.Thread(target=_load, name="zine-load", daemon=True).start()


def _conn() -> duckdb.DuckDBPyConnection:
    if not _ready.wait(timeout=LOAD_TIMEOUT_S):
        raise RuntimeError(
            f"DuckDB hf:// load still running after {LOAD_TIMEOUT_S:.0f}s"
        )
    if _load_error is not None:
        raise RuntimeError(f"DuckDB hf:// load failed: {_load_error}") from _load_error
    with _lock:
        if _con is None:
            raise RuntimeError("DuckDB connection missing after successful load")
        return _con


def _run(fn, *args):
    fut = _pool.submit(fn, *args)
    try:
        return fut.result(timeout=CALL_TIMEOUT_S)
    except FuturesTimeout:
        fut.cancel()
        raise TimeoutError(
            f"zine tool exceeded {CALL_TIMEOUT_S:.0f}s hard timeout"
        ) from None


def _snippet(text: str, query: str) -> str:
    lower = text.lower()
    q = query.lower()
    pos = lower.find(q)
    if pos < 0:
        tok = q.split()[0] if q.split() else q
        pos = lower.find(tok)
    if pos < 0:
        start = 0
    else:
        start = max(0, pos - 40)
    raw = text[start : start + SNIPPET_CHARS * 3]
    raw = re.sub(r"[^A-Za-z0-9 .,;:'!?()-]+", " ", raw)
    snip = re.sub(r"\s+", " ", raw).strip()[:SNIPPET_CHARS]
    snip = snip.rsplit(" ", 1)[0] if len(snip) == SNIPPET_CHARS else snip
    if start > 0:
        snip = "…" + snip
    if start + SNIPPET_CHARS < len(text):
        snip = snip + "…"
    return snip


def _search_impl(query: str, zine: str | None, limit: int) -> str:
    con = _conn()
    q = query.strip()
    if not q:
        raise ValueError("query must be non-empty")
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    z = (zine or "").strip().lower() or None
    if z is not None and z not in ALLOWED_ZINES:
        raise ValueError(f"zine must be one of: {', '.join(sorted(ALLOWED_ZINES))}")

    # Escape single quotes for SQL string literal (fail loudly on weirdness)
    if "\x00" in q:
        raise ValueError("query contains NUL")
    needle = q.lower()

    sql = (
        "SELECT id, zine, issue, article, title, author, text "
        "FROM zines WHERE contains(lower(text), ?) "
    )
    params: list = [needle]
    if z is not None:
        sql += "AND zine = ? "
        params.append(z)
    sql += "LIMIT ?"
    params.append(limit)

    with _lock:
        rows = con.execute(sql, params).fetchall()

    if not rows:
        scope = f" in {z}" if z else ""
        return f"No hits for {q!r}{scope}."

    lines = []
    for rid, zin, issue, article, title, author, text in rows:
        lines.append(f"{title} ({rid}) – {_snippet(text, q)}")
    out = "\n".join(lines)
    if len(out.encode("utf-8")) > 4000:
        raise RuntimeError(
            f"result {len(out.encode('utf-8'))} bytes exceeds 4KB budget"
        )
    return out


def _read_impl(chunk_id: str) -> str:
    con = _conn()
    cid = chunk_id.strip()
    if not cid:
        raise ValueError("id must be non-empty")
    with _lock:
        row = con.execute(
            "SELECT id, zine, issue, article, title, author, chunk_idx, text, source_url "
            "FROM zines WHERE id = ?",
            [cid],
        ).fetchone()
    if row is None:
        raise LookupError(f"unknown id: {cid}")
    rid, zin, issue, article, title, author, chunk_idx, text, url = row
    body = text if len(text) <= READ_CHARS else text[:READ_CHARS] + "…"
    auth = f"\nAuthor: {author}" if author else ""
    out = (
        f"id: {rid}\n"
        f"zine: {zin} issue={issue} article={article} chunk={chunk_idx}\n"
        f"title: {title}{auth}\n"
        f"source: {url}\n\n"
        f"{body}"
    )
    if len(out.encode("utf-8")) > 4500:
        # trim body further to keep under ~4.5KB wire size
        overhead = len(out) - len(body)
        keep = max(500, 4000 - overhead)
        body = text[:keep] + "…"
        out = (
            f"id: {rid}\n"
            f"zine: {zin} issue={issue} article={article} chunk={chunk_idx}\n"
            f"title: {title}{auth}\n"
            f"source: {url}\n\n"
            f"{body}"
        )
    return out


@mcp.tool()
def zine_search(
    query: str,
    zine: str | None = None,
    limit: int = 5,
) -> str:
    """Search hacker zines (Phrack, PoC||GTFO, tmp.0ut). zine optional: phrack|pocorgtfo|tmpout. limit 1-5. Lines: title (id) – snippet; use zine_read with the id."""
    if zine:
        k = re.sub(r"[^a-z0-9]", "", zine.lower())
        zine = {"phrack": "phrack", "pocorgtfo": "pocorgtfo", "pocgtfo": "pocorgtfo", "tmpout": "tmpout", "tmp0ut": "tmpout"}.get(k, zine)
    return _run(_search_impl, query, zine, limit)


@mcp.tool()
def zine_read(id: str) -> str:
    """Read one zine section by id from zine_search, e.g. phrack-69-07-003."""
    return _run(_read_impl, id)


if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
