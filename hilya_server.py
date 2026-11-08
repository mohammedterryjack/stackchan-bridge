"""Hilyat al-Awliya MCP: Abu Nu'aym al-Isfahani's Hilyat al-Awliya wa Tabaqat al-Asfiya, original Arabic, from
hf://datasets/MTerryJack/hilyat-al-awliya (OpenITI-derived, CC BY-NC-SA 4.0; DuckDB in memory, no downloads kept).
Tools: hilya_people, hilya_read, hilya_search, hilya_random. Each call logs one stderr line via mcp_tool_log;
a failed dataset load exits at startup."""

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

REPO = "MTerryJack/hilyat-al-awliya"
CALL_TIMEOUT_S = 8.0
MAX_LIMIT = 5
PEOPLE_PAGE = 40
SNIPPET_CHARS = 180
MIN_ENTRIES = 600
LABEL = "Abu Nu'aym al-Isfahani (أبو نعيم الأصفهاني), Hilyat al-Awliya wa Tabaqat al-Asfiya (حلية الأولياء وطبقات الأصفياء)"
NOTE = "[Classical Arabic original: explain or translate it in the user's language.]"
_DIAC = "[\u064B-\u0652\u0670\u0640]"
_FROM, _TO = "أإآٱىةؤئ", "اااايهوي"
NORM_SQL = "translate(regexp_replace({c}, '" + _DIAC + "', '', 'g'), '" + _FROM + "', '" + _TO + "')"
# Famous names (normalised Arabic). Longest English keys are replaced first.
NAMES = {
    "hasan al-basri": "الحسن البصري", "hasan basri": "الحسن البصري", "al-hasan al-basri": "الحسن البصري",
    "rabia": "رابعه العدويه", "rabi'a": "رابعه العدويه", "rabia al-adawiyya": "رابعه العدويه",
    "ibrahim ibn adham": "ابراهيم بن ادهم", "ibrahim bin adham": "ابراهيم بن ادهم", "ibn adham": "ابراهيم بن ادهم",
    "fudayl ibn iyad": "الفضيل بن عياض", "fudayl": "الفضيل بن عياض", "fudail": "الفضيل بن عياض",
    "bishr al-hafi": "بشر بن الحارث", "bishr": "بشر بن الحارث", "junayd": "الجنيد", "junaid": "الجنيد",
    "abu bakr": "ابو بكر الصديق", "umar": "عمر بن الخطاب", "omar": "عمر بن الخطاب", "uthman": "عثمان بن عفان",
    "ali": "علي بن ابي طالب", "abu dharr": "ابو ذر", "salman al-farisi": "سلمان الفارسي", "salman": "سلمان الفارسي",
    "bilal": "بلال بن رباح", "abu darda": "ابو الدرداء", "abu al-darda": "ابو الدرداء", "muadh": "معاذ بن جبل",
    "ibn masud": "عبدالله بن مسعود", "ibn mas'ud": "عبدالله بن مسعود", "ibn abbas": "عبدالله بن عباس", "ibn umar": "عبدالله بن عمر",
    "uways": "اويس القرني", "uways al-qarani": "اويس القرني", "sufyan al-thawri": "سفيان الثوري", "sufyan thawri": "سفيان الثوري",
    "sufyan ibn uyayna": "سفيان بن عيينه", "malik ibn dinar": "مالك بن دينار", "malik ibn anas": "مالك بن انس", "imam malik": "مالك بن انس",
    "shafii": "الشافعي", "al-shafi'i": "الشافعي", "ahmad ibn hanbal": "احمد بن حنبل", "ibn hanbal": "احمد بن حنبل",
    "abdullah ibn al-mubarak": "عبدالله بن المبارك", "ibn al-mubarak": "عبدالله بن المبارك", "dawud al-tai": "داود الطايي",
    "maruf al-karkhi": "معروف الكرخي", "ma'ruf": "معروف", "sari al-saqati": "السري", "dhul-nun": "ذو النون", "dhu al-nun": "ذو النون",
    "bayazid": "ابو يزيد", "abu yazid": "ابو يزيد", "bistami": "البسطامي", "harith al-muhasibi": "الحارث المحاسبي", "muhasibi": "المحاسبي",
    "sahl al-tustari": "سهل بن عبدالله", "tustari": "التستري", "said ibn al-musayyib": "سعيد بن المسيب", "umar ibn abd al-aziz": "عمر بن عبدالعزيز",
    "wahb ibn munabbih": "وهب بن منبه", "abu sulayman al-darani": "ابو سليمان الداراني", "darani": "الداراني", "shaqiq al-balkhi": "شقيق البلخي",
    "hatim al-asamm": "حاتم الاصم", "yahya ibn muadh": "يحيي بن معاذ", "ahl al-suffa": "اهل الصفه", "suffa": "الصفه",
    "zayn al-abidin": "علي بن الحسين", "jafar al-sadiq": "جعفر بن محمد", "hasan": "الحسن", "husayn": "الحسين",
}
EN_AR = {
    "patience": "الصبر", "gratitude": "الشكر", "repentance": "التوبه", "asceticism": "الزهد", "zuhd": "الزهد", "renunciation": "الزهد",
    "piety": "الورع", "scrupulousness": "الورع", "fear": "الخوف", "hope": "الرجاء", "love": "المحبه", "weeping": "البكاء", "crying": "البكاء",
    "death": "الموت", "grave": "القبر", "paradise": "الجنه", "hell": "النار", "world": "الدنيا", "dunya": "الدنيا", "hereafter": "الاخره",
    "prayer": "الصلاه", "night": "الليل", "fasting": "الصوم", "quran": "القران", "remembrance": "الذكر", "dhikr": "الذكر",
    "knowledge": "العلم", "humility": "التواضع", "pride": "الكبر", "sincerity": "الاخلاص", "trust": "التوكل", "reliance": "التوكل",
    "contentment": "الرضا", "poverty": "الفقر", "wealth": "المال", "money": "المال", "heart": "القلب", "soul": "النفس", "self": "النفس",
    "tongue": "اللسان", "silence": "الصمت", "solitude": "العزله", "hunger": "الجوع", "sin": "الذنب", "sins": "الذنوب", "mercy": "الرحمه",
    "sufi": "الصوفي", "sufism": "التصوف", "saint": "الولي", "saints": "الاولياء", "god": "الله", "allah": "الله", "prophet": "النبي",
    "truthfulness": "الصدق", "intention": "النيه", "charity": "الصدقه", "dream": "رويا", "jihad": "الجهاد", "ruler": "السلطان", "sultan": "السلطان",
}

