"""Female awliya MCP: al-Sulami Dhikr al-Niswa and Ibn al-Jawzi Sifat al-Safwa (women), original Arabic, from
hf://datasets/MTerryJack/female-awliya (OpenITI-derived, CC BY-NC-SA 4.0; DuckDB in memory, no downloads kept).
Tools: sulami_women_people/read/search/random and safwa_women_people/read/search/random. Each call logs via
mcp_tool_log; a failed dataset load exits at startup. Replies are short parts (~800 chars) for spoken audio."""

from __future__ import annotations

import contextvars
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

import duckdb
from fastmcp import FastMCP

from mcp_tool_log import get_logger, hf_revision, logged, note

REPO = "MTerryJack/female-awliya"
CALL_TIMEOUT_S = 8.0
MAX_LIMIT = 5
PEOPLE_PAGE = 40
SNIPPET_CHARS = 180
MIN_SULAMI = 80
MIN_SAFWA = 100
NOTE = "[Classical Arabic original: explain or translate it in the user's language.]"
_DIAC = "[\u064B-\u0652\u0670\u0640]"
_FROM, _TO = "أإآٱىةؤئ", "اااايهوي"
NORM_SQL = "translate(regexp_replace({c}, '" + _DIAC + "', '', 'g'), '" + _FROM + "', '" + _TO + "')"

NAMES = {
    "rabia": "رابعه العدويه", "rabi'a": "رابعه العدويه", "rabia al-adawiyya": "رابعه العدويه",
    "rabia al adawiyya": "رابعه العدويه", "rabia adawiya": "رابعه العدويه",
    "fatima of nishapur": "فاطمه النيسابوريه", "fatima nishapur": "فاطمه النيسابوريه",
    "umm haram": "ام حرام", "umm kulthum": "ام كلثوم", "aisha": "عائشه", "aysha": "عائشه",
    "hafsa": "حفصه", "zaynab": "زينب", "safiyya": "صفيه", "maymuna": "ميمونه",
    "umm salama": "ام سلمه", "umm ayman": "ام ايمن", "nufaysa": "نفيسه", "nafisa": "نفيسه",
    "sha'wana": "شعوانه", "shawana": "شعوانه", "fatima": "فاطمه", "maryam": "مريم", "mariam": "مريم",
    "rukayya": "رقيه", "ruqayya": "رقيه",
}
EN_AR = {
    "patience": "الصبر", "gratitude": "الشكر", "repentance": "التوبه", "asceticism": "الزهد", "zuhd": "الزهد",
    "piety": "الورع", "fear": "الخوف", "hope": "الرجاء", "love": "المحبه", "weeping": "البكاء", "crying": "البكاء",
    "death": "الموت", "prayer": "الصلاه", "night": "الليل", "fasting": "الصوم", "quran": "القران",
    "remembrance": "الذكر", "dhikr": "الذكر", "knowledge": "العلم", "sincerity": "الاخلاص", "trust": "التوكل",
    "contentment": "الرضا", "poverty": "الفقر", "heart": "القلب", "soul": "النفس", "sufi": "الصوفي",
    "saint": "الوليه", "saints": "الاولياء", "god": "الله", "allah": "الله", "prophet": "النبي",
    "women": "النساء", "woman": "المراه", "worship": "العباده", "tears": "الدموع",
}

BOOKS = {
    "sulami": {
        "key": "sulami",
        "label": "al-Sulami (السلمي), Dhikr al-Niswa al-Muta'abbidat as-Sufiyyat (ذكر النسوة المتعبدات الصوفيات)",
        "prefix": "sulami_women",
        "min": MIN_SULAMI,
        "A": "al-Sulami (السلمي) Dhikr al-Niswa (ذكر النسوة): use when the user asks about female awliya, Sufi women, pious women, Rabia al-Adawiyya, or al-Sulami's book of female devotees.",
    },
    "safwa": {
        "key": "safwa",
        "label": "Ibn al-Jawzi (ابن الجوزي), Sifat al-Safwa (صفة الصفوة) — women biographies",
        "prefix": "safwa_women",
        "min": MIN_SAFWA,
        "A": "Ibn al-Jawzi (ابن الجوزي) Sifat al-Safwa women (صفة الصفوة، تراجم النساء): use when the user asks about female awliya in Sifat al-Safwa, Ibn al-Jawzi on pious women, or Rabia and other women saints beyond al-Sulami.",
    },
}
H = "Text is classical Arabic: explain or translate it in the user's language. Replies are short parts for spoken audio; call again with part=N+1 for more."

