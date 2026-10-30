# Stack-chan MCP bridge

A Docker container that gives an M5Stack Stack-chan (running Xiaozhi firmware, connected to the xiaozhi.me cloud) extra tools over MCP: web search, news, time, prayer times, Quran/Hadith, Tanakh, Rumi, maps and more. It runs 16 local MCP servers exposing 42 tools. Replies are short, speakable plain text, and each tool call has an 8 second deadline. Errors fail loudly with a spoken message rather than silently falling back.

## How it works
`mcp_pipe.py` opens a WebSocket to your xiaozhi.me agent's MCP endpoint and starts every server listed in `mcp_config.json` as a child process (stdio). When you talk to Stack-chan, the Xiaozhi cloud can call these tools and read the result aloud. Tools are only used while someone is talking to the robot.

## Servers and tools (16 servers, 42 tools)
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

No API keys are needed: every server uses free, keyless sources.

## Prerequisites
- Docker with Docker Compose (Docker Desktop on macOS/Windows is fine). Any always-on computer works; the bridge only makes outgoing connections.
- A Stack-chan (or other device) running Xiaozhi firmware, bound to an agent in your account on xiaozhi.me.
- `git` and `curl`.

## Setup
1. Clone the repo:

       git clone https://github.com/mohammedterryjack/stackchan-bridge.git
       cd stackchan-bridge

2. Get your MCP endpoint: in xiaozhi.me, open the MCP endpoint settings for your agent and copy the endpoint address (a `wss://...` URL containing a token). Treat it like a password; anyone with it can attach tools to your robot.

3. Create `.env` (gitignored) and paste the endpoint:

       cp .env.example .env

   Then edit `.env` so it reads `MCP_ENDPOINT=<your endpoint>`. The variable must be called `MCP_ENDPOINT`.

4. Point compose at your `.env`: `compose.yaml` currently has `env_file:` set to the author's own path. Change it to `env_file: .env`.

5. Download the DuckDB httpfs extension (not committed; the Dockerfile bakes it into the image so the zines and Rumi servers can read Hugging Face datasets). Use `linux_arm64` on Apple Silicon / ARM hosts, `linux_amd64` on Intel/AMD:

       mkdir -p duckdb-ext
       curl -fL -o duckdb-ext/httpfs.duckdb_extension.gz https://extensions.duckdb.org/v1.3.2/linux_arm64/httpfs.duckdb_extension.gz

   The version must match the pinned `duckdb==1.3.2` in the Dockerfile.

6. Build and start only the bridge (the `wa-bridge` service in `compose.yaml` is a separate, optional service that needs files not in this repo; ignore it):

       docker compose up -d --build --no-deps stackchan-bridge

7. Check it connected:

       docker compose ps stackchan-bridge
       docker logs --tail 50 stackchan-bridge

   Logs show the WebSocket connection and each server starting. Don't paste logs publicly without checking them for your endpoint.

8. Ask Stack-chan something like "what time is it in Tokyo?" or "read Psalm 23".

## Testing a tool
`tools/sc_t.py` starts one server inside the running container and calls a tool the way Stack-chan would, printing the reply, size and time taken:

    docker exec -i stackchan-bridge python - hackernews list < tools/sc_t.py
    docker exec -i stackchan-bridge python - time clock_time '{"timezone": "Tokyo"}' < tools/sc_t.py

The first argument is a server name from `mcp_config.json`; the second is `list` or a tool name; the third is JSON arguments (use `list` to see each tool's parameter names). `tools/sc_chain.py` runs a JSON list of `[tool, args]` calls in one session.

## Adding, removing or configuring servers
- Servers are defined in `mcp_config.json` under `mcpServers`: a `command`, `args` and `env` for each. Delete an entry to remove a server.
- To add your own, write a stdio MCP server (see `tanakh_server.py` for a small FastMCP example), add it to the `COPY` line in the `Dockerfile`, and add an entry to `mcp_config.json`.
- Third-party servers (web search, YouTube, arXiv, Hacker News) run through `mcp_filter_proxy.py` with a JSON file in `filter_configs/` that limits which tools are exposed, rewrites descriptions, and caps result sizes.
- `MCP_TOOL_DEADLINE_S` (default 8) sets the per-call deadline.
- `mcp_config.json` and `filter_configs/` are baked into the image, so rebuild after any change:

      docker compose up -d --build --no-deps stackchan-bridge

## Privacy and security
- The container runs as a non-root user with a read-only filesystem, all capabilities dropped and only `/tmp` as scratch space. Nothing is written to disk; the zines and Rumi datasets (a few MB) are loaded into memory at start-up.
- Tool queries go to the public services listed below (e.g. search terms to DuckDuckGo, places to OpenStreetMap). Location tools never assume a default location; Stack-chan has to ask where you are.
- Keep `.env` private and never commit it (it's in `.gitignore`).

## Troubleshooting
- **"Please set the MCP_ENDPOINT environment variable"** in the logs: `.env` is missing, misnamed, or `env_file` in `compose.yaml` doesn't point at it.
- **Build fails at the DuckDB step:** `duckdb-ext/httpfs.duckdb_extension.gz` is missing or for the wrong architecture.
- **Changes don't show up:** rebuild; config is baked into the image.
- **Stack-chan doesn't use a tool:** ask more explicitly (e.g. "use the maps tool to find pharmacies near ..."), and check the tool works with `tools/sc_t.py`.
- **`web_read` says "empty body":** the page is built by JavaScript and has no text in its HTML.
- **A tool times out:** the upstream service was slower than the 8 second deadline; try again.
- Device features (reminders, head movement, LEDs) come from the robot's firmware, not this bridge.

## Data sources and credits
- Tanakh: [Sefaria](https://www.sefaria.org) API (texts carry their own licences; mostly CC/public domain English translations).
- Quran: [alquran.cloud](https://alquran.cloud) API. Hadith: [fawazahmed0/hadith-api](https://github.com/fawazahmed0/hadith-api) via jsDelivr.
- Rumi: [MTerryJack/rumi-mathnawi](https://huggingface.co/datasets/MTerryJack/rumi-mathnawi), R. A. Nicholson's English translation. Books V-VI (1934) are public domain in the UK/EU but may still be under copyright in the US; see the dataset card.
- Zines: [MTerryJack/hacker-zines](https://huggingface.co/datasets/MTerryJack/hacker-zines).
- Prayer times: [AlAdhan](https://aladhan.com) API.
- Maps: [OpenStreetMap](https://www.openstreetmap.org/copyright) data via Nominatim (please respect its usage policy) and routing via the public [OSRM](https://project-osrm.org) demo server. Map data (c) OpenStreetMap contributors, ODbL.
- Web search: DuckDuckGo. News: BBC, Google News, Simon Willison, Krebs on Security, Schneier on Security, LWN, 2600 RSS feeds. Darknet Diaries: darknetdiaries.com and its podcast feed.
- Third-party MCP servers: duckduckgo-mcp-server, mcp-hn, mcp-server-reddit, mcp-youtube-transcript, arxiv-mcp-server. `mcp_pipe.py` is adapted from the Xiaozhi mcp-calculator example pipe.
