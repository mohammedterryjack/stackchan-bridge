"""Shared bookmarks for Stack-chan's book tools: each *_read tool records where the user stopped and the exact read call
for the next passage; mcp_pipe.py serves bookmark_continue / bookmark_list / bookmark_set / bookmark_clear.

State is one small JSON file (BOOKMARKS_FILE, on the named Docker volume mounted at /data/bookmarks) shared by separate
server processes: every change takes an exclusive flock on <file>.lock and is written atomically (temp file, fsync,
os.replace). No fallbacks: a missing BOOKMARKS_FILE or an unwritable file raises."""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
import time
from contextlib import contextmanager


def _ints(s: str) -> list[int]:
    return [int(x) for x in re.findall(r"\d+", s)]


def _nums(*names: str, need: int = 1, defaults: dict | None = None, as_str: tuple = ()):
    """Parser for 'N', 'N part P', 'N:P' positions -> read-tool args (later numbers optional)."""
    def parse(pos: str) -> dict:
        n = _ints(pos)
        if len(n) < need:
            raise ValueError(f"position needs {need} number(s): {', '.join(names[:need])}")
        out = dict(defaults or {})
        for k, v in zip(names, n):
            out[k] = str(v) if k in as_str else v
        return out
    return parse


def _verse_ref(book_arg: str, chap_arg: str, verse_arg: str):
    def parse(pos: str) -> dict:
        m = re.fullmatch(r"\s*(.*?\S)\s+(\d+)(?:\s*[:. ]\s*(\d+))?\s*", pos)
        if not m:
            raise ValueError("position must look like 'Genesis 3' or 'Genesis 3:4'")
        out = {book_arg: m.group(1), chap_arg: int(m.group(2))}
        if m.group(3):
            out[verse_arg] = int(m.group(3))
        return out
    return parse


def _quran(pos: str) -> dict:
    m = re.fullmatch(r"\s*(\d{1,3})(?:\s*[:. ]\s*(\d{1,3}))?\s*", pos)
    if m:
        return {"surah": m.group(1), "ayah": int(m.group(2) or 1)}
    m = re.fullmatch(r"\s*(.*?[A-Za-z].*?)(?:\s+(\d{1,3}))?\s*", pos)
    if not m:
        raise ValueError("position must look like '2:255' or 'Al-Kahf 10'")
    return {"surah": m.group(1), "ayah": int(m.group(2) or 1)}


def _hadith(pos: str) -> dict:
    m = re.fullmatch(r"\s*([A-Za-z' -]+?)\s+(\d+)\s*", pos)
    if not m:
        raise ValueError("position must look like 'nawawi 5' or 'bukhari 12'")
    return {"collection": m.group(1), "number": int(m.group(2))}


def _zine(pos: str) -> dict:
    p = pos.strip()
    if not re.fullmatch(r"[a-z0-9-]+", p):
        raise ValueError("position must be a zine id like phrack-69-07-003 (from zine_search)")
    return {"id": p}


