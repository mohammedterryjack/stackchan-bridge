"""Ethiopian canon MCP: the books of the Ethiopian Orthodox Tewahedo canon that are not in the Tanakh or gospel tools.
One row per verse from hf://datasets/MTerryJack/ethiopian-canon (DuckDB in memory). Sources per row: R. H. Charles 1917
(Enoch, Jubilees, PD), World English Bible (deuterocanon, Acts-Revelation, PD), Wikisource Translation:1-3 Meqabyan
(CC BY-SA 4.0), Kraft & Purintun 4 Baruch (CCAT, non-commercial). Tools: ethiopian_books, _read, _search, _random."""

from fastmcp import FastMCP

from verse_corpus import Corpus

BOOKS = ["enoch", "jubilees", "tobit", "judith", "esther_additions", "wisdomofsolomon", "sirach", "baruch", "1esdras", "2esdras",
         "prayerofmanasseh", "psalm151", "azariah", "susanna", "bel", "4baruch", "1meqabyan", "2meqabyan", "3meqabyan",
         "acts", "romans", "revelation"]
ALIASES = {"henok": "enoch", "1enoch": "enoch", "kufale": "jubilees", "wisdom": "wisdomofsolomon", "ecclesiasticus": "sirach",
           "ben sira": "sirach", "4 ezra": "2esdras", "ezra sutuel": "2esdras", "ezra kali": "1esdras", "3 ezra": "1esdras",
           "letter of jeremiah": "baruch", "song of the three": "azariah", "paralipomena of jeremiah": "4baruch",
           "rest of the words of baruch": "4baruch", "esther": "esther_additions",
           "apocalypse": "revelation", "manasseh": "prayerofmanasseh"}
NAMES = ("1 Enoch, Jubilees, 1-3 Meqabyan (Ethiopian Maccabees), 4 Baruch, Tobit, Judith, Additions to Esther, Wisdom, Sirach, "
         "Baruch, 1 Esdras, 2 Esdras (4 Ezra), Prayer of Manasseh, Psalm 151, Prayer of Azariah, Susanna, Bel and the Dragon, "
         "and Acts to Revelation")
c = Corpus("ETHIOPIAN", "MTerryJack/ethiopian-canon", "ethiopian", BOOKS, ALIASES)
mcp = FastMCP("EthiopianCanon")
c.register(mcp, {
    "books": f"Use when the user asks which Ethiopian Bible (Ethiopian Orthodox canon) books are available. Lists {NAMES} with chapter and verse counts. Sinodos, the Book of the Covenant, Ethiopic Clement and Didascalia are not included (no public-domain English text).",
    "read": f"Use when the user asks to read or quote a passage from {NAMES}, e.g. Enoch 1, Jubilees 2:1, Romans 8:28 or Revelation 21. book name; chapter; optional verse_start and verse_end (up to 8 verses). Returns the verses, a reference and a Next: call.",
    "search": f"Use when the user asks what the Book of Enoch, Jubilees, Meqabyan, the apocrypha or the New Testament epistles say about a topic (e.g. Watchers, angels, giants, the calendar, wisdom, faith, love). query: English words, all must match; optional book; limit 1-5. Returns references with ethiopian_read calls. Covers {NAMES}.",
    "random": f"Use when the user asks for a random verse or reading from the Ethiopian Bible, e.g. from Enoch, Jubilees, Sirach or the epistles. Optional book. Returns one verse with its reference. Covers {NAMES}.",
})

if __name__ == "__main__":
    c.main(mcp)