log = get_logger("FEMALE_AWLIYA")
mcp = FastMCP("FemaleAwliya")
_lock = threading.Lock()
_con: duckdb.DuckDBPyConnection | None = None
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="fawliya")


def _load() -> None:
    global _con
    t0 = time.monotonic()
    rev = hf_revision(REPO)
    url = f"hf://datasets/{REPO}@{rev}/data/train.parquet"
    con = duckdb.connect(config={"extension_directory": "/opt/duckdb/extensions"})
    con.execute("LOAD httpfs")
    con.execute(
        "CREATE TABLE t AS SELECT id, book_key, book, book_ar, author, number, name_ar, part, text_ar, "
        + NORM_SQL.format(c="text_ar") + " AS nt, " + NORM_SQL.format(c="name_ar") + f" AS nn FROM read_parquet('{url}')"
    )
    rows = con.execute("SELECT count(*) FROM t").fetchone()[0]
    for bk, meta in BOOKS.items():
        ents = con.execute("SELECT count(DISTINCT number) FROM t WHERE book_key = ?", [bk]).fetchone()[0]
        if ents < meta["min"]:
            raise RuntimeError(f"{url} book {bk} has only {ents} entries (expected >= {meta['min']})")
    rabia = con.execute(
        "SELECT count(DISTINCT book_key || '-' || cast(number as varchar)) FROM t WHERE contains(nn, 'رابعه')"
    ).fetchone()[0]
    if rabia < 2:
        raise RuntimeError(f"{url} is missing Rabia entries (found {rabia} distinct biographies containing رابعه)")
    with _lock:
        _con = con
    log.info(f"loaded {REPO}@{rev[:12]} rows={rows} rabia_bios={rabia} ms={(time.monotonic() - t0) * 1000:.0f}")


def _q(sql: str, params: list) -> list:
    with _lock:
        if _con is None:
            raise RuntimeError("female-awliya dataset not loaded")
        return _con.execute(sql, params).fetchall()


def _run(fn, *args):
    fut = _pool.submit(contextvars.copy_context().run, fn, *args)
    try:
        return fut.result(timeout=CALL_TIMEOUT_S)
    except FuturesTimeout:
        fut.cancel()
        raise TimeoutError(f"female-awliya tool exceeded {CALL_TIMEOUT_S:.0f}s") from None


def _norm(s: str) -> str:
    return re.sub(_DIAC, "", s).translate(str.maketrans(_FROM, _TO))


def _map(q: str, extra: dict) -> str:
    s = _norm(q.lower()).replace("’", "'")
    for en in sorted(extra, key=len, reverse=True):
        s = re.sub(rf"(?<![a-z']){re.escape(en)}(?![a-z'])", " " + extra[en] + " ", s)
    return s


def _name_query(q: str) -> str:
    s = _map(q, NAMES)
    left = re.findall(r"[a-z]{2,}", s)
    if left:
        raise ValueError(
            f"name words {left} are not known; write the name in Arabic (e.g. رابعة العدوية) or a known name such as: "
            + ", ".join(sorted(NAMES)[:20])
        )
    return re.sub(r"\s+", " ", re.sub(r"[^\u0621-\u064A ]", " ", s)).strip()


def _terms(q: str) -> list[str]:
    s = _map(q, {**EN_AR, **NAMES})
    left = re.findall(r"[a-z]{2,}", s)
    if left:
        raise ValueError(
            f"query words {left} are not known in English; use Arabic keywords (e.g. الزهد, التوبه) or one of: "
            + ", ".join(sorted(EN_AR))
        )
    return list(dict.fromkeys(re.findall(r"[\u0621-\u064A0-9]+", s)))