# key: (spoken label, mcp_config server, read tool, aliases, position parser, position help)
BOOKS: dict[str, tuple] = {
    "quran": ("the Quran", "quran-hadith", "quran_read", ("koran", "qur'an", "quran"), _quran, "surah:ayah, e.g. 2:255"),
    "hadith": ("Hadith collections", "quran-hadith", "hadith_get", ("hadith", "nawawi", "forty hadith", "sahih"), _hadith, "collection number, e.g. nawawi 5"),
    "tanakh": ("the Tanakh", "tanakh", "tanakh_read", ("torah", "hebrew bible", "old testament", "tanach"), _verse_ref("book", "chapter", "verse"), "book chapter:verse, e.g. Genesis 3:1"),
    "gospel": ("the Gospels", "gospel", "gospel_read", ("gospels", "gospel", "injil", "barnabas", "new testament gospels"), _verse_ref("book", "chapter", "verse_start"), "book chapter:verse, e.g. John 3:16"),
    "ethiopian": ("the Ethiopian canon", "ethiopian-canon", "ethiopian_read", ("ethiopian canon", "enoch", "jubilees", "meqabyan"), _verse_ref("book", "chapter", "verse_start"), "book chapter:verse, e.g. Enoch 1:1"),
    "rumi": ("Rumi's Mathnawi", "rumi", "rumi_read", ("mathnawi", "masnavi", "rumi"), _nums("book", "verse", defaults={"verse": 1}), "book:verse, e.g. 1:100"),
    "aesop": ("Aesop's Fables", "aesop", "aesop_read", ("aesop", "fables", "aesop's fables"), _nums("fable_id"), "fable number, e.g. 12"),
    "bahjat": ("Bahjat al-Nufus (Ibn Abi Jamrah)", "hadith-commentary", "bahjat_read", ("bahja", "bahjat al-nufus", "ibn abi jamrah", "hadith commentary"), _nums("hadith_number", "part", defaults={"part": 1}), "hadith number and part, e.g. 5 part 2"),
    "dreams": ("dreams of the Prophet", "prophet-dreams", "dreams_read", ("dreams", "prophet dreams", "mara'i", "marai"), _nums("dream_number", "part", defaults={"part": 1}), "dream number and part, e.g. 12 part 1"),
    "ghazali": ("al-Ghazali's Ihya Ulum al-Din", "sufi-classics", "ghazali_read", ("ihya", "ihya ulum al-din", "ghazali", "al-ghazali"), _nums("chapter", "part", defaults={"part": 1}), "chapter and part, e.g. 3 part 1"),
    "ibnarabi": ("Ibn Arabi's al-Futuhat al-Makkiyya", "sufi-classics", "ibnarabi_read", ("futuhat", "futuhat al-makkiyya", "ibn arabi", "ibn al-arabi"), _nums("chapter", "part", defaults={"part": 1}), "chapter and part, e.g. 3 part 1"),
    "jilani": ("al-Jilani's al-Ghunya", "sufi-classics", "jilani_read", ("ghunya", "ghunyat", "jilani", "abd al-qadir"), _nums("chapter", "part", defaults={"part": 1}), "chapter and part, e.g. 3 part 1"),
    "hilya": ("Hilyat al-Awliya", "hilyat-al-awliya", "hilya_read", ("hilyat al-awliya", "hilya", "abu nuaym", "abu nu'aym"), _nums("person", "part", defaults={"part": 1}, as_str=("person",)), "entry and part, e.g. 5 part 2"),
    "sulami_women": ("al-Sulami's Dhikr al-Niswa", "female-awliya", "sulami_women_read", ("sulami", "dhikr al-niswa", "sulami women"), _nums("person", "part", defaults={"part": 1}, as_str=("person",)), "entry and part, e.g. 1 part 1"),
    "safwa_women": ("Sifat al-Safwa women", "female-awliya", "safwa_women_read", ("safwa", "sifat al-safwa", "ibn al-jawzi", "safwa women"), _nums("person", "part", defaults={"part": 1}, as_str=("person",)), "entry and part, e.g. 1 part 1"),
    "zines": ("hacker zines", "zines", "zine_read", ("zine", "zines", "phrack", "pocorgtfo", "tmpout"), _zine, "a zine id, e.g. phrack-69-07-003"),
}

TOOL_SPECS = [
    {"name": "bookmark_continue", "description": "Continue reading a book where we left off ('carry on with the Quran'); no book = the last one read.",
     "inputSchema": {"type": "object", "properties": {"book": {"type": "string"}}}},
    {"name": "bookmark_list", "description": "Which books have a bookmark and where we left off in each.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "bookmark_set", "description": "Move a book's bookmark to a place Mo names, e.g. Quran 2:255 or Rumi 1:100.",
     "inputSchema": {"type": "object", "properties": {"book": {"type": "string"}, "position": {"type": "string"}}, "required": ["book", "position"]}},
    {"name": "bookmark_clear", "description": "Forget a book's bookmark, or all of them.",
     "inputSchema": {"type": "object", "properties": {"book": {"type": "string"}}, "required": ["book"]}},
]
PIPE_TOOLS = {t["name"] for t in TOOL_SPECS}


def _path() -> str:
    p = os.environ.get("BOOKMARKS_FILE", "").strip()
    if not p:
        raise RuntimeError("BOOKMARKS_FILE is not set; bookmarks need the /data/bookmarks volume")
    d = os.path.dirname(p)
    if not os.path.isdir(d):
        raise RuntimeError(f"bookmark directory {d} does not exist (is the bookmarks volume mounted?)")
    return p


