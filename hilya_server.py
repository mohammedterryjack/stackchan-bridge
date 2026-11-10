"""Hilyat al-Awliya MCP: Abu Nu'aym al-Isfahani's Hilyat al-Awliya wa Tabaqat al-Asfiya, original Arabic, from
hf://datasets/MTerryJack/hilyat-al-awliya (OpenITI-derived, CC BY-NC-SA 4.0; read on demand, nothing kept in RAM).
Tools: hilya_people, hilya_read, hilya_search. Each call logs one stderr line via mcp_tool_log;
an unreachable dataset fails the call aloud."""

from __future__ import annotations

import re

from app import mcp
from hf_data import HFTable

import bookmarks

from mcp_tool_log import get_logger, logged, note

REPO = "MTerryJack/hilyat-al-awliya"
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


def _check(q) -> None:
    rows, ents = q("SELECT count(*), count(DISTINCT number) FROM t")[0]
    if ents < MIN_ENTRIES:
        raise RuntimeError(f"{REPO} has only {ents} entries (expected >= {MIN_ENTRIES})")
    log.info(f"{REPO} rows={rows} entries={ents}")


_q = HFTable(REPO, "Hilyat al-Awliya", {"t": lambda src, cols: "SELECT id, volume, number, name_ar, section_ar, part, text_ar, "
             + NORM_SQL.format(c="text_ar") + " AS nt, " + NORM_SQL.format(c="name_ar") + " AS nn FROM " + src}, on_ready=_check).q


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
        return f"No passage in the Hilya contains all of {terms}. Try fewer or other words, hilya_people(), or hilya_read()."
    out = []
    for _, n, p, name, nt in rows:
        t = re.sub(r"\s+", " ", nt)
        i = max(0, t.find(terms[0]) - 60)
        out.append(f"- Entry {n} ({name[:50]}), part {p}: …{t[i:i + SNIPPET_CHARS]}… -> hilya_read(person='{n}', part={p})")
    return f"{NOTE}\n{LABEL}, searched for {' '.join(terms)}:\n" + "\n".join(out)


def _random_impl() -> str:
    n, p = _q("SELECT number, part FROM t WHERE length(text_ar) > 800 ORDER BY random() LIMIT 1", [])[0]
    return _read_impl(str(n), p)


A = "Abu Nu'aym's Hilyat al-Awliya (early saints and ascetics, Arabic text; translate when speaking):"


def _read_or_random(person: str | None, part: int) -> str:
    return _random_impl() if not person else _read_impl(person, part, True)


@mcp.tool(name="hilya_people", description=f"{A} list the people (about 670), optionally filtered by name, e.g. Hasan al-Basri.")
@logged(log)
def hilya_people(query: str | None = None, page: int = 1) -> str:
    return _people_impl(query, page)


@mcp.tool(name="hilya_read", description=f"{A} read a person's biography and sayings by entry number or name, in parts, or a random passage with no person.")
@logged(log)
def hilya_read(person: str | None = None, part: int = 1) -> str:
    return _read_or_random(person, part)


@mcp.tool(name="hilya_search", description=f"{A} find what the early saints said about a topic (zuhd, patience, repentance) or passages about a person.")
@logged(log)
def hilya_search(query: str, limit: int = 3) -> str:
    return _search_impl(query, limit)
