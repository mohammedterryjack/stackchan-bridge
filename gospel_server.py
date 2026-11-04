"""Gospel MCP: Matthew, Mark, Luke, John (World English Bible, public domain) and the Gospel of Barnabas (Ragg 1907,
public domain), one row per verse from hf://datasets/MTerryJack/gospels (DuckDB in memory). Tools: gospel_books,
gospel_read, gospel_search, gospel_random."""

from fastmcp import FastMCP

from verse_corpus import Corpus

BOOKS = ["matthew", "mark", "luke", "john", "barnabas"]
ALIASES = {"mt": "matthew", "matt": "matthew", "mk": "mark", "lk": "luke", "jn": "john", "jhn": "john", "gospel of barnabas": "barnabas"}
SET = "the Gospels of Matthew, Mark, Luke and John (World English Bible) and the Gospel of Barnabas (Ragg 1907; verses are paragraphs, chapter 0 is the prologue)"
c = Corpus("GOSPEL", "MTerryJack/gospels", "gospel", BOOKS, ALIASES)
mcp = FastMCP("Gospel")
c.register(mcp, {
    "books": f"Use when the user asks which gospels are available or how many chapters a gospel has. Lists {SET} with chapter and verse counts.",
    "read": f"Use when the user asks to read or quote a gospel passage, e.g. John 3:16 or Matthew 5 (Sermon on the Mount), or the Gospel of Barnabas chapter 1. book: Matthew, Mark, Luke, John or Barnabas; chapter; optional verse_start and verse_end (up to 8 verses). Returns the verses, a reference and a Next: call. Covers {SET}.",
    "search": f"Use when the user asks what the gospels or Jesus say about a topic, or where a phrase appears in Matthew, Mark, Luke, John or Barnabas. query: English words, all must match; optional book; limit 1-5. Returns references with gospel_read calls.",
    "random": f"Use when the user asks for a random gospel verse, a verse of the day or a reading from Matthew, Mark, Luke, John or the Gospel of Barnabas. Optional book. Returns one verse with its reference.",
})

if __name__ == "__main__":
    c.main(mcp)
