"""Sufi classics MCP: al-Ghazali's Ihya Ulum al-Din, Ibn Arabi's al-Futuhat al-Makkiyya and Abd al-Qadir al-Jilani's
al-Ghunya, original Arabic, from hf://datasets/MTerryJack/sufi-classics (OpenITI-derived, CC BY-NC-SA 4.0; DuckDB in memory).
One tool set per author (ghazali_*, ibnarabi_*, jilani_*): _contents, _read, _search, _random. Each call logs one stderr
line via mcp_tool_log; a failed dataset load exits at startup."""

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

REPO = "MTerryJack/sufi-classics"
CALL_TIMEOUT_S = 8.0
MAX_LIMIT = 5
CONTENTS_PAGE = 30
SNIPPET_CHARS = 180
NOTE = "[Classical Arabic original: explain or translate it in the user's language.]"
_DIAC = "[\u064B-\u0652\u0670\u0640]"
_FROM, _TO = "أإآٱىةؤئ", "اااايهوي"
NORM_SQL = "translate(regexp_replace({c}, '" + _DIAC + "', '', 'g'), '" + _FROM + "', '" + _TO + "')"
# prefix -> (book_key, English label, Arabic label)
AUTHORS = {
    "ghazali": ("ihya", "Al-Ghazali (الغزالي), Ihya Ulum al-Din (إحياء علوم الدين)"),
    "ibnarabi": ("futuhat", "Ibn Arabi (ابن عربي), al-Futuhat al-Makkiyya (الفتوحات المكية)"),
    "jilani": ("ghunya", "Abd al-Qadir al-Jilani (عبد القادر الجيلاني), al-Ghunya li-Talibi Tariq al-Haqq (الغنية لطالبي طريق الحق, Ghunyat al-Talibin)"),
}
TOPICS = {
    "ihya": "knowledge, worship, prayer, fasting, manners, the heart, anger, envy, pride, repentance, patience, gratitude, love, death",
    "futuhat": "the spirit, divine names, letters, stations, states, knowledge of God, unity of being, the Prophet, saints, spiritual journey",
    "ghunya": "fiqh of worship, etiquette, beliefs, Quran merits, months and days (Ramadan, Rajab), sermons (majalis), Sufi path, disciples and shaykhs",
}
EN_AR = {
    "knowledge": "العلم", "prayer": "الصلاه", "salat": "الصلاه", "fasting": "الصوم", "ramadan": "رمضان", "rajab": "رجب", "hajj": "الحج",
    "zakat": "الزكاه", "charity": "الصدقه", "heart": "القلب", "soul": "النفس", "self": "النفس", "spirit": "الروح", "love": "المحبه",
    "repentance": "التوبه", "patience": "الصبر", "gratitude": "الشكر", "thanks": "الشكر", "fear": "الخوف", "hope": "الرجاء",
    "death": "الموت", "grave": "القبر", "paradise": "الجنه", "hell": "النار", "anger": "الغضب", "envy": "الحسد", "pride": "الكبر",
    "arrogance": "الكبر", "humility": "التواضع", "sincerity": "الاخلاص", "trust": "التوكل", "reliance": "التوكل", "remembrance": "الذكر",
    "dhikr": "الذكر", "quran": "القران", "prophet": "النبي", "god": "الله", "allah": "الله", "names": "الاسماء", "mercy": "الرحمه",
    "marriage": "النكاح", "food": "الطعام", "eating": "الاكل", "sleep": "النوم", "dream": "الرويا", "dreams": "الرويا", "money": "المال",
    "wealth": "المال", "world": "الدنيا", "dunya": "الدنيا", "tongue": "اللسان", "lying": "الكذب", "backbiting": "الغيبه", "friendship": "الصحبه",
    "brotherhood": "الاخوه", "solitude": "العزله", "travel": "السفر", "music": "السماع", "contemplation": "التفكر", "reflection": "التفكر",
    "intention": "النيه", "truthfulness": "الصدق", "contentment": "الرضا", "asceticism": "الزهد", "zuhd": "الزهد", "poverty": "الفقر",
    "saints": "الاولياء", "saint": "الولي", "sufi": "التصوف", "sufism": "التصوف", "disciple": "المريد", "shaykh": "الشيخ", "light": "النور",
    "letters": "الحروف", "station": "المقام", "unveiling": "الكشف", "jesus": "عيسي", "moses": "موسي", "abraham": "ابراهيم", "angels": "الملايكه",
    "jinn": "الجن", "night": "الليل", "friday": "الجمعه", "ablution": "الوضوء", "purity": "الطهاره", "sin": "الذنب", "sins": "الذنوب",
}

log = get_logger("SUFI_CLASSICS")
mcp = FastMCP("SufiClassics")
_lock = threading.Lock()
_con: duckdb.DuckDBPyConnection | None = None
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sufi")


