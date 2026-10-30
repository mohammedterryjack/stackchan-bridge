# Stack-chan MCP bridge

Dockerised MCP bridge connecting an M5Stack Stack-chan (Xiaozhi cloud) to 12 local MCP servers exposing 28 tools. Replies are short, speakable plain text; each call has an 8s deadline.

## Servers and tools
- calculator: calculator
- web-search: web_search, web_read
- time: clock_time, convert_time
- hackernews: hn_top_stories, hn_search, hn_story, hn_user
- reddit: reddit_posts, reddit_post
- youtube: youtube_transcript, youtube_info
- youtube-search: youtube_search
- news: news_headlines, simonwillison_latest, security_news
- arxiv: arxiv_search, arxiv_read, arxiv_abstract
- zines: zine_search, zine_read
- prayer-times: prayer_times, next_prayer
- quran-hadith: read_quran, search_quran, get_hadith, random_hadith

## Setup
1. `cp .env.example .env` and set `STACKCHAN_MCP_ENDPOINT` (point `env_file` in compose.yaml at it).
2. Provide `duckdb-ext/httpfs.duckdb_extension.gz` (not committed; needed by the Dockerfile).
3. `docker compose up -d --build --no-deps stackchan-bridge`

Config changes (mcp_config.json, filter_configs/) are baked into the image, so rebuild after edits.

## Testing
Run a tool as Stack-chan would:

    docker exec -i stackchan-bridge python - hackernews list < tools/sc_t.py
    docker exec -i stackchan-bridge python - hackernews hn_top_stories '{}' < tools/sc_t.py

`tools/sc_chain.py` runs a JSON list of [tool, args] calls in one session.
