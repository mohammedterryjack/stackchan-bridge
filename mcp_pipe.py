"""
Simple MCP stdio <-> WebSocket pipe with optional unified config.
Version: 0.2.0

Usage (env):
    export MCP_ENDPOINT=<ws_endpoint>
    # Windows (PowerShell): $env:MCP_ENDPOINT = "<ws_endpoint>"

Start server process(es) from config:
Run all configured servers (default)
    python mcp_pipe.py

Run a single local server script (back-compat)
    python mcp_pipe.py path/to/server.py

Config discovery order:
    $MCP_CONFIG, then ./mcp_config.json

Env overrides:
    (none for proxy; uses current Python: python -m mcp_proxy)
"""

import asyncio
import websockets
import subprocess
import logging
import os
import signal
import sys
import json
import re
import time
import threading
from dotenv import load_dotenv

# Auto-load environment variables from a .env file if present
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('MCP_PIPE')

# Reconnection settings
INITIAL_BACKOFF = 1  # Initial wait time in seconds
MAX_BACKOFF = 60  # Maximum wait time in seconds

# WebSocket keepalive / limits
WS_OPTS = dict(ping_interval=15, ping_timeout=10, open_timeout=15, close_timeout=5, max_size=1024 * 1024)

# Hard per-tool deadline (seconds). Exceeding returns JSON-RPC error and kills the hung child.
TOOL_DEADLINE_S = float(os.environ.get("MCP_TOOL_DEADLINE_S", "8"))

async def connect_with_retry(uri, target):
    """Connect to WebSocket server with retry mechanism for a given server target."""
    reconnect_attempt = 0
    backoff = INITIAL_BACKOFF
    while True:
        state = {"connected": False}  # Infinite reconnection
        try:
            if reconnect_attempt > 0:
                logger.info(f"[{target}] Waiting {backoff}s before reconnection attempt {reconnect_attempt}...")
                await asyncio.sleep(backoff)

            # Attempt to connect
            await connect_to_server(uri, target, state)

        except Exception as e:
            if state["connected"]:
                # Last session was established, so start backoff fresh
                reconnect_attempt = 0
                backoff = INITIAL_BACKOFF
            reconnect_attempt += 1
            logger.warning(f"[{target}] Connection closed (attempt {reconnect_attempt}): {e}")
            # Calculate wait time for next reconnection (exponential backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)

async def connect_to_server(uri, target, state):
    """Connect to WebSocket server and pipe stdio for the given server target."""
    try:
        logger.info(f"[{target}] Connecting to WebSocket server...")
        async with websockets.connect(uri, **WS_OPTS) as websocket:
            state["connected"] = True
            logger.info(f"[{target}] Successfully connected to WebSocket server")
            pending_calls = {}  # JSON-RPC id -> (tool name, start time)

            cmd, env = build_server_command(target)
            # Ensure child stdio is line-buffered even when not a TTY
            env = dict(env)
            env.setdefault("PYTHONUNBUFFERED", "1")

            process_holder = {"proc": None}
            # Cache Xiaozhi initialize so in-place respawn after deadline can re-handshake
            session_state = {"init_message": None, "initialized": False}
            restart_lock = asyncio.Lock()

            def spawn_child(replay_session: bool = False):
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    encoding="utf-8",
                    text=True,
                    bufsize=1,
                    env=env,
                )
                logger.info(f"[{target}] Started server process: {' '.join(cmd)}")
                # Replay BEFORE publishing to process_holder so the async stdout
                # reader cannot race with initialize drain.
                if replay_session and session_state.get("init_message"):
                    try:
                        proc.stdin.write(session_state["init_message"] + "\n")
                        proc.stdin.flush()
                        deadline = time.monotonic() + 5
                        while time.monotonic() < deadline:
                            line = proc.stdout.readline()
                            if not line:
                                break
                            try:
                                msg = json.loads(line)
                            except Exception:
                                continue
                            if isinstance(msg, dict) and msg.get("id") is not None:
                                break
                        if session_state.get("initialized"):
                            proc.stdin.write(
                                json.dumps(
                                    {"jsonrpc": "2.0", "method": "notifications/initialized"}
                                )
                                + "\n"
                            )
                            proc.stdin.flush()
                        logger.info(f"[{target}] Replayed MCP initialize to respawned child")
                    except Exception as e:
                        logger.error(f"[{target}] Failed to replay initialize: {e}")
                process_holder["proc"] = proc
                return proc

            spawn_child(replay_session=False)

            await asyncio.gather(
                pipe_websocket_to_process(
                    websocket, process_holder, target, pending_calls, session_state
                ),
                pipe_process_to_websocket(websocket, process_holder, target, pending_calls),
                pipe_process_stderr_to_terminal(process_holder, target),
                watch_tool_deadlines(
                    websocket, process_holder, target, pending_calls, spawn_child, restart_lock
                ),
            )
    except websockets.exceptions.ConnectionClosed as e:
        logger.error(f"[{target}] WebSocket connection closed: {e}")
        raise
    except Exception as e:
        logger.error(f"[{target}] Connection error: {e}")
        raise
    finally:
        try:
            proc = process_holder.get("proc")
        except Exception:
            proc = None
        if proc is not None and proc.poll() is None:
            logger.info(f"[{target}] Terminating server process")
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            logger.info(f"[{target}] Server process terminated")