@contextmanager
def _locked():
    p = _path()
    with open(p + ".lock", "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            yield p
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def _read(p: str) -> dict:
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise RuntimeError(f"bookmark file {p} is not a JSON object")
    return data


def _write(p: str, data: dict) -> None:
    d = os.path.dirname(p)
    fd, tmp = tempfile.mkstemp(prefix=".bookmarks-", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    dfd = os.open(d, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def check_writable() -> str:
    """Startup check for mcp_pipe: the bookmark file can be locked, read and written."""
    with _locked() as p:
        _write(p, _read(p))
    return p


def load() -> dict:
    with _locked() as p:
        return _read(p)


def record(key: str, at: str, next_args: dict | None) -> None:
    """Called by a book server after a read: 'at' is the spoken place just read; next_args the read-tool args for the
    passage after it (None when the book is finished)."""
    if key not in BOOKS:
        raise KeyError(f"unknown bookmark book {key!r}")
    with _locked() as p:
        data = _read(p)
        data[key] = {"at": at, "next": next_args, "updated": time.time()}
        _write(p, data)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower().replace("al-", "").replace("the ", ""))


def resolve(book: str) -> str:
    q = _norm(book)
    if not q:
        raise ValueError("book is required")
    for k, (label, _, _, aliases, _, _) in BOOKS.items():
        if q in {_norm(k), _norm(label)} | {_norm(a) for a in aliases}:
            return k
    hits = [k for k, (label, _, _, aliases, _, _) in BOOKS.items()
            if any(q in _norm(x) or _norm(x) in q for x in (k, label, *aliases) if len(_norm(x)) >= 4)]
    if len(hits) == 1:
        return hits[0]
    raise ValueError(f"unknown book {book!r}; books with bookmarks support: "
                     + ", ".join(BOOKS[k][0] for k in BOOKS))


def _ago(t: float) -> str:
    s = max(0, time.time() - float(t))
    if s < 3600:
        return f"{int(s // 60)} min ago"
    if s < 86400:
        return f"{int(s // 3600)} h ago"
    return f"{int(s // 86400)} days ago"


def list_text() -> str:
    data = load()
    rows = sorted(((k, v) for k, v in data.items() if k in BOOKS), key=lambda kv: -kv[1]["updated"])
    if not rows:
        return "No bookmarks yet. Read any book (Quran, Hilya, Rumi, Ihya...) and the place is saved automatically."
    out = []
    for k, v in rows:
        end = " (finished)" if v["next"] is None else ""
        where = f"set to start at {v['at']}" if v.get("set") else f"read up to {v['at']}"
        out.append(f"{BOOKS[k][0]}: {where}{end}, {_ago(v['updated'])}")
    return "Bookmarks: " + "; ".join(out) + ". Say which book to continue."


def set_text(book: str, position: str) -> str:
    k = resolve(book)
    label, _, tool, _, parse, helptext = BOOKS[k]
    if not (position or "").strip():
        raise ValueError(f"position is required for {label}: {helptext}")
    try:
        args = parse(position)
    except ValueError as e:
        raise ValueError(f"{e}. For {label} use {helptext}") from None
    with _locked() as p:
        data = _read(p)
        prev = (data.get(k) or {}).get("next") or {}
        if k == "dreams" and "source" in prev:
            args.setdefault("source", prev["source"])
        data[k] = {"at": position.strip(), "next": args, "updated": time.time(), "set": True}
        _write(p, data)
    return f"Bookmark for {label} set to {position.strip()}. Say continue reading {label} to start there."


def clear_text(book: str) -> str:
    if _norm(book) == "all":
        with _locked() as p:
            n = len(_read(p))
            _write(p, {})
        return f"Cleared all {n} bookmarks."
    k = resolve(book)
    with _locked() as p:
        data = _read(p)
        if k not in data:
            raise ValueError(f"there is no bookmark for {BOOKS[k][0]}")
        del data[k]
        _write(p, data)
    return f"Cleared the bookmark for {BOOKS[k][0]}."


def plan_continue(book: str | None) -> tuple[str, str, str, dict, str]:
    """-> (key, server, tool, args, header) for the read call that continues a book."""
    data = load()
    if book and book.strip():
        k = resolve(book)
        if k not in data:
            raise ValueError(f"there is no bookmark for {BOOKS[k][0]} yet; read it once (or use bookmark_set) first")
    else:
        mine = [(v["updated"], k) for k, v in data.items() if k in BOOKS]
        if not mine:
            raise ValueError("there are no bookmarks yet; read a book first")
        k = max(mine)[1]
    v = data[k]
    label, server, tool, _, _, _ = BOOKS[k]
    if v["next"] is None:
        raise ValueError(f"{label} is finished at {v['at']}; use bookmark_set to start somewhere else")
    head = f"Continuing {label} from {v['at']}." if v.get("set") else f"Continuing {label} after {v['at']}."
    return k, server, tool, dict(v["next"]), head


def handle(tool: str, args: dict) -> str:
    """bookmark_list / bookmark_set / bookmark_clear (bookmark_continue needs the caller to run the read call)."""
    a = args or {}
    if tool == "bookmark_list":
        return list_text()
    if tool == "bookmark_set":
        return set_text(str(a.get("book") or ""), str(a.get("position") or ""))
    if tool == "bookmark_clear":
        return clear_text(str(a.get("book") or ""))
    raise ValueError(f"unknown bookmark tool {tool!r}")
