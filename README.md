# Stack-chan MCP bridge

A Docker container that gives an M5Stack Stack-chan (running Xiaozhi firmware, connected to the xiaozhi.me cloud) extra tools over MCP: web search, news, time, prayer times, Quran/Hadith, Tanakh, Rumi, Aesop, hadith commentary, dreams of the Prophet, Sufi classics (al-Ghazali, Ibn Arabi, al-Jilani), Hilyat al-Awliya, recipes, Japanese lessons in romaji, live learning (a local Hindsight memory), maps and more. It runs 26 local MCP servers exposing 77 tools. Replies are short, speakable plain text, and each tool call has an 8 second deadline. Errors fail loudly with a spoken message rather than silently falling back.

## How it works
`mcp_pipe.py` opens a WebSocket to your xiaozhi.me agent's MCP endpoint and starts every server listed in `mcp_config.json` as a child process (stdio). When you talk to Stack-chan, the Xiaozhi cloud can call these tools and read the result aloud. Tools are only used while someone is talking to the robot.

## Servers and tools (26 servers, 77 tools)
- calculator: calculator, more_text (pages through long replies), and the bookmark tools bookmark_continue, bookmark_list, bookmark_set, bookmark_clear (served by mcp_pipe.py on this connection; see Bookmarks below)

Reply cap: mcp_pipe.py caps every tool reply at ~800 chars (max 1000), splitting on sentence/verse boundaries with "Part N of M" notes; Stack-chan calls more_text(part=N) to continue.

