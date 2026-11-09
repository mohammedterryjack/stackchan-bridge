FROM python:3.12-slim
RUN useradd -m -u 1000 bridge
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt fastmcp duckduckgo-mcp-server workspace-mcp mcp-server-time wikipedia-mcp "mcp[cli]>=1.6.0" "httpx>=0.28.1" "requests>=2.32.3" "duckdb==1.3.2"
# Japanese romaji (japanese server): MeCab tokenizer + bundled unidic-lite dictionary, baked in at build time
RUN pip install --no-cache-dir "fugashi==1.5.2" "unidic-lite==1.0.8"
# Hijri -> Gregorian dates for alhabibumar.com news (habib-omar server), Umm al-Qura calendar, pure Python
RUN pip install --no-cache-dir "hijridate==2.6.0"
# hackernews needs mcp 1.x; isolated venv so they do not clash with mcp 2.x above
RUN python -m venv /opt/social && /opt/social/bin/pip install --no-cache-dir "mcp>=1.20,<2" mcp-hn==0.1.0
# youtube 0.8.3 (needs mcp 2.x, supports --response-limit) is not on PyPI; pinned to GitHub commit
RUN pip install --no-cache-dir "https://github.com/jkawamoto/mcp-youtube-transcript/archive/fc6e33cfdc8618d375dc600bb54f0576d84615fe.tar.gz"
# arxiv-mcp-server needs mcp 1.x (>=1.27); own venv
RUN python -m venv /opt/arxiv && /opt/arxiv/bin/pip install --no-cache-dir "mcp>=1.27,<2" arxiv-mcp-server==0.8.2
# DuckDB httpfs fetched at build time for the build arch, under /opt (compose tmpfs wipes /home/bridge); pinned duckdb==1.3.2
COPY duckdb_bake_httpfs.py /tmp/duckdb_bake_httpfs.py
RUN python /tmp/duckdb_bake_httpfs.py && rm -f /tmp/duckdb_bake_httpfs.py
COPY mcp_pipe.py calculator.py news_server.py zine_server.py time_server.py hn_server.py web_search_server.py prayer_times_server.py youtube_search_server.py quran_hadith_server.py maps_server.py tanakh_server.py rumi_server.py aesop_server.py hadith_commentary_server.py prophet_dreams_server.py sufi_classics_server.py gospel_server.py ethiopian_server.py hilya_server.py female_awliya_server.py habib_omar_server.py recipes_server.py japanese_server.py japanese_romaji.py memory_server.py bookmarks.py verse_corpus.py mcp_tool_log.py mcp_filter_proxy.py mcp_config.json ./
COPY filter_configs/ /app/filter_configs/
# Reading bookmarks (small user state) live on the named volume mounted here by compose.yaml; the image dir is
# owned by bridge so a fresh volume inherits that ownership.
RUN mkdir -p /data/bookmarks && chown bridge:bridge /data/bookmarks
ENV BOOKMARKS_FILE=/data/bookmarks/bookmarks.json
USER bridge
CMD ["python","mcp_pipe.py"]
