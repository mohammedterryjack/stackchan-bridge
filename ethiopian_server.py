"""Ethiopian canon MCP: the books of the Ethiopian Orthodox Tewahedo canon that are not in the Tanakh or gospel tools.
One row per verse from hf://datasets/MTerryJack/ethiopian-canon (DuckDB in memory). Sources per row: R. H. Charles 1917
(Enoch, Jubilees, PD), World English Bible (deuterocanon, Acts-Revelation, PD), and Beta masaheft Ge'ez texts
(CC BY-SA 4.0): 1-3 Meqabyan, 4 Baruch, Canons of Clement (untranslated Ge'ez). Tools: ethiopian_books, _read, _search, _random."""

from fastmcp import FastMCP

from verse_corpus import Corpus

BOOKS = ["enoch", "jubilees", "tobit", "judith", "esther_additions", "wisdomofsolomon", "sirach", "baruch", "1esdras", "2esdras",
         "prayerofmanasseh", "psalm151", "azariah", "susanna", "bel", "4baruch", "1meqabyan", "2meqabyan", "3meqabyan", "clement",
         "acts", "romans", "revelation"]
ALIASES = {"henok": "enoch", "1enoch": "enoch", "kufale": "jubilees", "wisdom": "wisdomofsolomon", "ecclesiasticus": "sirach",
           "ben sira": "sirach", "4 ezra": "2esdras", "ezra sutuel": "2esdras", "ezra kali": "1esdras", "3 ezra": "1esdras",
           "letter of jeremiah": "baruch", "song of the three": "azariah", "paralipomena of jeremiah": "4baruch",
           "rest of the words of baruch": "4baruch", "esther": "esther_additions",
           "apocalypse": "revelation", "manasseh": "prayerofmanasseh",
           "qalementos": "clement", "sinodos of clement": "clement", "canons of clement": "clement", "ethiopic clement": "clement",
           "meqabyan": "1meqabyan", "ethiopian maccabees": "1meqabyan"}
NAMES = ("1 Enoch, Jubilees, 1-3 Meqabyan (Ethiopian Maccabees, Ge'ez), 4 Baruch (Ge'ez), Canons of Clement (Ge'ez), Tobit, Judith, Additions to Esther, Wisdom, Sirach, "
         "Baruch, 1 Esdras, 2 Esdras (4 Ezra), Prayer of Manasseh, Psalm 151, Prayer of Azariah, Susanna, Bel and the Dragon, "
         "and Acts to Revelation")
c = Corpus("ETHIOPIAN", "MTerryJack/ethiopian-canon", "ethiopian", BOOKS, ALIASES)
mcp = FastMCP("EthiopianCanon")
c.register(mcp, {
    "books": f"Use when the user asks which Ethiopian Bible (Ethiopian Orthodox canon) books are available. Lists {NAMES} with chapter and verse counts. 1-3 Meqabyan, 4 Baruch and the Canons of Clement are Ge'ez (Classical Ethiopic) source text, not English: translate or explain them for the user. Not available: the full Sinodos, the Book of the Covenant (Mashafa Kidan), the full Qalementos and the Didascalia (no open machine-readable text).",
    "read": f"Use when the user asks to read or quote a passage from {NAMES}, e.g. Enoch 1, Jubilees 2:1, Romans 8:28 or Revelation 21. book name; chapter; optional verse_start and verse_end (up to 8 verses). Returns the verses, a reference and a Next: call. Meqabyan, 4 Baruch and Clement come back in Ge'ez: translate or explain them in the user's language.",
    "search": f"Use when the user asks what the Book of Enoch, Jubilees, Meqabyan, the apocrypha or the New Testament epistles say about a topic (e.g. Watchers, angels, giants, the calendar, wisdom, faith, love). query: English words (or Ge'ez words for the Ge'ez books: Meqabyan, 4 Baruch, Clement), all must match; optional book; limit 1-5. Returns references with ethiopian_read calls. Covers {NAMES}.",
    "random": f"Use when the user asks for a random verse or reading from the Ethiopian Bible, e.g. from Enoch, Jubilees, Sirach or the epistles. Optional book. Returns one verse with its reference (Ge'ez verses must be translated or explained). Covers {NAMES}.",
})

if __name__ == "__main__":
    c.main(mcp)