- web-search: web_search, web_read (DuckDuckGo: 5 min cache, 2 s between searches; on a bot check or dropped connection it stops for 15 min and says 'DuckDuckGo is rate-limiting us, try again in N minutes' instead of retrying)
- time: time_now, time_convert (city or timezone required, no default)
- hackernews: hn_top_stories, hn_search, hn_story, hn_user
- youtube: youtube_transcript, youtube_info
- youtube-search: youtube_search
- news: news_headlines (topic, or source such as Simon Willison or Schneier)
- arxiv: arxiv_search, arxiv_read, arxiv_abstract
- zines: zine_search, zine_read
- prayer-times: prayer_times (today's reply also says which prayer is next and how long until it), prayer_alarms (city and country required, no default)
- quran-hadith: quran_read, quran_search, hadith_get (no number = random hadith)
- tanakh: tanakh_read (book, chapter, optional verse or range; no chapter = random verse), tanakh_search (Sefaria, English)
- rumi: rumi_read (no book = random passage), rumi_search (Nicholson's English Mathnawi, Books 1-6, via HF dataset)
- aesop: aesop_read (no fable = random), aesop_search (Townsend's Aesop's Fables, 313 fables, via HF dataset)
- hadith-commentary: bahjat_read (no number = random), bahjat_search (Ibn Abi Jamrah's Bahjat al-Nufus, classical Arabic, via HF dataset; search normalises alif, ya, ta marbuta and diacritics)
- prophet-dreams: dreams_read (no number = random), dreams_search (no query = list sources) (dreams others saw of the Prophet from al-Mara'i al-Hisan (Ibn Abi Jamrah), Sa'adat al-Darayn (al-Nabhani), al-Manamat (Ibn Abi al-Dunya) and al-Mustaghithin billah (Ibn Bashkuwal), classical Arabic, via HF dataset; dream_number is per source, and without `source` the read defaults to al-Mara'i)
- sufi-classics: ghazali_contents/read/search (al-Ghazali, Ihya Ulum al-Din), ibnarabi_contents/read/search (Ibn Arabi, al-Futuhat al-Makkiyya), jilani_contents/read/search (Abd al-Qadir al-Jilani, al-Ghunya li-Talibi Tariq al-Haqq); original Arabic via HF dataset, Stack-chan explains or translates
- hilyat-al-awliya: hilya_people, hilya_read (no person = random), hilya_search (Abu Nu'aym al-Isfahani, Hilyat al-Awliya wa Tabaqat al-Asfiya: 674 entries/biographies of sahaba, tabi'in and early ascetics; original Arabic via HF dataset, Stack-chan explains or translates; famous English names like Hasan al-Basri or Junayd are mapped to Arabic)
- female-awliya: sulami_women_people/read/search (al-Sulami, Dhikr al-Niswa: 82 women including Rabia al-Adawiyya as entry 1) and safwa_women_people/read/search (Ibn al-Jawzi, Sifat al-Safwa women: ~110 biographies); original Arabic via HF dataset MTerryJack/female-awliya (OpenITI, CC BY-NC-SA 4.0); short ~800-char parts for spoken audio
- habib-omar: habib_omar_latest, habib_omar_read (Habib Omar / Habib Umar bin Hafiz live updates from his official login-free sources, newest first and every item dated: Telegram channel t.me/HabibOmar via its public web preview (lessons, live streams, clips, several a day), official YouTube channel HabibOmarCom RSS, Arabic news from alhabibumar.com (Hijri dates converted to Gregorian with hijridate) and English answers by Sayyidi Habib Umar from muwasala.org (an archive, rarely updated); source=all merges them and fails loudly if any source breaks; live fetch only, 5-minute in-memory cache, no storage; short parts)
- recipes: recipe_suggest, recipe_search (dish name or ingredient), recipe_read, recipe_ingredients (live from TheMealDB free public API, test key 1; small in-memory cache; Stack-chan may adapt methods for the Tokit Omni Cook, whose own cookNjoy recipe API requires signed requests and is not used)
- japanese: japanese_word, japanese_phrase (random, by topic/level, or search with query), japanese_to_romaji (learn Japanese by voice: replies are ASCII romaji only, English first then 'In Japanese: ...', so the English TTS voice can read them; words live from the jisho.org API with a small in-memory cache; ~147k Tatoeba eng-jpn phrase pairs (CC BY 2.0 FR) with precomputed romaji via HF dataset MTerryJack/japanese-phrases, built by tools/build_japanese_phrases.py; romaji from fugashi + unidic-lite baked into the image)
- memory: learn_this (ingest: 'learn that', 'remember', 'note this', 'don't forget'), learn_recall (fetch: 'what have you learnt about', 'what do you know about', 'do you remember'), learn_status, learn_reflect, learn_reflection (live learning, Stack-chan's only real long-term memory: Stack-chan remembers facts Mo teaches her and recalls them later; see Memory below)
- gospel: gospel_read (no chapter = random verse), gospel_search (no query = list books) (Matthew, Mark, Luke, John from the World English Bible and the Gospel of Barnabas, Ragg 1907; one row per verse via HF dataset)
- ethiopian-canon: ethiopian_read (no chapter = random verse), ethiopian_search (no query = list books) (Ethiopian Orthodox canon books not in the Tanakh or gospel tools, in their source language where possible, which the AI translates or explains: Ge'ez 1 Enoch, Jubilees, Sirach, Wisdom, Baruch, 4 Baruch, 1-3 Meqabyan, Canons of Clement and Acts to Revelation; Amharic EOTC Qalementos, Didesqelya, Tobit, Judith, 1-2 Esdras (Ezra Kale/Sutuel), Letter of Jeremiah, Azariah, Susanna and Bel; English only where no Ge'ez/Amharic exists: Sinodos and its Arabic/Sahidic recensions (Horner), Testament of Our Lord (Kidan stand-in), Gessew (Schodde), Apostolic Constitutions and 85 Canons (Abtelis stand-in), Additions to Esther, Prayer of Manasseh, Psalm 151)
- maps: map_search, map_nearby, map_distance (OpenStreetMap, keyless)

Bookmarks: every book read tool saves where you stopped, one bookmark per book: quran_read (Quran), hadith_get (hadith collections), tanakh_read (Tanakh), gospel_read, ethiopian_read, rumi_read, aesop_read, bahjat_read, dreams_read, ghazali_read (Ihya), ibnarabi_read (Futuhat), jilani_read (Ghunya), hilya_read, sulami_women_read, safwa_women_read and zine_read. Random/search tools do not move a bookmark. Say "continue reading the Quran" (bookmark_continue reads the next passage through that book's live server), "what am I reading?" (bookmark_list), "set my Rumi bookmark to 1:100" (bookmark_set) or "forget my Hilya bookmark" (bookmark_clear). They live in /data/bookmarks/bookmarks.json on the named Docker volume `stackchan-bookmarks` (stackchan-bridge only), so they survive rebuilds and restarts; writes are atomic under a file lock (bookmarks.py), and the bridge refuses to start if the volume is not writable. `make test` uses a separate smoke-test.json on the same volume and restarts the container once to prove bookmarks persist.

Tool-list size: Xiaozhi caps the tool list it will accept (undocumented token limit), so descriptions are one or two sentences, random/list variants are folded into read/search (no args = random or list), and mcp_pipe strips titles, null defaults and Optional wrappers from schemas. After this the full list is ~30 KB.

Tool-list logging: mcp_pipe logs each `<< initialize` / `<< tools/list` from Xiaozhi and `>> tools/list reply tools=N bytes=B` per server, so `docker logs stackchan-bridge | grep 'tools/list'` shows what Xiaozhi was offered (about 70 KB for 106 tools across 28 connections). Xiaozhi documents a token cap on the tool list and a per-endpoint connection cap but publishes no numbers; the xiaozhi.me agent console shows them.

Memory (live learning): the compose service `hindsight` runs the official Hindsight image (ghcr.io/vectorize-io/hindsight:1.2.0, API only, no published ports) on the `stackchan` network; memory_server.py calls its REST API at http://hindsight:8888, bank `mo`. learn_this queues a retain: Hindsight extracts facts with the Mac's native Ollama (qwen2.5-coder:7b at num_ctx 8192, the model already resident, so nothing is evicted) and embeds/reranks them with the local models baked into the image (BAAI/bge-small-en-v1.5, hs-rerank-1; HF_HUB_OFFLINE=1). Learning takes ~15-30 s (longer while other Ollama clients are busy), so learn_this waits up to 6 s and otherwise says it is still learning; learn_status reports pending and failed learning. learn_recall needs no LLM (~0.1 s) and returns the top 3 facts. Memories live in embedded Postgres on the named volume `stackchan-hindsight-data`, so they survive rebuilds and restarts. No fallbacks: memory_server refuses to start if Hindsight is down, raises if Hindsight's LLM health check fails, and surfaces failed retains. `make up` starts hindsight and the bridge; `make test` uses the separate `stackchan-smoke` bank (wiped at the start), learns a fact, recalls it, then restarts both containers and recalls it again. After each learn Hindsight consolidates facts into higher-level observations in the background (another LLM call, ~20-70 s).

Memory viewer: the Hindsight Control Plane runs inside the `hindsight` container and is published on the Mac's loopback only (`127.0.0.1:9999`, never 0.0.0.0; the API port 8888 stays unpublished because the viewer talks to it inside the container). Open http://127.0.0.1:9999 on the Mac, pick the bank `mo` (Stack-chan's real memory; `stackchan-smoke` is the test bank), then open Memories to see the World/Experience facts and the graph view, or Entities for the entity graph. Direct link: http://127.0.0.1:9999/banks/mo?view=data. The viewer has edit and delete buttons; they change Stack-chan's memory for real.

Reflection: learn_reflect queues Hindsight's agentic reflect (it searches memories and observations, then reasons with the 7B), which takes about 25 s to a few minutes, and replies at once ("I'm thinking about that now; ask me for my reflection in a few minutes"). A detached worker (`memory_server.py --reflect-worker`, one per bank, flock-guarded) waits for pending learning/consolidation to settle, runs the reflect and stores the answer or the error in /data/bookmarks/reflections-<bank>.json on the `stackchan-bookmarks` volume (last 10 kept). learn_reflection gives the latest finished answer, "Still thinking about ..., N min so far", or raises the recorded error. A job interrupted by a restart is run again by the next worker. Hindsight's reflect limits are set for the local model: per-call LLM timeout 600 s (the 30 s default is what made the first attempts fail), wall timeout 1800 s, 5 iterations, 4500 context tokens (inside Ollama's 8192), 1024 completion tokens. `make test` starts a reflection on the smoke bank and checks the replies; `make test-reflect` (opt-in, minutes) waits for one to finish.

No API keys are needed: every server uses free, keyless sources.

## Prerequisites
- Docker with Docker Compose v2 (Docker Desktop on macOS/Windows is fine), plus `git` and `make`. Any always-on computer works; the bridge only makes outgoing connections.
- A Stack-chan (or other device) running Xiaozhi firmware, bound to an agent in your account on xiaozhi.me.

## Quick start

    git clone https://github.com/mohammedterryjack/stackchan-bridge.git
    cd stackchan-bridge
    make setup

The first `make setup` creates `.env` from `.env.example` and stops with a message. Open `.env` and set `MCP_ENDPOINT` to your endpoint: in xiaozhi.me, open the MCP endpoint settings for your agent and copy the `wss://...` address. Treat it like a password; anyone with it can attach tools to your robot. `.env` is gitignored. Then run `make setup` again.

`make setup` checks Docker and Compose are installed and running, checks `.env` has a real `MCP_ENDPOINT` (it fails loudly otherwise; there are no defaults), then builds and starts only the `stackchan-bridge` container. The DuckDB httpfs extension the zines, Rumi, Aesop, hadith-commentary, prophet-dreams, sufi-classics, hilyat-al-awliya and female-awliya servers need is downloaded inside the Docker build for your machine's architecture (arm64 or amd64), so there is no manual download.

| Command | What it does |
|---|---|
| `make setup` / `make run` | check prerequisites and `.env`, build and start the bridge |
| `make test` | call one tool on every server inside the running bridge (list in `tools/smoke.txt`); prints PASS/FAIL, exits non-zero on any failure |
| `make test-web` | opt-in: exactly one DuckDuckGo web search (`tools/smoke-web.txt`); kept out of `make test` so test runs don't trip DuckDuckGo's bot check |
| `make test-reflect` | opt-in: one memory reflection run to completion on the smoke bank (minutes) |
| `make logs` | follow the bridge logs (don't paste them publicly without checking for your endpoint) |
| `make restart` / `make stop` | restart or stop the bridge |

To run a second copy alongside (e.g. a second Stack-chan agent), use another clone with its own `.env` and run `COMPOSE_PROJECT_NAME=bridge2 make setup CONTAINER=bridge2` (pass `CONTAINER=bridge2` to the other targets too).

Then ask Stack-chan something like "what time is it in Tokyo?" or "read Psalm 23".

## Testing a tool
`tools/sc_t.py` starts one server inside the running container and calls a tool the way Stack-chan would, printing the reply, size and time taken:

    docker exec -i stackchan-bridge python - hackernews list < tools/sc_t.py
    docker exec -i stackchan-bridge python - time time_now '{"timezone": "Tokyo"}' < tools/sc_t.py

The first argument is a server name from `mcp_config.json`; the second is `list` or a tool name; the third is JSON arguments (use `list` to see each tool's parameter names). `tools/sc_chain.py` runs a JSON list of `[tool, args]` calls in one session.

## Adding, removing or configuring servers
- Servers are defined in `mcp_config.json` under `mcpServers`: a `command`, `args` and `env` for each. Delete an entry to remove a server.
- To add your own, write a stdio MCP server (see `tanakh_server.py` for a small FastMCP example), add it to the `COPY` line in the `Dockerfile`, and add an entry to `mcp_config.json`.
- Third-party servers (web search, YouTube, arXiv, Hacker News) run through `mcp_filter_proxy.py` with a JSON file in `filter_configs/` that limits which tools are exposed, rewrites descriptions, and caps result sizes.
- `MCP_TOOL_DEADLINE_S` (default 8) sets the per-call deadline.
- `mcp_config.json` and `filter_configs/` are baked into the image, so rebuild after any change:

      make setup

## Privacy and security
- The container runs as a non-root user with a read-only filesystem, all capabilities dropped and only `/tmp` as scratch space. Nothing is written to disk; the zines, Rumi, Aesop, Bahjat al-Nufus and al-Mara'i datasets (a few MB) are loaded into memory at start-up.
- Tool queries go to the public services listed below (e.g. search terms to DuckDuckGo, places to OpenStreetMap). Location tools never assume a default location; Stack-chan has to ask where you are.
- Keep `.env` private and never commit it (it's in `.gitignore`).

## Troubleshooting
- **"Please set the MCP_ENDPOINT environment variable"** in the logs: `.env` in the repo folder is missing or the variable is misnamed; `make setup` checks this.
- **Build fails at the DuckDB step:** the build couldn't download the httpfs extension from extensions.duckdb.org; check internet access and retry.
- **Changes don't show up:** rebuild; config is baked into the image.
- **Stack-chan doesn't use a tool:** ask more explicitly (e.g. "use the maps tool to find pharmacies near ..."), and check the tool works with `tools/sc_t.py`.
- **`web_read` says "empty body":** the page is built by JavaScript and has no text in its HTML.
- **A tool times out:** the upstream service was slower than the 8 second deadline; try again.
- Device features (reminders, head movement, LEDs) come from the robot's firmware, not this bridge.

## Data sources and credits
- Tanakh: [Sefaria](https://www.sefaria.org) API (texts carry their own licences; mostly CC/public domain English translations).
- Quran: [alquran.cloud](https://alquran.cloud) API. Hadith: [fawazahmed0/hadith-api](https://github.com/fawazahmed0/hadith-api) via jsDelivr.
- Rumi: [MTerryJack/rumi-mathnawi](https://huggingface.co/datasets/MTerryJack/rumi-mathnawi), R. A. Nicholson's English translation. Books V-VI (1934) are public domain in the UK/EU but may still be under copyright in the US; see the dataset card.
- Aesop: [MTerryJack/aesop-fables](https://huggingface.co/datasets/MTerryJack/aesop-fables), George Fyler Townsend's translation from Project Gutenberg eBook #21 (public domain).
- Bahjat al-Nufus: [MTerryJack/bahjat-al-nufus](https://huggingface.co/datasets/MTerryJack/bahjat-al-nufus). Arabic only. The commentary is OCR of an old Egyptian print (archive.org BahjatNufus) and the hadith text comes from OpenITI (CC BY-NC-SA 4.0). The author died 695/699 AH (about 1296-1300 CE). The OCR is noisy and some hadith/commentary boundaries are low confidence; see the dataset card. Replies tell the AI to translate or explain the Arabic.
- Dreams of the Prophet: [MTerryJack/prophet-dreams](https://huggingface.co/datasets/MTerryJack/prophet-dreams), with a `source` column and a per-row `licence` column. All four works are public domain. al-Manamat is Shamela typed text (main text only). al-Mara'i al-Hisan is archive.org OCR of poor quality. Sa'adat al-Darayn is typed Shamela text with the copyist's notes removed. al-Mustaghithin is the read.shamela.ws main text without the editor's apparatus. Only accounts of the Prophet being seen in a dream are included.
- Sufi classics: [MTerryJack/sufi-classics](https://huggingface.co/datasets/MTerryJack/sufi-classics), derived from the [OpenITI](https://openiti.org) corpus (CC BY-NC-SA 4.0): Ihya (Shamela0009472), Futuhat (JK009237), Ghunya (Sham19Y0017817). Markup removed, ~2KB chunks; chapter numbers are sequential per book (1 = introduction), so Futuhat chapter N is not always bab N.
- Hilyat al-Awliya: [MTerryJack/hilyat-al-awliya](https://huggingface.co/datasets/MTerryJack/hilyat-al-awliya), derived from the [OpenITI](https://openiti.org) corpus (CC BY-NC-SA 4.0), JK000022 edition: 674 entries in 3,165 ~2KB rows with volume, entry number and person name; entry numbers are sequential (1 = introduction).
- Gospels: [MTerryJack/gospels](https://huggingface.co/datasets/MTerryJack/gospels): Matthew, Mark, Luke and John from the [World English Bible](https://ebible.org/engwebu/) (public domain); Gospel of Barnabas, Lonsdale and Laura Ragg's 1907 translation (public domain) via sacred-texts.com, one paragraph per verse.
- Ethiopian canon: [MTerryJack/ethiopian-canon](https://huggingface.co/datasets/MTerryJack/ethiopian-canon) (public), with per-row `source`, `language` (gez/am/en) and `licence` columns. Ge'ez: NT Acts-Revelation, 1 Enoch, Jubilees, Sirach, Wisdom and Baruch from EOTCOpenSource/bible-dataset (labelled Dillmann edition; no licence stated, included at owner request); 1-3 Meqabyan, 4 Baruch and Canons of Clement from [Beta maṣāḥǝft](https://betamasaheft.eu) (CC BY-SA 4.0). Amharic: Qalementos, Didesqelya, Tobit, Judith, Ezra Kale (1 Esdras), Ezra Sutuel (2 Esdras), Letter of Jeremiah and Daniel 3/13/14 (Azariah, Susanna, Bel) from the EOTCOpenSource 80-weahadu JSON (copy at henaorth16/80-weahadu; no licence stated, included at owner request). English kept only where no Ge'ez/Amharic exists: Sinodos (Horner 1904, plus Arabic and Sahidic Statutes), Testament of Our Lord (Cooper & Maclean 1902), Gessew (Schodde 1885), Apostolic Constitutions and 85 Canons (ANF 7), and WEB Additions to Esther, Prayer of Manasseh and Psalm 151. Removed 2026-11-04 as duplicates of the source-language texts: Charles's English Enoch/Jubilees, WEB English apocrypha and Acts-Revelation, Harden's Didascalia, Grébaut's French Qalementos 1-3, Gibson's Kitab al-Magall and Mingana's Apocalypse of Peter. Not available: Ge'ez/Amharic Sinodos, Kidan, Gessew and Abtelis.
- Zines: [MTerryJack/hacker-zines](https://huggingface.co/datasets/MTerryJack/hacker-zines).
- Prayer times: [AlAdhan](https://aladhan.com) API.
- Maps: [OpenStreetMap](https://www.openstreetmap.org/copyright) data via Nominatim (please respect its usage policy) and routing via the public [OSRM](https://project-osrm.org) demo server. Map data (c) OpenStreetMap contributors, ODbL.
- Web search: DuckDuckGo. News: Al Jazeera, DW, The Real News Network, Reuters (via Google News, reuters.com only), Google News, Simon Willison, Krebs on Security, Schneier on Security, LWN, 2600 RSS feeds.
- Third-party MCP servers: duckduckgo-mcp-server, mcp-hn, mcp-youtube-transcript, arxiv-mcp-server. `mcp_pipe.py` is adapted from the Xiaozhi mcp-calculator example pipe.

Debugging: the hadith-commentary, prophet-dreams, sufi-classics, hilyat-al-awliya, gospel and ethiopian-canon servers log one stderr line per tool call (`tool=… args=… ids=[…] ms=… ok|error=…`) and, at startup, the pinned dataset revision and row counts; they exit at startup if the dataset cannot load. See `docker logs stackchan-bridge`.
