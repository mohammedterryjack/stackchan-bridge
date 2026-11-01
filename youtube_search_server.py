"""YouTube search by spoken words (title/channel/topic) -> video ids. Scrapes results page; fail loudly."""
from __future__ import annotations

import json
import re

import httpx
from fastmcp import FastMCP

TIMEOUT = httpx.Timeout(connect=2.0, read=5.0, write=2.0, pool=2.0)
mcp = FastMCP("YouTubeSearch")


def _walk(o, out):
    if isinstance(o, dict):
        if "videoRenderer" in o:
            out.append(o["videoRenderer"])
        for v in o.values():
            _walk(v, out)
    elif isinstance(o, list):
        for v in o:
            _walk(v, out)


def _text(x) -> str:
    if not x:
        return ""
    if "simpleText" in x:
        return x["simpleText"]
    return "".join(r.get("text", "") for r in x.get("runs", []))


@mcp.tool()
def youtube_search(query: str, limit: int = 3) -> str:
    """Use when the user wants a YouTube video on a topic or from a channel. limit 1-5 (default 3). Returns lines: title, channel, length (video ID) for youtube_info or youtube_transcript."""
    q = (query or "").strip()
    if not q:
        raise ValueError("query required")
    if not 1 <= limit <= 5:
        raise ValueError("limit must be 1..5")
    r = httpx.get(
        "https://www.youtube.com/results",
        params={"search_query": q, "hl": "en", "gl": "GB"},
        headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en-GB,en;q=0.9"},
        cookies={"CONSENT": "YES+1"},
        timeout=TIMEOUT,
        follow_redirects=True,
    )
    r.raise_for_status()
    m = re.search(r"var ytInitialData = (\{.*?\});</script>", r.text, re.S)
    if not m:
        raise RuntimeError("YouTube results page had no ytInitialData (layout changed or blocked)")
    vids: list[dict] = []
    _walk(json.loads(m.group(1)), vids)
    if not vids:
        return f"No YouTube videos found for '{q}'."
    lines = []
    for v in vids[:limit]:
        lines.append(f"{_text(v.get('title'))}, {_text(v.get('ownerText'))}, {_text(v.get('lengthText')) or 'live'} (video {v['videoId']})")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