def _find(book: str, person: str) -> int:
    p = str(person).strip()
    if re.fullmatch(r"\d+", p):
        n, last = int(p), _q("SELECT max(number) FROM t WHERE book_key = ?", [book])[0][0]
        if not 1 <= n <= last:
            raise ValueError(f"number {n} is out of range for {book}: entries are 1 to {last}.")
        return n
    nq = _name_query(p)
    if not nq:
        raise ValueError("person must be an entry number or a name.")
    toks = nq.split()
    rows = _q("SELECT number FROM t WHERE book_key = ? AND nn = ? GROUP BY 1 ORDER BY 1 LIMIT 1", [book, nq]) or _q(
        "SELECT number FROM t WHERE book_key = ? AND "
        + " AND ".join("contains(nn, ?)" for _ in toks)
        + " GROUP BY 1 ORDER BY min(length(nn)), 1 LIMIT 1",
        [book] + toks,
    )
    if not rows:
        raise ValueError(
            f"No biography named {nq!r} in {book}. Try {BOOKS[book]['prefix']}_people(query=...) or search."
        )
    return rows[0][0]


def _read_impl(book: str, person: str, part: int) -> str:
    meta = BOOKS[book]
    n, p = _find(book, person), int(part)
    last = _q("SELECT max(number) FROM t WHERE book_key = ?", [book])[0][0]
    rows = _q(
        "SELECT id, name_ar, text_ar, (SELECT max(part) FROM t WHERE book_key = ? AND number = ?) "
        "FROM t WHERE book_key = ? AND number = ? AND part = ?",
        [book, n, book, n, p],
    )
    if not rows:
        m = _q("SELECT max(part) FROM t WHERE book_key = ? AND number = ?", [book, n])[0][0]
        raise ValueError(f"part {p} is out of range: entry {n} has parts 1 to {m}.")
    rid, name, txt, m = rows[0]
    note(rid)
    pref = meta["prefix"]
    nxt = f"{pref}_read(person='{n}', part={p + 1})" if p < m else (
        f"{pref}_read(person='{n + 1}')" if n < last else "end of book"
    )
    return f"{NOTE}\n{meta['label']}. Entry {n}: {name}. Part {p} of {m}:\n{txt}\nNext: {nxt}"


def _people_impl(book: str, query: str | None, page: int) -> str:
    meta = BOOKS[book]
    pg = int(page)
    if pg < 1:
        raise ValueError("page must be 1 or more.")
    f, fp = " WHERE book_key = ?", [book]
    if query and query.strip():
        nq = _name_query(query)
        if not nq:
            raise ValueError(f"query {query!r} has no Arabic letters or known names.")
        f += " AND " + " AND ".join("contains(nn, ?)" for _ in nq.split())
        fp += nq.split()
    rows = _q(f"SELECT number, any_value(name_ar), max(part) FROM t{f} GROUP BY 1 ORDER BY 1", fp)
    if not rows:
        return f"No biography name matches {query!r} in {book}. Try {meta['prefix']}_search()."
    pages = (len(rows) + PEOPLE_PAGE - 1) // PEOPLE_PAGE
    if pg > pages:
        raise ValueError(f"page {pg} is out of range: 1 to {pages}.")
    out = [f"{n}. {nm[:80]} ({k} parts)" for n, nm, k in rows[(pg - 1) * PEOPLE_PAGE : pg * PEOPLE_PAGE]]
    more = (
        f"\nMore: {meta['prefix']}_people(page={pg + 1}{', query=' + repr(query) if query else ''})"
        if pg < pages
        else ""
    )
    return (
        f"{NOTE}\n{meta['label']}: {len(rows)} entries{' matching ' + repr(query) if query else ''}, "
        f"page {pg}/{pages}. Open with {meta['prefix']}_read(person='N').\n"
        + "\n".join(out)
        + more
    )