def _start_line_reader(stream, loop, out_queue, label):
    """Dedicated thread per stream — never use asyncio.to_thread(readline) across many servers
    (default executor starvation leaves tool responses unread until the hard deadline)."""
    def _run():
        try:
            while True:
                line = stream.readline()
                if not line:
                    break
                asyncio.run_coroutine_threadsafe(out_queue.put(line), loop)
        except Exception as e:
            logger.error(f"{label} line reader error: {e}")
        finally:
            try:
                asyncio.run_coroutine_threadsafe(out_queue.put(None), loop)
            except Exception:
                pass
    t = threading.Thread(target=_run, name=label, daemon=True)
    t.start()
    return t


async def watch_tool_deadlines(websocket, process_holder, target, pending_calls, spawn_child, restart_lock):
    """Fail loudly if any tools/call exceeds TOOL_DEADLINE_S; restart child in place (keep WS)."""
    try:
        while True:
            await asyncio.sleep(0.2)
            proc = process_holder.get("proc")
            if proc is None:
                continue
            now = time.monotonic()
            overdue = [
                (cid, tool, started)
                for cid, (tool, started) in list(pending_calls.items())
                if now - started >= TOOL_DEADLINE_S
            ]
            if not overdue:
                continue
            async with restart_lock:
                # re-check under lock
                now = time.monotonic()
                overdue = [
                    (cid, tool, started)
                    for cid, (tool, started) in list(pending_calls.items())
                    if now - started >= TOOL_DEADLINE_S
                ]
                if not overdue:
                    continue
                for cid, tool, started in overdue:
                    pending_calls.pop(cid, None)
                    elapsed_ms = (now - started) * 1000
                    err = {
                        "jsonrpc": "2.0",
                        "id": cid,
                        "error": {
                            "code": -32000,
                            "message": (
                                f"tool {tool!r} exceeded {TOOL_DEADLINE_S:.0f}s hard deadline "
                                f"(elapsed_ms={elapsed_ms:.0f})"
                            ),
                        },
                    }
                    payload = json.dumps(err, ensure_ascii=False)
                    logger.error(
                        f"[{target}] tools/call DEADLINE tool={tool} id={cid} "
                        f"elapsed_ms={elapsed_ms:.0f} limit_s={TOOL_DEADLINE_S:.0f}"
                    )
                    try:
                        await websocket.send(payload)
                    except Exception as e:
                        logger.error(f"[{target}] failed to send deadline error: {e}")
                # Kill hung child and respawn off the event loop (replay uses blocking readline).
                # Keep the WebSocket so Xiaozhi does not 4004/reconnect.
                def _kill_and_respawn():
                    old = process_holder.get("proc")
                    if old is not None and old.poll() is None:
                        try:
                            old.kill()
                        except Exception:
                            pass
                        try:
                            old.wait(timeout=2)
                        except Exception:
                            pass
                    spawn_child(replay_session=True)

                try:
                    await asyncio.to_thread(_kill_and_respawn)
                    logger.info(f"[{target}] Respawned server process after deadline (WS kept)")
                except Exception as e:
                    logger.error(f"[{target}] Failed to respawn after deadline: {e}")
                    raise
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(f"[{target}] Error in tool deadline watcher: {e}")
        raise


async def pipe_websocket_to_process(websocket, process_holder, target, pending_calls, session_state):
    """Read data from WebSocket and write to process stdin"""
    try:
        while True:
            message = await websocket.recv()
            logger.debug(f"[{target}] << {str(message)[:120]}...")

            if isinstance(message, bytes):
                message = message.decode("utf-8")
            # Normalize: Xiaozhi may already include a trailing newline; never send blank lines
            message = message.strip()
            if not message:
                continue
            msg = json.loads(message)
            if isinstance(msg, dict) and msg.get("method") == "initialize":
                session_state["init_message"] = message
                session_state["initialized"] = False
            elif isinstance(msg, dict) and msg.get("method") == "notifications/initialized":
                session_state["initialized"] = True
            if isinstance(msg, dict) and msg.get("method") == "tools/call":
                tool = msg["params"]["name"]
                pending_calls[msg["id"]] = (tool, time.monotonic())
                logger.info(f"[{target}] tools/call start tool={tool} id={msg['id']}")
            # Write one NDJSON line; no retry: fail loudly if child is gone
            proc = process_holder.get("proc")
            if proc is None or proc.stdin is None or proc.poll() is not None:
                raise RuntimeError(f"[{target}] child process not available for stdin write")
            proc.stdin.write(message + "\n")
            proc.stdin.flush()
    except Exception as e:
        logger.error(f"[{target}] Error in WebSocket to process pipe: {e}")
        raise
    finally:
        proc = process_holder.get("proc")
        if proc is not None and proc.stdin and not proc.stdin.closed:
            try:
                proc.stdin.close()
            except Exception:
                pass


