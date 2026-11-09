"""Shared verse-corpus MCP builder for gospel_server.py and ethiopian_server.py: one row per verse from a Hugging Face
parquet (DuckDB in memory). Tools: {pfx}_read (no chapter = random verse), {pfx}_search (no query = list books). Each call logs one stderr line via
mcp_tool_log; a failed dataset load exits at startup (no fallback)."""

from __future__ import annotations

import contextvars
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

import duckdb
from fastmcp import FastMCP

import bookmarks

from mcp_tool_log import get_logger, hf_revision, logged, note

CALL_TIMEOUT_S = 8.0
MAX_VERSES = 8
MAX_CHARS = 1000
MAX_LIMIT = 5


def _k(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


LANGS = {"gez": "Ge'ez (Classical Ethiopic)", "grc": "Greek", "ar": "Arabic", "fr": "French"}


def _lang_note(lang: str) -> str:
    return "" if lang in (None, "en") else f" [Source text in {LANGS.get(lang, lang)}, no English in this dataset: translate or explain it for the user.]"


class Corpus:
    def __init__(self, name: str, repo: str, pfx: str, books: list[str], aliases: dict[str, str]):
        self.repo, self.pfx, self.books, self.aliases = repo, pfx, books, aliases
        self.log = get_logger(name)
        self.lock = threading.Lock()
        self.con: duckdb.DuckDBPyConnection | None = None
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix=pfx)
        self.names: dict[str, str] = {}
        self.order: list[tuple[str, str, int, int]] = []

    def load(self) -> None:
        t0 = time.monotonic()
        rev = hf_revision(self.repo)
        url = f"hf://datasets/{self.repo}@{rev}/data/train.parquet"
        con = duckdb.connect(config={"extension_directory": "/opt/duckdb/extensions"})
        con.execute("LOAD httpfs")
        cols = [d[0] for d in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{url}')").fetchall()]
        lang = "language" if "language" in cols else "'en'"
        con.execute(f"CREATE TABLE t AS SELECT id, book_key, book, chapter, verse, text, source, {lang} AS lang, lower(text) AS lt FROM read_parquet('{url}')")
        rows = con.execute("SELECT book_key, any_value(book), min(id), count(*), count(DISTINCT chapter) FROM t GROUP BY 1 ORDER BY 3").fetchall()
        got = [r[0] for r in rows]
        missing = set(self.books) - set(got)
        if missing:
            raise RuntimeError(f"Books missing from {url}: {sorted(missing)}")
        self.order = [(r[0], r[1], r[3], r[4]) for r in rows]
        for bk, b, _, _ in self.order:
            self.names[_k(bk)] = bk
            self.names[_k(b)] = bk
            self.names[_k(b.split(" (")[0])] = bk
        for a, bk in self.aliases.items():
            self.names[_k(a)] = bk
        with self.lock:
            self.con = con
        self.log.info(f"loaded {self.repo}@{rev[:12]} rows={sum(r[3] for r in rows)} books={len(rows)} " + " ".join(f"[{bk}]={n}" for bk, _, n, _ in self.order) + f" ms={(time.monotonic() - t0) * 1000:.0f}")

    def q(self, sql: str, params: list) -> list:
        with self.lock:
            if self.con is None:
                raise RuntimeError(f"{self.repo} not loaded")
            return self.con.execute(sql, params).fetchall()

    def run(self, fn, *args):
        fut = self.pool.submit(contextvars.copy_context().run, fn, *args)
        try:
            return fut.result(timeout=CALL_TIMEOUT_S)
        except FuturesTimeout:
            fut.cancel()
            raise TimeoutError(f"{self.pfx} tool exceeded {CALL_TIMEOUT_S:.0f}s") from None

    def book(self, book: str) -> tuple[str, str]:
        key = _k(book)
        bk = self.names.get(key)
        if bk is None:
            hits = sorted({v for n, v in self.names.items() if key and (n.startswith(key) or key in n)})
            if len(hits) == 1:
                bk = hits[0]
            else:
                raise ValueError(f"unknown book {book!r}. Books: {', '.join(b for _, b, _, _ in self.order)}.")
        return bk, next(b for k, b, _, _ in self.order if k == bk)

    def books_impl(self) -> str:
        out = [f"{b} ({ch} ch, {n} v)" for _, b, n, ch in self.order]
        note = f" {self.note}" if getattr(self, "note", "") else ""
        return f"{len(out)} books: " + "; ".join(out) + f". Read with {self.pfx}_read(book, chapter).{note}"

    def read_impl(self, book: str, chapter: int, verse_start: int | None, verse_end: int | None) -> str:
        bk, name = self.book(book)
        c = int(chapter)
        lo, hi = self.q("SELECT min(chapter), max(chapter) FROM t WHERE book_key = ?", [bk])[0]
        if not lo <= c <= hi:
            raise ValueError(f"{name} chapter {c} is out of range: chapters {lo} to {hi}.")
        vmax = self.q("SELECT max(verse) FROM t WHERE book_key = ? AND chapter = ?", [bk, c])[0][0]
        if vmax is None:
            raise ValueError(f"{name} has no chapter {c} in this text (gap in the source).")
        s = int(verse_start) if verse_start else 1
        e = int(verse_end) if verse_end else s + MAX_VERSES - 1
        if not 1 <= s <= vmax and not (s == 0 and bk == "barnabas") or e < s:
            raise ValueError(f"{name} {c} has verses 1 to {vmax}; got {s}-{e}.")
        e = min(e, s + MAX_VERSES - 1)
        rows = self.q("SELECT id, verse, text, lang FROM t WHERE book_key = ? AND chapter = ? AND verse BETWEEN ? AND ? ORDER BY verse", [bk, c, s, e])
        out, last = [], s - 1
        for rid, v, txt, _ in rows:
            if out and sum(len(x) for x in out) + len(txt) > MAX_CHARS:
                break
            out.append(f"{v} {txt}")
            last = v
            note(rid)
        if not rows:
            raise ValueError(f"{name} {c}:{s}-{e} has no verses in this text.")
        ref = f"{name} {c}:{s}" + (f"-{last}" if last > s else "")
        nxt = f"{self.pfx}_read('{name}', {c}, {last + 1})" if last < vmax else (f"{self.pfx}_read('{name}', {c + 1})" if c < hi else "end of book")
        bookmarks.record(self.pfx, ref, {"book": name, "chapter": c, "verse_start": last + 1} if last < vmax else ({"book": name, "chapter": c + 1} if c < hi else None))
        return " ".join(out)[:MAX_CHARS + 400] + f" ({ref}).{_lang_note(rows[0][3])} Next: {nxt}"

    def search_impl(self, query: str, book: str | None, limit: int) -> str:
        words = re.findall(r"\w[\w']*", (query or "").lower())
        if not words:
            raise ValueError("query must contain words (English, or Ge'ez for Ge'ez books).")
        if not 1 <= int(limit) <= MAX_LIMIT:
            raise ValueError(f"limit {limit} is out of range: use 1 to {MAX_LIMIT}.")
        f, fp, scope = "", [], "all books"
        if book:
            bk, scope = self.book(book)
            f, fp = " AND book_key = ?", [bk]
        phrase = " ".join(words)
        where = " AND ".join("regexp_matches(lt, ?)" for _ in words)
        pats = [rf"\b{re.escape(w)}" if w.isascii() else re.escape(w) for w in words]
        rows = self.q(f"SELECT id, book, chapter, verse, text FROM t WHERE {where}{f} ORDER BY contains(lt, ?) DESC, id LIMIT ?", pats + fp + [phrase, int(limit)])
        total = self.q(f"SELECT count(*) FROM t WHERE {where}{f}", pats + fp)[0][0]
        if not rows:
            return f"No verse in {scope} has all of: {', '.join(words)}. Try fewer words or {self.pfx}_read()."
        note(*[r[0] for r in rows])
        out = [f"{b} {c}:{v}: {t[:200]}" for _, b, c, v, t in rows]
        return f"{total} verses match in {scope}; top {len(rows)}: " + " | ".join(out) + f". Next: {self.pfx}_read(book, chapter, verse) for context."

    def random_impl(self, book: str | None) -> str:
        f, fp = "", []
        if book:
            bk, _ = self.book(book)
            f, fp = " AND book_key = ?", [bk]
        rid, b, c, v, t, lg = self.q(f"SELECT id, book, chapter, verse, text, lang FROM t WHERE length(text) > 60{f} ORDER BY random() LIMIT 1", fp)[0]
        note(rid)
        return f"{t} ({b} {c}:{v}).{_lang_note(lg)} Next: {self.pfx}_read('{b}', {c}, {v + 1}) or {self.pfx}_read()."

    def register(self, mcp: FastMCP, docs: dict[str, str]) -> None:
        def read(book: str | None = None, chapter: int | None = None, verse_start: int | None = None, verse_end: int | None = None) -> str:
            if chapter is None:
                if verse_start is not None or verse_end is not None:
                    raise ValueError("give the chapter with the verses")
                return self.run(self.random_impl, book)
            if not book:
                raise ValueError(f"give the book with the chapter; books: {self.books_impl()}")
            return self.run(self.read_impl, book, chapter, verse_start, verse_end)

        def search(query: str | None = None, book: str | None = None, limit: int = 3) -> str:
            if not (query or "").strip():
                return self.run(self.books_impl)
            return self.run(self.search_impl, query, book, limit)

        for n, fn in [("read", read), ("search", search)]:
            fn.__name__ = f"{self.pfx}_{n}"
            mcp.tool(name=fn.__name__, description=docs[n])(logged(self.log)(fn))

    def main(self, mcp: FastMCP) -> None:
        try:
            self.load()
        except BaseException as e:
            self.log.critical(f"startup failed: dataset {self.repo} did not load: {type(e).__name__}: {e}")
            sys.exit(1)
        mcp.run(transport="stdio", show_banner=False)