def _search_impl(book: str, query: str, limit: int) -> str:
    meta = BOOKS[book]
    if not (query or "").strip():
        raise ValueError("query must be non-empty: Arabic keywords, a common English topic word, or a famous name.")
    if not 1 <= int(limit) <= MAX_LIMIT:
        raise ValueError(f"limit {limit} is out of range: use 1 to {MAX_LIMIT}.")
    terms = _terms(query)
    if not terms:
        raise ValueError(f"query {query!r} has no Arabic letters or known English words.")
    pats = [re.escape(x) for x in terms]
    where = " AND ".join("contains(nt, ?)" for _ in terms)
    score = " + ".join("len(regexp_extract_all(nt, ?))" for _ in terms)
    rows = _q(
        f"SELECT id, number, part, name_ar, nt FROM t WHERE book_key = ? AND {where} "
        f"ORDER BY ({score}) DESC, number, part LIMIT ?",
        [book] + terms + pats + [int(limit)],
    )
    note(*[r[0] for r in rows])
    if not rows:
        return f"No passage in {book} contains all of {terms}. Try fewer words or {meta['prefix']}_random()."
    out = []
    for _, n, p, name, nt in rows:
        t = re.sub(r"\s+", " ", nt)
        i = max(0, t.find(terms[0]) - 60)
        out.append(
            f"- Entry {n} ({name[:50]}), part {p}: …{t[i:i + SNIPPET_CHARS]}… "
            f"-> {meta['prefix']}_read(person='{n}', part={p})"
        )
    return f"{NOTE}\n{meta['label']}, searched for {' '.join(terms)}:\n" + "\n".join(out)


def _random_impl(book: str) -> str:
    n, p = _q(
        "SELECT number, part FROM t WHERE book_key = ? AND length(text_ar) > 400 ORDER BY random() LIMIT 1",
        [book],
    )[0]
    return _read_impl(book, str(n), p)


def _register(book: str) -> None:
    meta = BOOKS[book]
    pref, A = meta["prefix"], meta["A"]

    def people(query: str | None = None, page: int = 1) -> str:
        return _run(_people_impl, book, query, page)

    def read(person: str, part: int = 1) -> str:
        return _run(_read_impl, book, person, part)

    def search(query: str, limit: int = 3) -> str:
        return _run(_search_impl, book, query, limit)

    def random_() -> str:
        return _run(_random_impl, book)

    people.__name__ = f"{pref}_people"
    read.__name__ = f"{pref}_read"
    search.__name__ = f"{pref}_search"
    random_.__name__ = f"{pref}_random"
    mcp.tool(
        name=f"{pref}_people",
        description=f"{A} Lists the women biographies in book order. Optional query filters names (Arabic, or famous names like Rabia, Fatima, Aisha); page (40 per page). Returns entry numbers for {pref}_read. {H}",
    )(logged(log)(people))
    mcp.tool(
        name=f"{pref}_read",
        description=f"{A} Reads one woman's biography. person: entry number from {pref}_people/{pref}_search, or a name (Arabic or famous English, e.g. 'Rabia'); part 1.. (about 800 characters each for spoken audio). Returns the text and a Next: call. {H}",
    )(logged(log)(read))
    mcp.tool(
        name=f"{pref}_search",
        description=f"{A} Full-text search of the women's biographies (zuhd, love, weeping, prayer, Rabia, etc.). query: Arabic keywords, common English topic words, or famous names; all must match. limit 1-5. Returns snippets with the {pref}_read call. {H}",
    )(logged(log)(search))
    mcp.tool(
        name=f"{pref}_random",
        description=f"{A} Use for a random passage from a female awliya biography. Returns one short part with a Next: call. {H}",
    )(logged(log)(random_))


for _bk in BOOKS:
    _register(_bk)


if __name__ == "__main__":
    try:
        _load()
    except BaseException as e:
        log.critical(f"startup failed: dataset {REPO} did not load: {type(e).__name__}: {e}")
        sys.exit(1)
    mcp.run(transport="stdio", show_banner=False)