def _load() -> None:
    global _con
    t0 = time.monotonic()
    rev = hf_revision(REPO)
    url = f"hf://datasets/{REPO}@{rev}/data/train.parquet"
    con = duckdb.connect(config={"extension_directory": "/opt/duckdb/extensions"})
    con.execute("LOAD httpfs")
    con.execute("CREATE TABLE t AS SELECT id, book_key, book, book_ar, volume_ar, chapter_number, chapter_title_ar, part, text_ar, licence, "
                + NORM_SQL.format(c="text_ar") + f" AS nt FROM read_parquet('{url}')")
    counts = dict(con.execute("SELECT book_key, count(*) FROM t GROUP BY 1").fetchall())
    missing = {k for k, _ in AUTHORS.values()} - set(counts)
    if missing:
        raise RuntimeError(f"Books missing from {url}: {sorted(missing)}")
    with _lock:
        _con = con
    log.info(f"loaded {REPO}@{rev[:12]} rows={sum(counts.values())} " + " ".join(f"[{k}]={v}" for k, v in sorted(counts.items())) + f" ms={(time.monotonic() - t0) * 1000:.0f}")


def _q(sql: str, params: list) -> list:
    with _lock:
        if _con is None:
            raise RuntimeError("Sufi classics dataset not loaded")
        return _con.execute(sql, params).fetchall()


def _run(fn, *args):
    fut = _pool.submit(contextvars.copy_context().run, fn, *args)
    try:
        return fut.result(timeout=CALL_TIMEOUT_S)
    except FuturesTimeout:
        fut.cancel()
        raise TimeoutError(f"sufi tool exceeded {CALL_TIMEOUT_S:.0f}s") from None


def _norm(s: str) -> str:
    return re.sub(_DIAC, "", s).translate(str.maketrans(_FROM, _TO))


def _terms(q: str) -> list[str]:
    s = _norm(q.lower())
    for en in sorted(EN_AR, key=len, reverse=True):
        s = re.sub(rf"\b{re.escape(en)}\b", " " + EN_AR[en] + " ", s)
    left = re.findall(r"[a-z]{2,}", s)
    if left:
        raise ValueError(f"query words {left} are not known in English; use Arabic keywords (e.g. التوبه, المحبه) or one of: {', '.join(sorted(EN_AR))}.")
    return list(dict.fromkeys(re.findall(r"[\u0621-\u064A0-9]+", s)))


def _read_impl(pfx: str, chapter: int, part: int) -> str:
    bk, label = AUTHORS[pfx]
    c, p = int(chapter), int(part)
    last = _q("SELECT max(chapter_number) FROM t WHERE book_key = ?", [bk])[0][0]
    if not 1 <= c <= last:
        raise ValueError(f"chapter {c} is out of range: chapters are 1 to {last}. Use {pfx}_contents() to find one.")
    rows = _q("SELECT id, volume_ar, chapter_title_ar, text_ar, (SELECT max(part) FROM t WHERE book_key = ? AND chapter_number = ?) FROM t WHERE book_key = ? AND chapter_number = ? AND part = ?", [bk, c, bk, c, p])
    if not rows:
        n = _q("SELECT max(part) FROM t WHERE book_key = ? AND chapter_number = ?", [bk, c])[0][0]
        raise ValueError(f"part {p} is out of range: chapter {c} has parts 1 to {n}.")
    rid, vol, title, txt, n = rows[0]
    note(rid)
    nxt = f"{pfx}_read(chapter={c}, part={p + 1})" if p < n else (f"{pfx}_read(chapter={c + 1})" if c < last else "end of book")
    where = f"{vol} / " if vol else ""
    return f"{NOTE}\n{label}. {where}Chapter {c}: {title}. Part {p}/{n}:\n{txt}\nNext: {nxt}"


def _contents_impl(pfx: str, page: int, query: str | None) -> str:
    bk, label = AUTHORS[pfx]
    pg = int(page)
    if pg < 1:
        raise ValueError("page must be 1 or more.")
    f, fp = "", []
    if query and query.strip():
        terms = _terms(query)
        if not terms:
            raise ValueError(f"query {query!r} has no Arabic letters or known English words.")
        f = "".join(" AND " + NORM_SQL.format(c="chapter_title_ar || ' ' || volume_ar") + " LIKE ?" for _ in terms)
        fp = [f"%{x}%" for x in terms]
    rows = _q(f"SELECT chapter_number, any_value(volume_ar), any_value(chapter_title_ar), max(part) FROM t WHERE book_key = ?{f} GROUP BY 1 ORDER BY 1", [bk] + fp)
    if not rows:
        return f"No chapter titles in {label} match {query!r}. Try {pfx}_search() to search the full text."
    pages = (len(rows) + CONTENTS_PAGE - 1) // CONTENTS_PAGE
    if pg > pages:
        raise ValueError(f"page {pg} is out of range: 1 to {pages}.")
    out = [f"{c}. {(v + ' / ') if v else ''}{t[:120]} ({n} parts)" for c, v, t, n in rows[(pg - 1) * CONTENTS_PAGE: pg * CONTENTS_PAGE]]
    more = f"\nMore: {pfx}_contents(page={pg + 1}{', query=' + repr(query) if query else ''})" if pg < pages else ""
    return f"{NOTE}\n{label}: {len(rows)} chapters{' matching ' + repr(query) if query else ''}, page {pg}/{pages}. Open with {pfx}_read(chapter=N).\n" + "\n".join(out) + more