_PYD_MSG = {"int_parsing": "must be a number", "float_parsing": "must be a number", "int_from_float": "must be a whole number",
            "bool_parsing": "must be true or false", "string_type": "must be text", "missing_argument": "is required"}


def _speakable_validation(text: str) -> str | None:
    """Turn a pydantic 'validation error for call[...]' into a short spoken error, no links."""
    if "validation error for call[" not in text:
        return None
    lines = text.splitlines()[1:]
    out = []
    for i, ln in enumerate(lines):
        if ln and not ln.startswith(" ") and i + 1 < len(lines):
            m = re.search(r"\[type=([a-z_]+)", lines[i + 1])
            out.append(f"{ln.strip()} {_PYD_MSG.get(m.group(1) if m else '', 'has the wrong type')}")
    return "Error: " + ("; ".join(out) or "wrong arguments") + "."


def _slim_payload(payload: str) -> str:
    """Drop structuredContent (duplicate of text content) and tool outputSchema to keep Xiaozhi frames small."""
    try:
        msg = json.loads(payload)
    except ValueError:
        return payload
    if not isinstance(msg, dict) or not isinstance(msg.get("result"), dict):
        return payload
    res = msg["result"]
    changed = False
    if "structuredContent" in res:
        res.pop("structuredContent"); changed = True
    if "_meta" in res:
        res.pop("_meta"); changed = True
    if res.get("isError"):
        for c in res.get("content") or []:
            if isinstance(c, dict) and isinstance(c.get("text"), str):
                short = _speakable_validation(c["text"])
                if short:
                    c["text"] = short; changed = True
    for t in res.get("tools") or []:
        if isinstance(t, dict) and "outputSchema" in t:
            t.pop("outputSchema"); changed = True
    return json.dumps(msg, ensure_ascii=False) if changed else payload


async def pipe_process_to_websocket(websocket, process_holder, target, pending_calls):
    """Read process stdout via a dedicated thread; forward lines to WebSocket."""
    loop = asyncio.get_running_loop()
    try:
        while True:
            proc = process_holder.get("proc")
            if proc is None or proc.stdout is None:
                await asyncio.sleep(0.05)
                continue
            # Attach a fresh reader to the current child; if child is respawned, reattach
            q = asyncio.Queue()
            attached = proc
            _start_line_reader(proc.stdout, loop, q, f"[{target}]-stdout")
            while process_holder.get("proc") is attached:
                line = await q.get()
                if line is None:
                    # EOF on this child
                    if process_holder.get("proc") is attached:
                        logger.info(f"[{target}] Process has ended output")
                        # Wait briefly for respawn (deadline path) instead of killing the WS
                        for _ in range(50):
                            await asyncio.sleep(0.1)
                            if process_holder.get("proc") is not attached:
                                break
                        else:
                            break
                    break
                data = line
                # Send clean WS frame (no trailing newline) — Xiaozhi expects one JSON object per message
                payload = data.rstrip("\r\n")
                if not payload:
                    continue
                payload = _slim_payload(payload)
                logger.debug(f"[{target}] >> {payload[:120]}...")
                await websocket.send(payload)
                try:
                    resp = json.loads(payload)
                except Exception:
                    continue
                if isinstance(resp, dict) and resp.get("id") in pending_calls:
                    tool, started = pending_calls.pop(resp["id"])
                    is_error = "error" in resp or bool((resp.get("result") or {}).get("isError"))
                    logger.info(
                        f"[{target}] tools/call done tool={tool} id={resp['id']} "
                        f"duration_ms={(time.monotonic() - started) * 1000:.0f} "
                        f"bytes={len(payload.encode('utf-8'))} isError={is_error}"
                    )
            if process_holder.get("proc") is attached and (attached.poll() is not None):
                # Child died and was not respawned — end pipe (triggers WS reconnect)
                break
    except Exception as e:
        logger.error(f"[{target}] Error in process to WebSocket pipe: {e}")
        raise