log = get_logger("HILYA")
mcp = FastMCP("HilyatAlAwliya")
_lock = threading.Lock()
_con: duckdb.DuckDBPyConnection | None = None
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="hilya")


def _load() -> None:
    global _con
    t0 = time.monotonic()
    rev = hf_revision(REPO)
    url = f"hf://datasets/{REPO}@{rev}/data/train.parquet"
    con = duckdb.connect(config={"extension_directory": "/opt/duckdb/extensions"})
    con.execute("LOAD httpfs")
    con.execute("CREATE TABLE t AS SELECT id, volume, number, name_ar, section_ar, part, text_ar, "
                + NORM_SQL.format(c="text_ar") + " AS nt, " + NORM_SQL.format(c="name_ar") + f" AS nn FROM read_parquet('{url}')")
    rows, ents = con.execute("SELECT count(*), count(DISTINCT number) FROM t").fetchone()
    if ents < MIN_ENTRIES:
        raise RuntimeError(f"{url} has only {ents} entries (expected >= {MIN_ENTRIES})")
    with _lock:
        _con = con
    log.info(f"loaded {REPO}@{rev[:12]} rows={rows} entries={ents} ms={(time.monotonic() - t0) * 1000:.0f}")


def _q(sql: str, params: list) -> list:
    with _lock:
        if _con is None:
            raise RuntimeError("Hilyat al-Awliya dataset not loaded")
        return _con.execute(sql, params).fetchall()


