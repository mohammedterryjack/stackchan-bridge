# Stack-chan MCP bridge

Dockerised MCP bridge connecting an M5Stack Stack-chan (Xiaozhi cloud) to 16 local MCP servers exposing 42 tools. Replies are short, speakable plain text; each call has an 8s deadline.

## Servers and tools
- calculator: calculator
- web-search: web_search, web_read
- time: clock_time, convert_time (city or timezone required, no default)
- hackernews: hn_top_stories, hn_search, hn_story, hn_user
- reddit: reddit_posts, reddit_post
- youtube: youtube_transcript, youtube_info
- youtube-search: youtube_search
- news: news_headlines, simonwillison_latest, security_news
- arxiv: arxiv_search, arxiv_read, arxiv_abstract
- zines: zine_search, zine_read
- darknet-diaries: darknet_episodes, darknet_episode, darknet_transcript, darknet_find
- prayer-times: prayer_times, next_prayer, prayer_alarms (city and country required, no default)
- quran-hadith: read_quran, search_quran, get_hadith, random_hadith
- tanakh: tanakh_verse, tanakh_chapter, tanakh_search (Sefaria, English)
- rumi: rumi_read, rumi_search, rumi_random (Nicholson's English Mathnawi, Books 1-6, via HF dataset)
- maps: map_search, map_nearby, map_distance (OpenStreetMap, keyless)

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