def _search_impl(pfx: str, query: str, limit: int) -> str:
    bk, label = AUTHORS[pfx]
    if not (query or "").strip():
        raise ValueError("query must be non-empty: Arabic keywords or a common English topic word.")
    if not 1 <= int(limit) <= MAX_LIMIT:
        raise ValueError(f"limit {limit} is out of range: use 1 to {MAX_LIMIT}.")
    terms = _terms(query)
    if not terms:
        raise ValueError(f"query {query!r} has no Arabic letters or known English topic words.")
    pats = [re.escape(x) for x in terms]
    where = " AND ".join("contains(nt, ?)" for _ in terms)
    score = " + ".join("len(regexp_extract_all(nt, ?))" for _ in terms)
    rows = _q(f"SELECT id, chapter_number, part, chapter_title_ar, nt FROM t WHERE book_key = ? AND {where} ORDER BY ({score}) DESC, chapter_number, part LIMIT ?",
              [bk] + terms + pats + [int(limit)])
    note(*[r[0] for r in rows])
    if not rows:
        return f"No passage in {label} contains all of {terms}. Try fewer or other words, or {pfx}_random()."
    out = []
    for _, c, p, title, nt in rows:
        t = re.sub(r"\s+", " ", nt)
        i = max(0, t.find(terms[0]) - 60)
        out.append(f"- Chapter {c} ({title[:60]}), part {p}: …{t[i:i + SNIPPET_CHARS]}… -> {pfx}_read(chapter={c}, part={p})")
    return f"{NOTE}\n{label}, searched for {' '.join(terms)}:\n" + "\n".join(out)


def _random_impl(pfx: str) -> str:
    bk, _ = AUTHORS[pfx]
    c, p = _q("SELECT chapter_number, part FROM t WHERE book_key = ? AND length(text_ar) > 800 ORDER BY random() LIMIT 1", [bk])[0]
    return _read_impl(pfx, c, p)


def _register(pfx: str) -> None:
    bk, label = AUTHORS[pfx]
    author = label.split(",")[0]
    hint = f"{label}. Text is classical Arabic: explain or translate it in the user's language."

    def contents(page: int = 1, query: str | None = None) -> str:
        return _run(_contents_impl, pfx, page, query)

    def read(chapter: int, part: int = 1) -> str:
        return _run(_read_impl, pfx, chapter, part)

    def search(query: str, limit: int = 3) -> str:
        return _run(_search_impl, pfx, query, limit)

    def random() -> str:
        return _run(_random_impl, pfx)

    docs = {
        "contents": f"{author}: use when the user asks what is in {label.split(', ', 1)[1]}, its chapters or books, or to find a chapter by title. page (30 per page); optional query filters titles (Arabic or English topic words). Returns chapter numbers for {pfx}_read. {hint}",
        "read": f"{author}: use when the user asks to read or continue a chapter of {label.split(', ', 1)[1]}. chapter number from {pfx}_contents or {pfx}_search; part 1.. (about 2000 characters each). Returns the text and a Next: call. {hint}",
        "search": f"{author}: use when the user asks what {author.split(' (')[0]} says about a topic (e.g. {TOPICS[bk]}). query: Arabic keywords or common English topic words; all must match. limit 1-5. Returns snippets with the {pfx}_read call to open each. {hint}",
        "random": f"{author}: use when the user mentions {author.split(' (')[0]} or asks for a random passage, wisdom or reading from {label.split(', ', 1)[1]}. Returns one passage with its chapter and a Next: call. {hint}",
    }
    for name, fn in [("contents", contents), ("read", read), ("search", search), ("random", random)]:
        fn.__name__ = f"{pfx}_{name}"
        mcp.tool(name=fn.__name__, description=docs[name])(logged(log)(fn))


for _p in AUTHORS:
    _register(_p)


if __name__ == "__main__":
    try:
        _load()
    except BaseException as e:
        log.critical(f"startup failed: dataset {REPO} did not load: {type(e).__name__}: {e}")
        sys.exit(1)
    mcp.run(transport="stdio", show_banner=False)