def _run(fn, *args):
    fut = _pool.submit(contextvars.copy_context().run, fn, *args)
    try:
        return fut.result(timeout=CALL_TIMEOUT_S)
    except FuturesTimeout:
        fut.cancel()
        raise TimeoutError(f"hilya tool exceeded {CALL_TIMEOUT_S:.0f}s") from None


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
        raise ValueError(f"name words {left} are not known; write the name in Arabic (e.g. سفيان الثوري) or use a known name such as: {', '.join(sorted(NAMES)[:25])}...")
    return re.sub(r"\s+", " ", re.sub(r"[^\u0621-\u064A ]", " ", s)).strip()


def _terms(q: str) -> list[str]:
    s = _map(q, {**EN_AR, **NAMES})
    left = re.findall(r"[a-z]{2,}", s)
    if left:
        raise ValueError(f"query words {left} are not known in English; use Arabic keywords (e.g. الزهد, التوبه) or one of: {', '.join(sorted(EN_AR))}.")
    return list(dict.fromkeys(re.findall(r"[\u0621-\u064A0-9]+", s)))


def _find(person: str) -> int:
    p = str(person).strip()
    if re.fullmatch(r"\d+", p):
        n, last = int(p), _q("SELECT max(number) FROM t", [])[0][0]
        if not 1 <= n <= last:
            raise ValueError(f"number {n} is out of range: entries are 1 to {last}. Use hilya_people() to find one.")
        return n
    nq = _name_query(p)
    if not nq:
        raise ValueError("person must be an entry number or a name.")
    rows = _q("SELECT number FROM t WHERE nn = ? GROUP BY 1 ORDER BY 1 LIMIT 1", [nq]) or \
        _q("SELECT number FROM t WHERE " + " AND ".join("contains(nn, ?)" for _ in nq.split()) + " GROUP BY 1 ORDER BY min(length(nn)), 1 LIMIT 1", nq.split())
    if not rows:
        raise ValueError(f"No biography named {nq!r} in the Hilya (e.g. Rabia al-Adawiyya has no entry). Try hilya_people(query=...) or hilya_search({p!r}).")
    return rows[0][0]


def _read_impl(person: str, part: int, mark: bool = False) -> str:
    n, p = _find(person), int(part)
    last = _q("SELECT max(number) FROM t", [])[0][0]
    rows = _q("SELECT id, volume, name_ar, text_ar, (SELECT max(part) FROM t WHERE number = ?) FROM t WHERE number = ? AND part = ?", [n, n, p])
    if not rows:
        m = _q("SELECT max(part) FROM t WHERE number = ?", [n])[0][0]
        raise ValueError(f"part {p} is out of range: entry {n} has parts 1 to {m}.")
    rid, vol, name, txt, m = rows[0]
    note(rid)
    nxt = f"hilya_read(person='{n}', part={p + 1})" if p < m else (f"hilya_read(person='{n + 1}')" if n < last else "end of book")
    if mark:
        bookmarks.record("hilya", f"entry {n}, part {p} of {m}", {"person": str(n), "part": p + 1} if p < m else ({"person": str(n + 1)} if n < last else None))
    return f"{NOTE}\n{LABEL}. Volume {vol}, entry {n}: {name}. Part {p}/{m}:\n{txt}\nNext: {nxt}"


def _people_impl(query: str | None, page: int) -> str:
    pg = int(page)
    if pg < 1:
        raise ValueError("page must be 1 or more.")
    f, fp = "", []
    if query and query.strip():
        nq = _name_query(query)
        if not nq:
            raise ValueError(f"query {query!r} has no Arabic letters or known names.")
        f = " WHERE " + " AND ".join("contains(nn, ?)" for _ in nq.split())
        fp = nq.split()
    rows = _q(f"SELECT number, any_value(volume), any_value(name_ar), max(part) FROM t{f} GROUP BY 1 ORDER BY 1", fp)
    if not rows:
        return f"No biography name matches {query!r}. Try hilya_search() to search the full text."
    pages = (len(rows) + PEOPLE_PAGE - 1) // PEOPLE_PAGE
    if pg > pages:
        raise ValueError(f"page {pg} is out of range: 1 to {pages}.")
    out = [f"{n}. {nm[:80]} (vol {v}, {k} parts)" for n, v, nm, k in rows[(pg - 1) * PEOPLE_PAGE: pg * PEOPLE_PAGE]]
    more = f"\nMore: hilya_people(page={pg + 1}{', query=' + repr(query) if query else ''})" if pg < pages else ""
    return f"{NOTE}\n{LABEL}: {len(rows)} entries{' matching ' + repr(query) if query else ''}, page {pg}/{pages}. Open with hilya_read(person='N').\n" + "\n".join(out) + more