async def pipe_process_stderr_to_terminal(process_holder, target):
    """Read process stderr via a dedicated thread; print to terminal."""
    loop = asyncio.get_running_loop()
    try:
        while True:
            proc = process_holder.get("proc")
            if proc is None or proc.stderr is None:
                await asyncio.sleep(0.05)
                continue
            q = asyncio.Queue()
            attached = proc
            _start_line_reader(proc.stderr, loop, q, f"[{target}]-stderr")
            while process_holder.get("proc") is attached:
                line = await q.get()
                if line is None:
                    if process_holder.get("proc") is attached:
                        logger.info(f"[{target}] Process has ended stderr output")
                        for _ in range(50):
                            await asyncio.sleep(0.1)
                            if process_holder.get("proc") is not attached:
                                break
                        else:
                            break
                    break
                sys.stderr.write(line)
                sys.stderr.flush()
            if process_holder.get("proc") is attached and (attached.poll() is not None):
                break
    except Exception as e:
        logger.error(f"[{target}] Error in process stderr pipe: {e}")
        raise


def signal_handler(sig, frame):
    """Handle interrupt signals"""
    logger.info("Received interrupt signal, shutting down...")
    sys.exit(0)

def load_config():
    """Load JSON config from $MCP_CONFIG or ./mcp_config.json. Return dict or {}."""
    path = os.environ.get("MCP_CONFIG") or os.path.join(os.getcwd(), "mcp_config.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load config {path}: {e}")
        return {}


def build_server_command(target=None):
    """Build [cmd,...] and env for the server process for a given target.

    Priority:
    - If target matches a server in config.mcpServers: use its definition
    - Else: treat target as a Python script path (back-compat)
    If target is None, read from sys.argv[1].
    """
    if target is None:
        assert len(sys.argv) >= 2, "missing server name or script path"
        target = sys.argv[1]
    cfg = load_config()
    servers = cfg.get("mcpServers", {}) if isinstance(cfg, dict) else {}

    if target in servers:
        entry = servers[target] or {}
        if entry.get("disabled"):
            raise RuntimeError(f"Server '{target}' is disabled in config")
        typ = (entry.get("type") or entry.get("transportType") or "stdio").lower()

        # environment for child process
        child_env = os.environ.copy()
        for k, v in (entry.get("env") or {}).items():
            child_env[str(k)] = str(v)

        if typ == "stdio":
            command = entry.get("command")
            args = entry.get("args") or []
            if not command:
                raise RuntimeError(f"Server '{target}' is missing 'command'")
            return [command, *args], child_env

        if typ in ("sse", "http", "streamablehttp"):
            url = entry.get("url")
            if not url:
                raise RuntimeError(f"Server '{target}' (type {typ}) is missing 'url'")
            # Unified approach: always use current Python to run mcp-proxy module
            cmd = [sys.executable, "-m", "mcp_proxy"]
            if typ in ("http", "streamablehttp"):
                cmd += ["--transport", "streamablehttp"]
            # optional headers: {"Authorization": "Bearer xxx"}
            headers = entry.get("headers") or {}
            for hk, hv in headers.items():
                cmd += ["-H", hk, str(hv)]
            cmd.append(url)
            return cmd, child_env

        raise RuntimeError(f"Unsupported server type: {typ}")

    # Fallback to script path (back-compat)
    script_path = target
    if not os.path.exists(script_path):
        raise RuntimeError(
            f"'{target}' is neither a configured server nor an existing script"
        )
    return [sys.executable, script_path], os.environ.copy()

if __name__ == "__main__":
    # Register signal handler
    signal.signal(signal.SIGINT, signal_handler)
    
    # Get token from environment variable or command line arguments
    endpoint_url = os.environ.get('MCP_ENDPOINT')
    if not endpoint_url:
        logger.error("Please set the `MCP_ENDPOINT` environment variable")
        sys.exit(1)
    
    # Determine target: default to all if no arg; single target otherwise
    target_arg = sys.argv[1] if len(sys.argv) >= 2 else None

    async def _main():
        if not target_arg:
            cfg = load_config()
            servers_cfg = (cfg.get("mcpServers") or {})
            all_servers = list(servers_cfg.keys())
            enabled = [name for name, entry in servers_cfg.items() if not (entry or {}).get("disabled")]
            skipped = [name for name in all_servers if name not in enabled]
            if skipped:
                logger.info(f"Skipping disabled servers: {', '.join(skipped)}")
            if not enabled:
                raise RuntimeError("No enabled mcpServers found in config")
            logger.info(f"Starting servers: {', '.join(enabled)}")
            tasks = [asyncio.create_task(connect_with_retry(endpoint_url, t)) for t in enabled]
            # Run all forever; if any crashes it will auto-retry inside
            await asyncio.gather(*tasks)
        else:
            if os.path.exists(target_arg):
                await connect_with_retry(endpoint_url, target_arg)
            else:
                logger.error("Argument must be a local Python script path. To run configured servers, run without arguments.")
                sys.exit(1)

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        logger.info("Program interrupted by user")
    except Exception as e:
        logger.error(f"Program execution error: {e}")