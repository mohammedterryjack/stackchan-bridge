"""Hacker News MCP: Firebase item API + Algolia search. Spoken plain text."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx
from fastmcp import FastMCP

FB = "https://hacker-news.firebaseio.com/v0"
ALGOLIA = "https://hn.algolia.com/api/v1"
MAX_STORIES = 10
MAX_COMMENTS = 4
TIMEOUT = httpx.Timeout(connect=2.0, read=3.0, write=2.0, pool=2.0)
STORY_TYPES = {
    "top": "topstories",
    "new": "newstories",
    "ask_hn": "askstories",
    "show_hn": "showstories",
}

mcp = FastMCP("HackerNews")


def _client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, headers={"User-Agent": "stackchan-hn/1.0"}, follow_redirects=True)


def _get_json(client: httpx.Client, url: str) -> Any:
    r = client.get(url)
    r.raise_for_status()
    return r.json()


def _item(client: httpx.Client | None, item_id: int) -> dict | None:
    if client is None:
        with _client() as c:
            data = _get_json(c, f"{FB}/item/{int(item_id)}.json")
    else:
        data = _get_json(client, f"{FB}/item/{int(item_id)}.json")
    if not data or data.get("deleted") or data.get("dead"):
        return None
    return data


def _story_line(it: dict) -> dict:
    return {
        "id": it.get("id"),
        "title": it.get("title"),
        "author": it.get("by"),
        "points": it.get("score"),
        "comments": it.get("descendants"),
    }


def _say(st: dict) -> str:
    bits = [st["title"] or "(untitled)", f"{st.get('points') or 0} points"]
    if st.get("comments") is not None:
        bits.append(f"{st['comments']} comments")
    return ", ".join(bits) + f" (hn {st['id']})"


def _plain(html_text: str) -> str:
    import html as _h, re as _re
    t = _re.sub(r"<[^>]+>", " ", _h.unescape(html_text or ""))
    t = _re.sub(r"\[\d+\]:?\s*https?://\S+", " ", t)
    t = _re.sub(r"\[\d+\]", "", t)
    t = _re.sub(r"https?://\S+", "link", t)
    return _re.sub(r"\s+", " ", t).strip()


def _fetch_items(ids: list[int]) -> list[dict]:
    by_id: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=min(10, max(1, len(ids)))) as pool:
        # per-call client inside _item(None, ...) — httpx.Client is not thread-safe
        futs = {pool.submit(_item, None, i): i for i in ids}
        for fut in as_completed(futs):
            it = fut.result()
            if not it:
                continue
            if it.get("type") == "comment":
                continue
            by_id[int(it["id"])] = it
    return [_story_line(by_id[i]) for i in ids if i in by_id]


@mcp.tool()
def hn_top_stories(story_type: str = "top", num_stories: int = 5) -> str:
    """Use when the user asks what is on Hacker News (tech and startup links), not general news. story_type: top|new|ask_hn|show_hn (default top). num_stories 1-10 (default 5). Returns lines ending (hn ID); hn_story opens one."""
    if not 1 <= num_stories <= MAX_STORIES:
        raise ValueError(f"num_stories must be 1..{MAX_STORIES}")
    key = (story_type or "top").strip().lower()
    if key not in STORY_TYPES:
        raise ValueError(f"story_type must be one of: {', '.join(STORY_TYPES)}")
    with _client() as client:
        ids = _get_json(client, f"{FB}/{STORY_TYPES[key]}.json")
    if not isinstance(ids, list) or not ids:
        raise RuntimeError("Firebase returned no story ids")
    stories = _fetch_items([int(i) for i in ids[:num_stories]])
    if not stories:
        raise RuntimeError("no stories fetched")
    return "\n".join(_say(st) for st in stories)


@mcp.tool()
def hn_search(query: str, num_results: int = 5, search_by_date: bool = False) -> str:
    """Use when the user wants Hacker News stories on a topic, e.g. 'what does Hacker News say about Rust'. num_results 1-10 (default 5). search_by_date: true for newest first. Returns lines ending (hn ID)."""
    q = (query or "").strip()
    if not q:
        raise ValueError("query required")
    if not 1 <= num_results <= MAX_STORIES:
        raise ValueError(f"num_results must be 1..{MAX_STORIES}")
    endpoint = "search_by_date" if search_by_date else "search"
    url = f"{ALGOLIA}/{endpoint}"
    with _client() as client:
        r = client.get(url, params={"query": q, "tags": "story", "hitsPerPage": num_results})
        r.raise_for_status()
        hits = r.json().get("hits") or []
    out = []
    for h in hits[:num_results]:
        out.append(
            {
                "id": int(h.get("objectID") or h.get("story_id") or 0),
                "title": h.get("title"),
                "author": h.get("author"),
                "points": h.get("points"),
                "comments": h.get("num_comments"),
            }
        )
    if not out:
        raise RuntimeError(f"no HN hits for {q!r}")
    return "\n".join(_say(st) for st in out)


def _comment_tree(client: httpx.Client, kids: list[int], depth: int, limit: int) -> list[dict]:
    if depth < 1 or not kids:
        return []
    ids = [int(k) for k in kids[:limit]]
    with ThreadPoolExecutor(max_workers=min(8, len(ids))) as pool:
        futs = {pool.submit(_item, None, i): i for i in ids}
        items: dict[int, dict] = {}
        for fut in as_completed(futs):
            it = fut.result()
            if it and it.get("type") == "comment" and it.get("text"):
                items[int(it["id"])] = it
    out = []
    for i in ids:
        it = items.get(i)
        if not it:
            continue
        text = _plain(it.get("text") or "")
        if len(text) > 200:
            text = text[:200].rsplit(" ", 1)[0] + "..."
        node = {"author": it.get("by"), "text": text}
        child_kids = it.get("kids") or []
        if depth > 1 and child_kids:
            node["comments"] = _comment_tree(client, child_kids, depth - 1, min(5, limit))
        out.append(node)
    return out


@mcp.tool()
def hn_story(story_id: int | str) -> str:
    """Use to open one Hacker News story and its discussion. story_id from '(hn 12345)'. Returns the story and top 4 comments."""
    import re as _re
    m = _re.fullmatch(r"\s*(?:hn\s*)?(\d+)\s*", str(story_id), _re.I)
    if not m:
        raise ValueError(f"story_id should be a number like 12345, got {story_id!r}")
    story_id = int(m.group(1))
    with _client() as client:
        it = _item(client, story_id)
        if not it:
            raise LookupError(f"story not found: {story_id}")
        base = _story_line(it)
        tree = _comment_tree(client, it.get("kids") or [], depth=1, limit=MAX_COMMENTS)
    lines = [_say(base)]
    if it.get("text"):
        lines.append(_plain(it["text"])[:600])
    lines += [f"{c['author']}: {c['text']}" for c in tree]
    out = "\n".join(lines)
    if len(out) > 2500:
        out = out[:2500].rsplit(" ", 1)[0] + "... (more comments not read)"
    return out


@mcp.tool()
def hn_user(user_name: str, num_stories: int = 5) -> str:
    """Use when the user asks about a Hacker News user. num_stories 1-10. Returns karma, about and recent stories."""
    if not 1 <= num_stories <= MAX_STORIES:
        raise ValueError(f"num_stories must be 1..{MAX_STORIES}")
    name = (user_name or "").strip()
    if not name:
        raise ValueError("user_name required")
    with _client() as client:
        user = _get_json(client, f"{FB}/user/{name}.json")
        if not user:
            raise LookupError(f"user not found: {name}")
        submitted = [int(i) for i in (user.get("submitted") or [])[: num_stories * 3]]
        stories = []
        for i in submitted:
            if len(stories) >= num_stories:
                break
            it = _item(client, i)
            if it and it.get("type") == "story":
                stories.append(_story_line(it))
        payload = {
            "id": user.get("id"),
            "karma": user.get("karma"),
            "created": user.get("created"),
            "about": (user.get("about") or "")[:300],
        }
    head = f"{payload['id']}, {payload['karma']} karma." + (f" About: {_plain(payload['about'])}" if payload["about"] else "")
    return "\n".join([head, *(_say(st) for st in stories)])


if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
