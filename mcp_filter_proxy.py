#!/usr/bin/env python3
"""Stdio MCP proxy: allowlist tools, patch schemas, inject/clamp args. Fail loudly."""
from __future__ import annotations

import json
import re
import os
import subprocess
import sys
import threading
from typing import Any


def _load_config() -> dict:
    path = os.environ.get("FILTER_PROXY_CONFIG")
    if not path:
        raise RuntimeError("FILTER_PROXY_CONFIG env var is required")
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    for t, f in (cfg.get("reply_format") or {}).items():
        if f not in FORMATTERS:
            raise RuntimeError(f"unknown reply_format {f!r} for {t}")
    if "allow" not in cfg or not isinstance(cfg["allow"], list) or not cfg["allow"]:
        raise RuntimeError("FILTER_PROXY_CONFIG.allow must be a non-empty list")
    return cfg


def _patch_tool(tool: dict, cfg: dict) -> dict | None:
    name = tool.get("name")
    allow = set(cfg["allow"])
    if name not in allow:
        return None
    out = dict(tool)
    pub = (cfg.get("rename") or {}).get(name)
    if pub:
        out["name"] = pub
    descs = cfg.get("descriptions") or {}
    if name in descs:
        out["description"] = descs[name]
    schema = dict(out.get("inputSchema") or out.get("input_schema") or {})
    props = dict(schema.get("properties") or {})
    drop = set((cfg.get("drop_params") or {}).get(name) or []) | set((cfg.get("hide_params") or {}).get(name) or [])
    for p in drop:
        props.pop(p, None)
    defaults = (cfg.get("schema_defaults") or {}).get(name) or {}
    for k, v in defaults.items():
        if k not in props:
            continue
        prop = dict(props[k])
        prop["default"] = v
        props[k] = prop
    maxima = (cfg.get("maxima") or {}).get(name) or {}
    for k, mx in maxima.items():
        if k not in props:
            continue
        prop = dict(props[k])
        prop["maximum"] = mx
        props[k] = prop
    # optional: make params optional
    for k in (cfg.get("optional_params") or {}).get(name) or []:
        req = list(schema.get("required") or [])
        if k in req:
            req.remove(k)
            schema["required"] = req
    for up_k, pub_k in ((cfg.get("arg_rename") or {}).get(name) or {}).items():
        if up_k not in props:
            raise RuntimeError(f"arg_rename: {name} has no param {up_k!r}")
        props[pub_k] = props.pop(up_k)
        schema["required"] = [pub_k if r == up_k else r for r in schema.get("required") or []]
    for k, spec in ((cfg.get("add_params") or {}).get(name) or {}).items():
        props[k] = spec
    for k, d in ((cfg.get("param_desc") or {}).get(name) or {}).items():
        if k not in props:
            raise RuntimeError(f"param_desc: {name} has no param {k!r}")
        props[k] = {**props[k], "description": d}
    schema["required"] = [r for r in schema.get("required") or [] if r in props]
    schema["properties"] = props
    out["inputSchema"] = schema
    out.pop("input_schema", None)
    # structuredContent is dropped from results, so drop outputSchema too
    out.pop("outputSchema", None)
    return out


def _arxiv_line(p: dict) -> str:
    authors = p.get("authors") or []
    who = authors[0] + (" et al" if len(authors) > 1 else "") if authors else "unknown author"
    return f"{p['title'].strip()}, {who}, {(p.get('published') or '')[:4]} (arxiv {p['id']})"


def _fmt_arxiv_search(text: str, args: dict | None = None) -> str:
    d = json.loads(text)
    papers = d["papers"]
    if not papers:
        return "No arXiv papers found."
    return "\n".join(re.sub(r"\s+", " ", _arxiv_line(p)) for p in papers)


