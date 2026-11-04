"""Ethiopian canon MCP: the books of the Ethiopian Orthodox Tewahedo canon that are not in the Tanakh or gospel tools.
One row per verse from hf://datasets/MTerryJack/ethiopian-canon (DuckDB in memory). Sources per row: R. H. Charles 1917
(Enoch, Jubilees, PD), World English Bible (deuterocanon, Acts-Revelation, PD), and Beta masaheft Ge'ez texts
(CC BY-SA 4.0): 1-3 Meqabyan, 4 Baruch, Canons of Clement (untranslated Ge'ez). Tools: ethiopian_books, _read, _search, _random."""

from fastmcp import FastMCP

from verse_corpus import Corpus

BOOKS = ["enoch", "jubilees", "tobit", "judith", "esther_additions", "wisdomofsolomon", "sirach", "baruch", "1esdras", "2esdras",
         "prayerofmanasseh", "psalm151", "azariah", "susanna", "bel", "4baruch", "1meqabyan", "2meqabyan", "3meqabyan", "clement",
         "sinodos", "didascalia", "testament1", "testament2", "magall", "qalementos1", "qalementos2", "qalementos3",
         "apostolic_constitutions", "apostolic_canons", "gessew", "sinodos_arabic", "sinodos_saidic", "mingana_apocpeter",
         "acts", "romans", "revelation"]
ALIASES = {"henok": "enoch", "1enoch": "enoch", "kufale": "jubilees", "wisdom": "wisdomofsolomon", "ecclesiasticus": "sirach",
           "ben sira": "sirach", "4 ezra": "2esdras", "ezra sutuel": "2esdras", "ezra kali": "1esdras", "3 ezra": "1esdras",
           "letter of jeremiah": "baruch", "song of the three": "azariah", "paralipomena of jeremiah": "4baruch",
           "rest of the words of baruch": "4baruch", "esther": "esther_additions",
           "apocalypse": "revelation", "manasseh": "prayerofmanasseh",
           "qalementos": "qalementos1", "qalementos 1": "qalementos1", "qalementos 2": "qalementos2", "qalementos 3": "qalementos3", "ethiopic qalementos": "qalementos1", "qalementos fr": "qalementos1", "magall": "magall", "book of clement": "magall", "kitab al-magall": "magall", "book of the rolls": "magall", "sinodos of clement": "clement", "canons of clement": "clement", "ethiopic clement": "clement",
           "sinodos": "sinodos", "synodos": "sinodos", "statutes of the apostles": "sinodos", "sirate tsion": "sinodos",
           "didesqelya": "didascalia", "ethiopic didascalia": "didascalia", "didascalia apostolorum": "didascalia",
           "kidan": "testament1", "mashafa kidan": "testament1", "meshafe kidan": "testament1", "book of the covenant": "testament1",
           "testament of our lord": "testament1", "testamentum domini": "testament1", "kidan 2": "testament2", "book of the covenant 2": "testament2",
           "apostolic constitutions": "apostolic_constitutions", "constitutions of the apostles": "apostolic_constitutions", "apostolic canons": "apostolic_canons", "85 apostolic canons": "apostolic_canons", "canons of the apostles": "apostolic_canons",
           "gessew": "gessew", "gitsew": "gessew", "gesew": "gessew", "ethiopic apostolic canons": "gessew", "schodde": "gessew", "arabic sinodos": "sinodos_arabic", "sinodos arabic": "sinodos_arabic", "arabic statutes": "sinodos_arabic", "saidic sinodos": "sinodos_saidic", "sahidic sinodos": "sinodos_saidic", "sinodos saidic": "sinodos_saidic", "coptic statutes": "sinodos_saidic", "kanons of the apostles": "sinodos_saidic",
           "mingana": "mingana_apocpeter", "apocalypse of peter": "mingana_apocpeter", "arabic book of clement": "mingana_apocpeter", "qalementos arabic": "mingana_apocpeter", "arabic apocalypse of peter": "mingana_apocpeter",
           "meqabyan": "1meqabyan", "ethiopian maccabees": "1meqabyan"}
