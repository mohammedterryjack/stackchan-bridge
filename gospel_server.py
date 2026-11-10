"""Gospel MCP: Matthew, Mark, Luke, John (World English Bible, public domain) and the Gospel of Barnabas (Ragg 1907,
public domain), one row per verse from hf://datasets/MTerryJack/gospels (read on demand). Tools: gospel_read,
gospel_search."""

from app import mcp
from verse_corpus import Corpus

BOOKS = ["matthew", "mark", "luke", "john", "barnabas"]
ALIASES = {"mt": "matthew", "matt": "matthew", "mk": "mark", "lk": "luke", "jn": "john", "jhn": "john", "gospel of barnabas": "barnabas"}
SET = "the Gospels of Matthew, Mark, Luke and John (World English Bible) and the Gospel of Barnabas (Ragg 1907; verses are paragraphs, chapter 0 is the prologue)"
c = Corpus("GOSPEL", "MTerryJack/gospels", "gospel", BOOKS, ALIASES)
c.register(mcp, {
    "read": "Gospels (Matthew, Mark, Luke, John, Barnabas): read a passage by book, chapter and verses (e.g. John 3:16), or a random verse with no chapter.",
    "search": "Gospels: find what Jesus or the gospels say about a topic, optionally in one book; with no query, list the books.",
})
