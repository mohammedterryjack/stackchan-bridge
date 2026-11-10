"""Stack-chan's own MCP tools in ONE process: each module registers its tools on app.mcp when imported (the import
order is the order Xiaozhi lists them). Book data is read on demand from Hugging Face (hf_data.py); nothing is
preloaded. At startup two background threads check the datasets and Hindsight and log loudly if either is down."""
import os
import threading

import calculator  # noqa: F401  (each import registers that module's tools)
import web_search_server  # noqa: F401
import time_server  # noqa: F401
import hn_server  # noqa: F401
import news_server  # noqa: F401
import zine_server  # noqa: F401
import prayer_times_server  # noqa: F401
import youtube_search_server  # noqa: F401
import quran_hadith_server  # noqa: F401
import maps_server  # noqa: F401
import tanakh_server  # noqa: F401
import rumi_server  # noqa: F401
import aesop_server  # noqa: F401
import hadith_commentary_server  # noqa: F401
import prophet_dreams_server  # noqa: F401
import sufi_classics_server  # noqa: F401
import gospel_server  # noqa: F401
import ethiopian_server  # noqa: F401
import hilya_server  # noqa: F401
import female_awliya_server  # noqa: F401
import habib_omar_server  # noqa: F401
import recipes_server  # noqa: F401
import japanese_server  # noqa: F401
import memory_server
from app import mcp
from hf_data import warm_all

if __name__ == "__main__":
    warm = os.environ.get("STACKCHAN_WARM", "all")  # tools/sc_t.py and tools/toollist.py run one call per process
    if warm == "all":
        threading.Thread(target=warm_all, name="hf-warm", daemon=True).start()
        threading.Thread(target=memory_server.startup, name="memory-startup", daemon=True).start()
    elif warm == "memory":  # learn_* smoke tests: Hindsight check and bank creation before the call
        memory_server.startup()
    elif warm != "none":
        raise SystemExit(f"STACKCHAN_WARM must be all, memory or none, not {warm!r}")
    mcp.run(transport="stdio", show_banner=False)
