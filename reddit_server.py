"""Collapsed Reddit MCP over mcp-server-reddit (stdio NDJSON)."""
from __future__ import annotations

import atexit
import json
import os
import re
import subprocess
import threading
from typing import Any

from fastmcp import FastMCP

UPSTREAM = os.environ.get("REDDIT_UPSTREAM", "/opt/social/bin/mcp-server-reddit")
MAX_LIMIT = 10
SORT_MAP = {
    "hot": "get_subreddit_hot_posts",
    "new": "get_subreddit_new_posts",
    "top": "get_subreddit_top_posts",
    "rising": "get_subreddit_rising_posts",
}

mcp = FastMCP("Reddit")


class _Upstream:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._id = 0
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "FASTMCP_SHOW_SERVER_BANNER": "false"}
        self._p = subprocess.Popen(
            [UPSTREAM],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            bufsize=1,
        )
        assert self._p.stdin and self._p.stdout
        self._send(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "reddit-collapse", "version": "1"},
                },
            }
        )
        init = self._recv_id(1)
        if "error" in init:
            raise RuntimeError(f"upstream init failed: {init['error']}")
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _send(self, obj: dict) -> None:
        self._p.stdin.write(json.dumps(obj) + "\n")
        self._p.stdin.flush()

    def _recv_id(self, expect: int, timeout_s: float = 30.0) -> dict:
        import time

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            line = self._p.stdout.readline()
            if not line:
                err = self._p.stderr.read() if self._p.poll() is not None else ""
                raise RuntimeError(f"upstream EOF: {err[:400]}")
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == expect:
                return msg
        raise TimeoutError(f"upstream timeout waiting for id={expect}")

    def call(self, name: str, arguments: dict) -> str:
        with self._lock:
            mid = self._next_id()
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": mid,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                }
            )
            msg = self._recv_id(mid)
        if "error" in msg:
            raise RuntimeError(str(msg["error"]))
        result = msg.get("result") or {}
        if result.get("isError"):
            texts = [
                c.get("text", "")
                for c in (result.get("content") or [])
                if c.get("type") == "text"
            ]
            raise RuntimeError("".join(texts) or "upstream tool error")
        parts: list[str] = []
        for c in result.get("content") or []:
            if c.get("type") == "text":
                parts.append(c.get("text") or "")
            else:
                parts.append(json.dumps(c, ensure_ascii=False))
        if not parts and "structuredContent" in result:
            parts.append(json.dumps(result["structuredContent"], ensure_ascii=False))
        out = "\n".join(parts)
        if not out:
            raise RuntimeError(f"empty result from upstream {name}")
        return out

    def close(self) -> None:
        try:
            self._p.terminate()
            self._p.wait(timeout=3)
        except Exception:
            self._p.kill()


_up: _Upstream | None = None
_up_lock = threading.Lock()


def _upstream() -> _Upstream:
    global _up
    with _up_lock:
        if _up is None:
            _up = _Upstream()
            atexit.register(_up.close)
        return _up


def _clamp(n: int, label: str) -> int:
    if not 1 <= n <= MAX_LIMIT:
        raise ValueError(f"{label} must be 1..{MAX_LIMIT}")
    return n


def _compact(raw: str) -> str:
    """Upstream returns a JSON list of posts; keep title/score/subreddit/url. Fail loudly on bad shape."""
    posts = json.loads(raw)
    if not isinstance(posts, list):
        raise RuntimeError("unexpected reddit upstream shape")
    return "\n".join(f"{p['title']}, {p['score']} points, r/{p['subreddit']} (post {p['id']})" for p in posts)


@mcp.tool()
def reddit_posts(
    subreddit: str | None = None,
    sort: str = "hot",
    limit: int = 5,
) -> str:
    """List Reddit posts. subreddit optional (omit for front page). sort: hot|new|top|rising. limit 1-10 (default 5). Lines end (post ID); use reddit_post to open one."""
    limit = _clamp(limit, "limit")
    s = (sort or "hot").strip().lower()
    if s not in SORT_MAP:
        raise ValueError(f"sort must be one of: {', '.join(sorted(SORT_MAP))}")
    sub = (subreddit or "").strip().removeprefix("/").removeprefix("r/")
    up = _upstream()
    if not sub:
        if s != "hot":
            raise ValueError("frontpage only supports sort=hot; pass a subreddit otherwise")
        return _compact(up.call("get_frontpage_posts", {"limit": limit}))
    tool = SORT_MAP[s]
    args: dict[str, Any] = {"subreddit_name": sub, "limit": limit}
    if s == "top":
        args["time"] = "day"
    return _compact(up.call(tool, args))


def _short(t: str, n: int) -> str:
    t = re.sub(r"https?://\S+", "link", t or "")
    t = re.sub(r"[*_>#`~\\]+", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + "..."


@mcp.tool()
def reddit_post(post_id: str) -> str:
    """One Reddit post plus top comments as spoken text. post_id from (post 1abc23)."""
    pid = re.sub(r"^(post|t3_)\s*", "", (post_id or "").strip(), flags=re.I).strip()
    pid = pid.removeprefix("t3_")
    if not re.fullmatch(r"[a-z0-9]{4,10}", pid, re.I):
        raise ValueError(f"post_id should look like 1abc23, got {post_id!r}")
    d = json.loads(_upstream().call("get_post_content", {"post_id": pid, "comment_limit": 5, "comment_depth": 1}))
    p = d["post"]
    lines = [f"{p['title']}, {p['score']} points, {p['comment_count']} comments, r/{p['subreddit']} (post {p['id']})"]
    body = p.get("content") or ""
    if body and p.get("post_type") == "text":
        lines.append(_short(body, 300))
    for c in d["comments"][:5]:
        lines.append(f"{c['author']}: {_short(c['body'], 150)}")
    if not d["comments"]:
        lines.append("No comments yet.")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