def _fmt_arxiv_abstract(text: str, args: dict | None = None) -> str:
    d = json.loads(text)
    if d.get("status") != "success":
        raise RuntimeError(f"arxiv get_abstract failed: {text[:300]}")
    head = _arxiv_line({**d, "id": d["paper_id"]})
    return re.sub(r"[ \t]+", " ", head + "\n" + re.sub(r"\s+", " ", d["abstract"]).strip())


def _fmt_yt_transcript(text: str, args: dict | None = None) -> str:
    d = json.loads(text)
    body = re.sub(r"\[[^\]]*\]|♪", " ", d["transcript"])
    body = re.sub(r"\s+", " ", body).strip()
    out = f"{d.get('title') or 'Transcript'}:\n{body}"
    if d.get("next_cursor"):
        out += f"\nmore: next_cursor {d['next_cursor']}"
    return out


def _fmt_arxiv_abstract_short(text: str, args: dict | None = None) -> str:
    out = _fmt_arxiv_abstract(text)
    if len(out) > 900:
        out = out[:900].rsplit(" ", 1)[0] + "..."
    return out


def _fmt_arxiv_read(text: str, args: dict | None = None) -> str:
    d = json.loads(text)
    if d.get("status") != "success":
        raise RuntimeError(f"arxiv download_paper failed: {text[:300]}")
    total = int(d["content_length"])
    parts = max(1, -(-total // ARXIV_PART_CHARS))
    n = int(d["start"]) // ARXIV_PART_CHARS + 1
    body = re.sub(r"\s+", " ", d["content"]).strip()
    return f"{body}\n(arxiv {d['paper_id']} part {n} of {parts})"


def _fmt_yt_info(text: str, args: dict) -> str:
    d = json.loads(text)
    m = re.search(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{11})", args["url"])
    if not m:
        raise RuntimeError(f"cannot read video id from {args['url']!r}")
    return f"{d['title']}, {d['uploader']}, {d['duration']}, uploaded {d['upload_date'][:4]} (video {m.group(1)})"


FORMATTERS = {
    "arxiv_abstract_short": _fmt_arxiv_abstract_short,
    "arxiv_read": _fmt_arxiv_read,
    "yt_info": _fmt_yt_info,
    "arxiv_search": _fmt_arxiv_search,
    "arxiv_abstract": _fmt_arxiv_abstract,
    "yt_transcript": _fmt_yt_transcript,
}


ARXIV_PART_CHARS = 1000


def _prepare_args(name: str, arguments: dict | None, cfg: dict) -> dict:
    args = dict(arguments or {})
    for up_k, pub_k in ((cfg.get("arg_rename") or {}).get(name) or {}).items():
        if pub_k in args:
            args[up_k] = args.pop(pub_k)
    if name in (cfg.get("arxiv_part_tools") or []):
        part = args.pop("part", 1)
        try:
            part = int(part)
        except (TypeError, ValueError):
            raise ValueError("part must be a whole number like 2")
        if part < 1:
            raise ValueError("part must be 1 or more")
        args["start"] = (part - 1) * ARXIV_PART_CHARS
        args["max_chars"] = ARXIV_PART_CHARS
        args["return_full_text"] = False
    for k, prefixes in ((cfg.get("strip_prefixes") or {}).get(name) or {}).items():
        v = args.get(k)
        if isinstance(v, str):
            v = v.strip()
            for pre in prefixes:
                if v.lower().startswith(pre):
                    v = v[len(pre):].strip()
            args[k] = v
    for k, v in ((cfg.get("call_defaults") or {}).get(name) or {}).items():
        if k not in args or args[k] is None:
            args[k] = v
    for k, mx in ((cfg.get("maxima") or {}).get(name) or {}).items():
        if k in args and args[k] is not None:
            try:
                if int(args[k]) > int(mx):
                    raise ValueError(f"{name}.{k}={args[k]} exceeds maximum {mx}")
            except (TypeError, ValueError) as e:
                if "exceeds maximum" in str(e):
                    raise
                raise ValueError(f"{name}.{k} must be an integer <= {mx}") from e
    forbidden = (cfg.get("forbid_true") or {}).get(name) or []
    for k in forbidden:
        if args.get(k) is True:
            raise ValueError(f"{name}.{k}=true is not allowed")
    drop = (cfg.get("drop_params") or {}).get(name) or []
    for k in drop:
        args.pop(k, None)
    return args


def _reader(src, dst, transform):
    for line in src:
        if not line:
            break
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            # fail loudly on non-JSON from child
            raise RuntimeError(f"child sent non-JSON line: {line[:200]!r}")
        out = transform(msg)
        if out is None:
            continue
        dst.write(json.dumps(out, ensure_ascii=False) + "\n")
        dst.flush()


def main() -> None:
    cfg = _load_config()
    argv = sys.argv[1:]
    if not argv:
        raise RuntimeError("usage: mcp_filter_proxy.py <upstream command...>")
    child = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        text=True,
        bufsize=1,
    )
    assert child.stdin and child.stdout

    pending_calls: dict[Any, str] = {}
    pending_started: dict[Any, float] = {}
    pending_args: dict[Any, dict] = {}
    deadline_s = float(os.environ.get("MCP_TOOL_DEADLINE_S", cfg.get("deadline_s", 8)))
    deadline_lock = threading.Lock()

    def from_client(msg: dict) -> dict | None:
        method = msg.get("method")
        if method == "tools/call":
            params = dict(msg.get("params") or {})
            rev = {v: k for k, v in (cfg.get("rename") or {}).items()}
            name = rev.get(params.get("name"), params.get("name"))
            if name in (cfg.get("rename") or {}) and params.get("name") == name:
                name = None  # old upstream name is hidden once renamed
            params["name"] = name
            msg = dict(msg); msg["params"] = params
            if name not in cfg["allow"]:
                # answer error ourselves without forwarding
                err = {
                    "jsonrpc": "2.0",
                    "id": msg.get("id"),
                    "error": {
                        "code": -32602,
                        "message": f"tool {name!r} is not exposed by filter proxy",
                    },
                }
                sys.stdout.write(json.dumps(err) + "\n")
                sys.stdout.flush()
                return None
            id_args = (cfg.get("youtube_id_args") or {}).get(name) or []
            if id_args and isinstance(params.get("arguments"), dict):
                a = dict(params["arguments"])
                for up_k, pub_k in ((cfg.get("arg_rename") or {}).get(name) or {}).items():
                    if pub_k in a:
                        a[up_k] = a.pop(pub_k)
                for k in id_args:
                    v = a.get(k)
                    if isinstance(v, str):
                        v = re.sub(r"^\s*(video|youtube)\s+", "", v.strip(), flags=re.I)
                        if re.fullmatch(r"[A-Za-z0-9_-]{11}", v):
                            v = "https://www.youtube.com/watch?v=" + v
                        a[k] = v
                params = dict(params); params["arguments"] = a
            try:
                args = _prepare_args(name, params.get("arguments"), cfg)
            except ValueError as e:
                err = {
                    "jsonrpc": "2.0",
                    "id": msg.get("id"),
                    "result": {
                        "content": [{"type": "text", "text": f"Error: {e}"}],
                        "isError": True,
                    },
                }
                sys.stdout.write(json.dumps(err) + "\n")
                sys.stdout.flush()
                return None
            params = dict(params)
            params["arguments"] = args
            msg = dict(msg)
            msg["params"] = params
            if "id" in msg:
                with deadline_lock:
                    pending_calls[msg["id"]] = name
                    pending_args[msg["id"]] = args
                    pending_started[msg["id"]] = __import__("time").monotonic()
        return msg

    def from_child(msg: dict) -> dict | None:
        # tools/list result
        if "result" in msg and isinstance(msg["result"], dict) and "tools" in msg["result"]:
            tools = []
            for t in msg["result"]["tools"]:
                patched = _patch_tool(t, cfg)
                if patched is not None:
                    tools.append(patched)
            if not tools:
                raise RuntimeError("filter proxy allowlist matched zero upstream tools")
            result = dict(msg["result"])
            result["tools"] = tools
            out = dict(msg)
            out["result"] = result
            return out
        # truncate oversized tool results when configured
        mid = msg.get("id")
        tool_name = None
        if mid is not None:
            with deadline_lock:
                tool_name = pending_calls.pop(mid, None)
                call_args = pending_args.pop(mid, {})
                pending_started.pop(mid, None)
        # drop structuredContent duplicate (same data as text content) to keep replies small
        if isinstance(msg.get("result"), dict) and "structuredContent" in msg["result"]:
            r2 = dict(msg["result"]); r2.pop("structuredContent")
            msg = dict(msg); msg["result"] = r2
        fmt_name = (cfg.get("reply_format") or {}).get(tool_name) if tool_name else None
        if fmt_name and isinstance(msg.get("result"), dict) and not msg["result"].get("isError"):
            fmt = FORMATTERS[fmt_name]
            r3 = dict(msg["result"])
            r3["content"] = [
                {"type": "text", "text": fmt(c["text"], call_args)} if c.get("type") == "text" else c
                for c in r3.get("content") or []
            ]
            msg = dict(msg); msg["result"] = r3
        trunc_map = cfg.get("truncate_result_chars") or {}
        if tool_name and tool_name in trunc_map and "result" in msg:
            limit = int(trunc_map[tool_name])
            result = msg["result"]
            if isinstance(result, dict) and "content" in result:
                content = []
                for part in result.get("content") or []:
                    part = dict(part)
                    if part.get("type") == "text" and isinstance(part.get("text"), str):
                        if len(part["text"]) > limit:
                            part["text"] = part["text"][:limit] + f"\n…[truncated to {limit} chars]"
                    content.append(part)
                result = dict(result)
                result["content"] = content
                out = dict(msg)
                out["result"] = result
                return out
        return msg

    def client_to_child():
        try:
            for line in sys.stdin:
                msg = json.loads(line)
                out = from_client(msg)
                if out is None:
                    continue
                child.stdin.write(json.dumps(out, ensure_ascii=False) + "\n")
                child.stdin.flush()
        finally:
            try:
                child.stdin.close()
            except Exception:
                pass

    def child_to_client():
        try:
            for line in child.stdout:
                msg = json.loads(line)
                out = from_child(msg)
                if out is None:
                    continue
                sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
                sys.stdout.flush()
        finally:
            pass


    def deadline_watcher():
        import time
        while True:
            time.sleep(0.2)
            if child.poll() is not None:
                return
            now = time.monotonic()
            overdue = []
            with deadline_lock:
                for mid, started in list(pending_started.items()):
                    if now - started >= deadline_s:
                        name = pending_calls.pop(mid, "?")
                        pending_started.pop(mid, None)
                        overdue.append((mid, name, now - started))
            for mid, name, elapsed in overdue:
                err = {
                    "jsonrpc": "2.0",
                    "id": mid,
                    "error": {
                        "code": -32000,
                        "message": (
                            f"tool {name!r} exceeded {deadline_s:.0f}s hard deadline "
                            f"(elapsed_ms={elapsed*1000:.0f})"
                        ),
                    },
                }
                sys.stdout.write(json.dumps(err) + "\n")
                sys.stdout.flush()
                try:
                    child.kill()
                except Exception:
                    pass
                return

    t1 = threading.Thread(target=client_to_child, daemon=True)
    t2 = threading.Thread(target=child_to_client, daemon=True)
    t1.start()
    t2.start()
    tw = threading.Thread(target=deadline_watcher, daemon=True)
    tw.start()
    t1.join()
    rc = child.wait()
    if rc not in (0, None) and t2.is_alive():
        t2.join(timeout=1)
    if rc not in (0, -15, -9):
        # upstream exited with error after stdin closed is normal on shutdown
        pass


if __name__ == "__main__":
    main()
