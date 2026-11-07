# Stack-chan MCP bridge

A Docker container that gives an M5Stack Stack-chan (running Xiaozhi firmware, connected to the xiaozhi.me cloud) extra tools over MCP: web search, news, time, prayer times, Quran/Hadith, Tanakh, Rumi, Aesop, hadith commentary, dreams of the Prophet, Sufi classics (al-Ghazali, Ibn Arabi, al-Jilani), Hilyat al-Awliya, recipes, Japanese lessons in romaji, maps and more. It runs 27 local MCP servers exposing 97 tools. Replies are short, speakable plain text, and each tool call has an 8 second deadline. Errors fail loudly with a spoken message rather than silently falling back.

## How it works
`mcp_pipe.py` opens a WebSocket to your xiaozhi.me agent's MCP endpoint and starts every server listed in `mcp_config.json` as a child process (stdio). When you talk to Stack-chan, the Xiaozhi cloud can call these tools and read the result aloud. Tools are only used while someone is talking to the robot.

## Servers and tools (26 servers, 92 tools)
- calculator: calculator, more_text (pages through long replies)

Reply cap: mcp_pipe.py caps every tool reply at ~800 chars (max 1000), splitting on sentence/verse boundaries with "Part N of M" notes; Stack-chan calls more_text(part=N) to continue.

- web-search: web_search, web_read
- time: time_now, time_convert (city or timezone required, no default)
- hackernews: hn_top_stories, hn_search, hn_story, hn_user
- reddit: reddit_posts, reddit_post
- youtube: youtube_transcript, youtube_info
- youtube-search: youtube_search
- news: news_headlines, news_simon_willison, news_security
- arxiv: arxiv_search, arxiv_read, arxiv_abstract
- zines: zine_search, zine_read
- darknet-diaries: darknet_episodes, darknet_episode, darknet_transcript, darknet_find
- prayer-times: prayer_times, prayer_next, prayer_alarms (city and country required, no default)
- quran-hadith: quran_read, quran_search, hadith_get, hadith_random
- tanakh: tanakh_verse, tanakh_chapter, tanakh_search (Sefaria, English)
- rumi: rumi_read, rumi_search, rumi_random (Nicholson's English Mathnawi, Books 1-6, via HF dataset)
- aesop: aesop_read, aesop_search, aesop_random (Townsend's Aesop's Fables, 313 fables, via HF dataset)
- hadith-commentary: bahjat_read, bahjat_search, bahjat_random (Ibn Abi Jamrah's Bahjat al-Nufus, classical Arabic, via HF dataset; search normalises alif, ya, ta marbuta and diacritics)
- prophet-dreams: dreams_read, dreams_search, dreams_random, dreams_sources (dreams others saw of the Prophet from al-Mara'i al-Hisan (Ibn Abi Jamrah), Sa'adat al-Darayn (al-Nabhani), al-Manamat (Ibn Abi al-Dunya) and al-Mustaghithin billah (Ibn Bashkuwal), classical Arabic, via HF dataset; dream_number is per source, and without `source` the read defaults to al-Mara'i)
- sufi-classics: ghazali_contents/read/search/random (al-Ghazali, Ihya Ulum al-Din), ibnarabi_contents/read/search/random (Ibn Arabi, al-Futuhat al-Makkiyya), jilani_contents/read/search/random (Abd al-Qadir al-Jilani, al-Ghunya li-Talibi Tariq al-Haqq); original Arabic via HF dataset, Stack-chan explains or translates
- hilyat-al-awliya: hilya_people, hilya_read, hilya_search, hilya_random (Abu Nu'aym al-Isfahani, Hilyat al-Awliya wa Tabaqat al-Asfiya: 674 entries/biographies of sahaba, tabi'in and early ascetics; original Arabic via HF dataset, Stack-chan explains or translates; famous English names like Hasan al-Basri or Junayd are mapped to Arabic)
- female-awliya: sulami_women_people/read/search/random (al-Sulami, Dhikr al-Niswa: 82 women including Rabia al-Adawiyya as entry 1) and safwa_women_people/read/search/random (Ibn al-Jawzi, Sifat al-Safwa women: ~110 biographies); original Arabic via HF dataset MTerryJack/female-awliya (OpenITI, CC BY-NC-SA 4.0); short ~800-char parts for spoken audio
- habib-omar: habib_omar_latest, habib_omar_read (Habib Omar / Habib Umar bin Hafiz live updates: Arabic news from alhabibumar.com and English Sayyidi teachings from muwasala.org; live fetch only, no storage; short parts)
- recipes: recipe_suggest, recipe_search, recipe_by_ingredient, recipe_read, recipe_ingredients (live from TheMealDB free public API, test key 1; small in-memory cache; Stack-chan may adapt methods for the Tokit Omni Cook, whose own cookNjoy recipe API requires signed requests and is not used)
- japanese: japanese_word, japanese_phrase_random, japanese_phrase_search, japanese_phrase_topics, japanese_to_romaji (learn Japanese by voice: replies are ASCII romaji only, English first then 'In Japanese: ...', so the English TTS voice can read them; words live from the jisho.org API with a small in-memory cache; ~147k Tatoeba eng-jpn phrase pairs (CC BY 2.0 FR) with precomputed romaji via HF dataset MTerryJack/japanese-phrases, built by tools/build_japanese_phrases.py; romaji from fugashi + unidic-lite baked into the image)
- gospel: gospel_books, gospel_read, gospel_search, gospel_random (Matthew, Mark, Luke, John from the World English Bible and the Gospel of Barnabas, Ragg 1907; one row per verse via HF dataset)
- ethiopian-canon: ethiopian_books, ethiopian_read, ethiopian_search, ethiopian_random (Ethiopian Orthodox canon books not in the Tanakh or gospel tools, in their source language where possible, which the AI translates or explains: Ge'ez 1 Enoch, Jubilees, Sirach, Wisdom, Baruch, 4 Baruch, 1-3 Meqabyan, Canons of Clement and Acts to Revelation; Amharic EOTC Qalementos, Didesqelya, Tobit, Judith, 1-2 Esdras (Ezra Kale/Sutuel), Letter of Jeremiah, Azariah, Susanna and Bel; English only where no Ge'ez/Amharic exists: Sinodos and its Arabic/Sahidic recensions (Horner), Testament of Our Lord (Kidan stand-in), Gessew (Schodde), Apostolic Constitutions and 85 Canons (Abtelis stand-in), Additions to Esther, Prayer of Manasseh, Psalm 151)
- maps: map_search, map_nearby, map_distance (OpenStreetMap, keyless)

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
- Web search: DuckDuckGo. News: Al Jazeera, DW, The Real News Network, Reuters (via Google News, reuters.com only), Google News, Simon Willison, Krebs on Security, Schneier on Security, LWN, 2600 RSS feeds. Darknet Diaries: darknetdiaries.com and its podcast feed.
- Third-party MCP servers: duckduckgo-mcp-server, mcp-hn, mcp-server-reddit, mcp-youtube-transcript, arxiv-mcp-server. `mcp_pipe.py` is adapted from the Xiaozhi mcp-calculator example pipe.

Debugging: the hadith-commentary, prophet-dreams, sufi-classics, hilyat-al-awliya, gospel and ethiopian-canon servers log one stderr line per tool call (`tool=… args=… ids=[…] ms=… ok|error=…`) and, at startup, the pinned dataset revision and row counts; they exit at startup if the dataset cannot load. See `docker logs stackchan-bridge`.