NAMES = ("1 Enoch, Jubilees, 1-3 Meqabyan (Ethiopian Maccabees, Ge'ez), 4 Baruch (Ge'ez), Canons of Clement (Ge'ez), Sinodos (Statutes of the Apostles, English), Didascalia (Didesqelya, English), Testament of Our Lord 1-2 (basis of the Mashafa Kidan, English), Kitab al-Magall (Arabic Qalementos, English), Qalementos 1-3 (Ethiopic Clement, French), Gessew (Ethiopic Apostolic Canons, 56, English), Apostolic Constitutions and 85 Apostolic Canons (Greek recension, English), Sinodos Arabic and Sahidic recensions (English), Apocalypse of Peter / Arabic Book of Clement (Mingana, English), Tobit, Judith, Additions to Esther, Wisdom, Sirach, "
         "Baruch, 1 Esdras, 2 Esdras (4 Ezra), Prayer of Manasseh, Psalm 151, Prayer of Azariah, Susanna, Bel and the Dragon, "
         "and Acts to Revelation")
c = Corpus("ETHIOPIAN", "MTerryJack/ethiopian-canon", "ethiopian", BOOKS, ALIASES)
mcp = FastMCP("EthiopianCanon")
c.register(mcp, {
    "books": f"Use when the user asks which Ethiopian Bible (Ethiopian Orthodox canon) books are available. Lists {NAMES} with chapter and verse counts. 1-3 Meqabyan, 4 Baruch and the Canons of Clement are Ge'ez (Classical Ethiopic) source text, not English: translate or explain them for the user. Sinodos = Horner's 1904 English of the Ethiopic Statutes (Sirate Tsion and Te'ezaz only); Didascalia = Harden's 1920 English of the Ethiopic; Testament of Our Lord = Cooper & Maclean's 1902 English of the Syriac Testamentum Domini, the source of the Mashafa Kidan (the Ethiopic Kidan differs); Kitab al-Magall = Gibson's 1901 English of the Arabic Clement from which the Ethiopic Qalementos was made. Qalementos 1-3 = Grébaut's French translation of the Ethiopic Qalementos (books 1-3 only; book 3 partial): it is FRENCH, so translate it for the user. These five are cleaned OCR and may have small errors. Gessew = Schodde's 1885 English of Fell's Ge'ez Gessew/Gitsew (56 Ethiopic Apostolic Canons; not the 81-canon Abtelis); Apostolic Constitutions / Apostolic Canons = ANF 7 English of the Greek (the Greek parallel of Gessew/Abtelis, not the Ethiopic); sinodos_arabic / sinodos_saidic = Horner's 1904 English of the Arabic and Sahidic Statutes. mingana_apocpeter = Mingana's 1931 English of the Arabic Apocalypse of Peter (Book of Clement, the Arabic family behind Qalementos), Parts 1-3 only (Parts 4-5 absent in the Arabic MS; some passages are Mingana's summaries); public domain in UK/EU. Not available: Abtelis (81 canons; the Greek 85 Apostolic Canons are the nearest parallel), Qalementos books 4-7, and the Ethiopic texts of the Sinodos, Kidan, Qalementos and Didascalia.",
    "read": f"Use when the user asks to read or quote a passage from {NAMES}, e.g. Enoch 1, Jubilees 2:1, Romans 8:28 or Revelation 21. book name; chapter; optional verse_start and verse_end (up to 8 verses). Returns the verses, a reference and a Next: call. Meqabyan, 4 Baruch and Clement come back in Ge'ez and Qalementos in French: translate or explain them in the user's language.",
    "search": f"Use when the user asks what the Book of Enoch, Jubilees, Meqabyan, the Sinodos, the Didascalia, the apocrypha or the New Testament epistles say about a topic (e.g. Watchers, angels, giants, the calendar, wisdom, faith, love). query: English words (or Ge'ez words for the Ge'ez books: Meqabyan, 4 Baruch, Clement; French words for Qalementos 1-3, e.g. Pierre, Adam), all must match; optional book; limit 1-5. Returns references with ethiopian_read calls. Covers {NAMES}.",
    "random": f"Use when the user asks for a random verse or reading from the Ethiopian Bible, e.g. from Enoch, Jubilees, Sirach or the epistles. Optional book. Returns one verse with its reference (Ge'ez and French verses must be translated or explained). Covers {NAMES}.",
})

if __name__ == "__main__":
    c.main(mcp)
