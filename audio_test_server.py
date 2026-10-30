"""Audio test MCP server: one tool that hands Stack-chan a ~5s public-domain mp3 clip.

Uses the community ResourceLink protocol: the tool returns a resource_link whose
description is the mp3 URL; the client then pages it via resources/read on
resource://read_from_http with {url, start, end}, getting base64 blobs and finally
base64("[DONE]"). Nothing is stored on disk; bytes are fetched per page.
"""
import base64
import json
import sys
import urllib.request

URI = "resource://read_from_http"
CLIP_URL = ("https://upload.wikimedia.org/wikipedia/commons/c/c6/"
            "Carol_of_the_Bells%2C_Ukrainian_-_Concert_Band_-_United_States_Air_Force_Heritage_of_America_Band.mp3")
CLIP_NAME = "Carol of the Bells (US Air Force Band, public domain), 5 second test clip"
CLIP_BYTES = 81920  # ~5s at the file's ~130 kbps
PAGE = 102400
UA = "stackchan-bridge/1.0 (audio test)"
DONE = base64.b64encode(b"[DONE]").decode()

TOOL = {
    "name": "audio_test_play",
    "description": "Play a short 5 second test sound clip on the robot speaker. Use when the user asks to test audio or play the test sound.",
    "inputSchema": {"type": "object", "properties": {}},
}


def _fetch(start: int, end: int) -> bytes:
    req = urllib.request.Request(CLIP_URL, headers={"User-Agent": UA, "Range": f"bytes={start}-{end - 1}"})
    with urllib.request.urlopen(req, timeout=8) as r:
        if r.status != 206:
            raise RuntimeError(f"clip server ignored Range (HTTP {r.status})")
        data = r.read(end - start + 1)
    if not data:
        raise RuntimeError("clip server returned no audio bytes")
    return data[: end - start]


_audio_start = None


def _audio_offset() -> int:
    """Byte offset where MP3 frames begin (skip the ID3v2 tag, which holds cover art)."""
    global _audio_start
    if _audio_start is None:
        h = _fetch(0, 10)
        if h[:3] == b"ID3":
            size = (h[6] & 0x7F) << 21 | (h[7] & 0x7F) << 14 | (h[8] & 0x7F) << 7 | (h[9] & 0x7F)
            _audio_start = 10 + size + (10 if h[5] & 0x10 else 0)
        else:
            _audio_start = 0
    return _audio_start


def _to_int(args: dict, key: str, default: int) -> int:
    v = args.get(key, default)
    if v is None:
        return default
    return int(v)


def tools_call(params: dict) -> dict:
    if params.get("name") != TOOL["name"]:
        raise ValueError(f"unknown tool {params.get('name')}")
    _audio_offset()  # fail loudly now if the clip is unreachable
    return {"content": [
        {"type": "text", "text": "Playing a five second test clip of Carol of the Bells."},
        {"type": "resource_link", "uri": URI, "name": CLIP_NAME, "description": CLIP_URL, "mimeType": "audio/mpeg"},
    ], "isError": False}


def resources_read(params: dict) -> dict:
    if params.get("uri") != URI:
        raise ValueError(f"unknown resource {params.get('uri')}")
    args = params.get("arguments") or {}
    url = args.get("url") or CLIP_URL
    if url != CLIP_URL:
        raise ValueError("this test server only streams its own test clip")
    start = max(0, _to_int(args, "start", 0))
    end = min(_to_int(args, "end", start + PAGE), start + PAGE, CLIP_BYTES)
    if start >= CLIP_BYTES or end <= start:
        blob = DONE
    else:
        off = _audio_offset()
        blob = base64.b64encode(_fetch(off + start, off + end)).decode()
    return {"contents": [{"uri": URI, "mimeType": "audio/mpeg", "blob": blob}]}


def handle(msg: dict):
    method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if not isinstance(method, str) or method.startswith("notifications/"):
        return None
    try:
        if method == "initialize":
            res = {"protocolVersion": params.get("protocolVersion") or "2025-06-18",
                   "capabilities": {"tools": {"listChanged": False}, "resources": {"subscribe": False, "listChanged": False}},
                   "serverInfo": {"name": "audio_test", "version": "1.0"}}
        elif method == "ping":
            res = {}
        elif method == "tools/list":
            res = {"tools": [TOOL]}
        elif method == "tools/call":
            try:
                res = tools_call(params)
            except Exception as e:
                res = {"content": [{"type": "text", "text": f"Audio test failed: {e}"}], "isError": True}
        elif method == "resources/list":
            res = {"resources": [{"uri": URI, "name": "audio stream", "mimeType": "audio/mpeg"}]}
        elif method == "resources/read":
            res = resources_read(params)
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}
        return {"jsonrpc": "2.0", "id": mid, "result": res}
    except Exception as e:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": f"Audio test failed: {e}"}}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            print(f"audio_test: bad JSON line: {line[:200]}", file=sys.stderr)
            continue
        out = handle(msg)
        if out is not None:
            sys.stdout.write(json.dumps(out) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
