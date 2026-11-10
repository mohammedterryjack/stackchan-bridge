"""Rumi Mathnawi MCP: on-demand reads of hf://datasets/MTerryJack/rumi-mathnawi (Nicholson English, Books I-VI)."""
from __future__ import annotations

import re

from app import mcp
from hf_data import HFTable

import bookmarks

BOOKS = (1, 2, 3, 4, 5, 6)
MAX_LIMIT = 5
PAGE_CHARS = 1400
SNIPPET_CHARS = 160

_q = HFTable("MTerryJack/rumi-mathnawi", "Rumi's Mathnawi", {"rumi": "SELECT book, section, verse_start, verse_end, text_en FROM {src}"}).q


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
    bookmarks.record("rumi", f"Book {book}, verses {a}-{b}", {"book": book, "verse": nxt} if nxt is not None else ({"book": book + 1, "verse": 1} if book < 6 else None))
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
    base = f"WITH d AS (SELECT book, verse_start, verse_end, text_en, lt, len(string_split(lt, ' ')) AS dl, {tf_cols} FROM (SELECT *, lower(text_en) AS lt FROM rumi))"
    stats = _q(f"{base} SELECT count(*), avg(dl), {df_cols} FROM d", pats)[0]
    n, avgdl, dfs = stats[0], stats[1], stats[2:]
    idf = [__import__("math").log(1 + (n - df + 0.5) / (df + 0.5)) for df in dfs]
    score = " + ".join(f"{idf[i]!r} * tf{i} * 2.2 / (tf{i} + 1.2 * (0.25 + 0.75 * dl / {avgdl!r}))" for i in range(len(terms)))
    rows = _q(
        f"{base} SELECT book, verse_start, verse_end, text_en FROM d WHERE {where} ORDER BY ({score}) + CASE WHEN contains(lt, ?) THEN 1.0 ELSE 0 END DESC, book, verse_start LIMIT ?",
        pats + [q.lower(), limit],
    )
    if not rows:
        return f"No passages mention {q!r}."
    def snip(t: str) -> str:
        return _snippet(t, q) if q.lower() in t.lower() else _snippet(t, terms[0])
    return "\n".join(f"{snip(t)} {_ref(b, a, e)}" for b, a, e, t in rows)


def _random_impl() -> str:
    b, a, e, sec, t = _q("SELECT book, verse_start, verse_end, section, text_en FROM rumi WHERE length(text_en) > 200 ORDER BY random() LIMIT 1", [])[0]
    head = f"{sec}. " if sec else ""
    return f"{head}{_speak(t)} {_ref(b, a, e)}\nNext: rumi_read(book={b}, verse={e + 1})"


def _read_or_random(book: int | None, verse: int | None, section: str | None) -> str:
    if book is None:
        if verse is not None or section:
            raise ValueError("give the Mathnawi book (1-6) with the verse or section.")
        return _random_impl()
    return _read_impl(book, verse, section)


@mcp.tool(name="rumi_read", description="Rumi's Mathnawi: read a passage by book (1-6) and verse or section title, or a random passage when no book is given ('read me some Rumi').")
def rumi_read(book: int | None = None, verse: int | None = None, section: str | None = None) -> str:
    return _read_or_random(book, verse, section)


@mcp.tool(name="rumi_search", description="Rumi's Mathnawi: find passages on a theme or phrase, e.g. 'Rumi on love'.")
def rumi_search(query: str, limit: int = 3) -> str:
    return _search_impl(query, limit)
