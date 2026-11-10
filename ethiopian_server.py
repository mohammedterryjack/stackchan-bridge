"""Ethiopian canon MCP: the books of the Ethiopian Orthodox Tewahedo canon that are not in the Tanakh or gospel tools.
One row per verse from hf://datasets/MTerryJack/ethiopian-canon (read on demand). Sources per row: R. H. Charles 1917
(Enoch, Jubilees, PD), World English Bible (deuterocanon, Acts-Revelation, PD), and Beta masaheft Ge'ez texts
(CC BY-SA 4.0): 1-3 Meqabyan, 4 Baruch, Canons of Clement (untranslated Ge'ez). Tools: ethiopian_books, _read, _search, _random."""

from app import mcp
from verse_corpus import Corpus

BOOKS = ["enoch", "jubilees", "tobit", "judith", "esther_additions", "wisdomofsolomon", "sirach", "baruch", "letterofjeremiah", "1esdras", "2esdras",
         "prayerofmanasseh", "psalm151", "azariah", "susanna", "bel", "4baruch", "1meqabyan", "2meqabyan", "3meqabyan", "clement",
         "qalementos_am", "didesqelya_am", "sinodos", "testament1", "testament2",
         "apostolic_constitutions", "apostolic_canons", "gessew", "sinodos_arabic", "sinodos_saidic",
         "acts", "romans", "revelation"]
ALIASES = {"henok": "enoch", "1enoch": "enoch", "mashafa henok": "enoch", "kufale": "jubilees", "wisdom": "wisdomofsolomon", "ecclesiasticus": "sirach",
           "ben sira": "sirach", "4 ezra": "2esdras", "ezra sutuel": "2esdras", "ezra kali": "1esdras", "ezra kale": "1esdras", "3 ezra": "1esdras",
           "letter of jeremiah": "letterofjeremiah", "epistle of jeremiah": "letterofjeremiah", "terefe ermias": "letterofjeremiah", "rest of jeremiah": "letterofjeremiah",
           "song of the three": "azariah", "paralipomena of jeremiah": "4baruch", "rest of the words of baruch": "4baruch", "terefe baruch": "4baruch",
           "esther": "esther_additions", "yodit": "judith", "tobiya": "tobit",
           "apocalypse": "revelation", "manasseh": "prayerofmanasseh",
           "qalementos": "qalementos_am", "qalementos amharic": "qalementos_am", "mashafa qalementos": "qalementos_am", "ቀሌምንጦስ": "qalementos_am", "መጽሐፈ ቀሌምንጦስ": "qalementos_am",
           "book of clement": "qalementos_am", "apocalypse of peter": "qalementos_am", "ethiopic qalementos": "qalementos_am",
           "sinodos of clement": "clement", "canons of clement": "clement", "ethiopic clement": "clement",
           "sinodos": "sinodos", "synodos": "sinodos", "statutes of the apostles": "sinodos", "sirate tsion": "sinodos",
           "didesqelya": "didesqelya_am", "didascalia": "didesqelya_am", "ethiopic didascalia": "didesqelya_am", "didascalia apostolorum": "didesqelya_am", "ዲድስቅልያ": "didesqelya_am", "መጽሐፈ ዲድስቅልያ": "didesqelya_am",
           "kidan": "testament1", "mashafa kidan": "testament1", "meshafe kidan": "testament1", "book of the covenant": "testament1",
           "testament of our lord": "testament1", "testamentum domini": "testament1", "kidan 2": "testament2", "book of the covenant 2": "testament2",
           "apostolic constitutions": "apostolic_constitutions", "constitutions of the apostles": "apostolic_constitutions", "apostolic canons": "apostolic_canons", "85 apostolic canons": "apostolic_canons", "canons of the apostles": "apostolic_canons", "abtelis": "apostolic_canons",
           "gessew": "gessew", "gitsew": "gessew", "gesew": "gessew", "ethiopic apostolic canons": "gessew", "schodde": "gessew", "arabic sinodos": "sinodos_arabic", "sinodos arabic": "sinodos_arabic", "arabic statutes": "sinodos_arabic", "saidic sinodos": "sinodos_saidic", "sahidic sinodos": "sinodos_saidic", "sinodos saidic": "sinodos_saidic", "coptic statutes": "sinodos_saidic", "kanons of the apostles": "sinodos_saidic",
           "meqabyan": "1meqabyan", "ethiopian maccabees": "1meqabyan"}
NAMES = ("GE'EZ source text: 1 Enoch, Jubilees, Sirach, Wisdom, Baruch, 4 Baruch, 1-3 Meqabyan, Canons of Clement, Acts to Revelation; "
         "AMHARIC (EOTC) text: Qalementos, Didesqelya, Tobit, Judith, 1 Esdras (Ezra Kale), 2 Esdras (Ezra Sutuel), Letter of Jeremiah, Prayer of Azariah, Susanna, Bel and the Dragon; "
         "ENGLISH only where no Ge'ez/Amharic is available: Sinodos (Horner), Testament of Our Lord 1-2 (Kidan stand-in), Gessew (Schodde), Apostolic Constitutions and 85 Apostolic Canons (Greek parallel, Abtelis stand-in), Sinodos Arabic and Sahidic recensions, Additions to Esther, Prayer of Manasseh, Psalm 151")
c = Corpus("ETHIOPIAN", "MTerryJack/ethiopian-canon", "ethiopian", BOOKS, ALIASES)
c.note = f"Languages: {NAMES}. Translate Ge'ez and Amharic text when speaking."
c.register(mcp, {
    "read": "Ethiopian Orthodox canon (Enoch, Jubilees, Meqabyan, Sirach, Qalementos, Sinodos and more; mostly Ge'ez or Amharic, translate when speaking): read by book and chapter, or a random verse with no chapter.",
    "search": "Ethiopian canon: find a topic (Ge'ez/Amharic words for those books, English for the English ones), optionally in one book; with no query, list the books and their languages.",
})