def _search_impl(query: str, limit: int) -> str:
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
    rows = _q(f"SELECT id, number, part, name_ar, nt FROM t WHERE {where} ORDER BY ({score}) DESC, number, part LIMIT ?", terms + pats + [int(limit)])
    note(*[r[0] for r in rows])
    if not rows:
        return f"No passage in the Hilya contains all of {terms}. Try fewer or other words, hilya_people(), or hilya_random()."
    out = []
    for _, n, p, name, nt in rows:
        t = re.sub(r"\s+", " ", nt)
        i = max(0, t.find(terms[0]) - 60)
        out.append(f"- Entry {n} ({name[:50]}), part {p}: …{t[i:i + SNIPPET_CHARS]}… -> hilya_read(person='{n}', part={p})")
    return f"{NOTE}\n{LABEL}, searched for {' '.join(terms)}:\n" + "\n".join(out)


def _random_impl() -> str:
    n, p = _q("SELECT number, part FROM t WHERE length(text_ar) > 800 ORDER BY random() LIMIT 1", [])[0]
    return _read_impl(str(n), p)


A = "Abu Nu'aym (أبو نعيم) Hilyat al-Awliya (حلية الأولياء): use when the user mentions Hilyat al-Awliya, the Hilya, Abu Nu'aym, or asks about early saints, sahaba, tabi'in, zuhhad (ascetics) or early Sufis and their sayings."
H = "Text is classical Arabic: explain or translate it in the user's language."


@mcp.tool(name="hilya_people", description=f"{A} Lists the biographies (tarajim, about 670 people in book order: sahaba, ahl al-suffa, tabi'in, later ascetics up to al-Junayd's era). Optional query filters names (Arabic, or famous names like Hasan al-Basri, Ibrahim ibn Adham, Fudayl, Bishr al-Hafi, Junayd); page (40 per page). Returns entry numbers for hilya_read. {H}")
@logged(log)
def hilya_people(query: str | None = None, page: int = 1) -> str:
    return _run(_people_impl, query, page)


@mcp.tool(name="hilya_read", description=f"{A} Reads one person's biography and sayings. person: entry number from hilya_people/hilya_search, or a name (Arabic or famous English name, e.g. 'Hasan al-Basri', 'Sufyan al-Thawri'); part 1.. (about 2000 characters each). Returns the text and a Next: call. {H}")
@logged(log)
def hilya_read(person: str, part: int = 1) -> str:
    return _run(_read_impl, person, part, True)


@mcp.tool(name="hilya_search", description=f"{A} Full-text search, e.g. what the early saints said about a topic (zuhd, patience, fear, weeping, the world, repentance, night prayer) or passages mentioning a person. query: Arabic keywords, common English topic words, or famous names (mapped to Arabic); all must match. limit 1-5. Returns snippets with the hilya_read call to open each. {H}")
@logged(log)
def hilya_search(query: str, limit: int = 3) -> str:
    return _run(_search_impl, query, limit)


@mcp.tool(name="hilya_random", description=f"{A} Use for a random passage, story or wisdom of the early saints from the Hilya. Returns one passage with its person and a Next: call. {H}")
@logged(log)
def hilya_random() -> str:
    return _run(_random_impl)


if __name__ == "__main__":
    try:
        _load()
    except BaseException as e:
        log.critical(f"startup failed: dataset {REPO} did not load: {type(e).__name__}: {e}")
        sys.exit(1)
    mcp.run(transport="stdio", show_banner=False)
