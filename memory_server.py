"""Live-learning memory MCP: Stack-chan learns facts Mo tells her and recalls them later.

Backed by the Hindsight container (compose service `hindsight`, data in the stackchan-hindsight-data
volume). Hindsight extracts facts with Mo's local Ollama model and embeds/reranks them with its own
local models; recall needs no LLM. One path, no fallbacks: if Hindsight is down the server refuses to
start, and failed learning operations are reported as errors.
Tools: memory_learn, memory_recall, memory_status.
"""

from __future__ import annotations

import os
import re
import threading
import time
from datetime import datetime, timezone

import httpx
from fastmcp import FastMCP

from mcp_tool_log import get_logger, logged, note

mcp = FastMCP("Memory")
log = get_logger("MEMORY")

URL = os.environ["HINDSIGHT_URL"].rstrip("/")
BANK = os.environ["MEMORY_BANK"]
if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", BANK):
    raise SystemExit(f"MEMORY_BANK {BANK!r} is not a valid bank id")
API = f"{URL}/v1/default/banks/{BANK}"
LEARN_WAIT_S = float(os.environ.get("MEMORY_LEARN_WAIT_S", "6"))
TIMEOUT = httpx.Timeout(connect=3, read=6, write=3, pool=3)
RECALL_TOP = 3
TEXT_MAX = 600
CONTEXT = "Mo is teaching Stack-chan (his desk robot) something to remember"
U = "Stack-chan's own long-term memory of things Mo has taught her (local Hindsight memory)."
_client = httpx.Client(timeout=TIMEOUT, headers={"User-Agent": "stackchan-memory/1.0"})
_llm_problem: str | None = None


def _req(method: str, path: str, **kw) -> dict:
    try:
        r = _client.request(method, API + path, **kw)
    except httpx.HTTPError as e:
        raise RuntimeError(f"Hindsight memory is unreachable ({type(e).__name__})") from e
    if r.status_code >= 400:
        raise RuntimeError(f"Hindsight returned HTTP {r.status_code}: {r.text[:200]}")
    return r.json()


def _clean(text: str) -> str:
    return text.split(" | ")[0].strip().rstrip(".") + "."


def _check_llm() -> None:
    """Hindsight only warns when its LLM is unreachable, so check it ourselves (in the background,
    because a busy shared Ollama can take a while). Unreachable or misconfigured = every learn fails."""
    global _llm_problem
    try:
        ops = _req("POST", "/health/llm", timeout=httpx.Timeout(30, connect=3))["operations"]
        bad = [o for o in ops if not o.get("ok") and o.get("status") != "timeout"]
        if bad:
            _llm_problem = f"Hindsight's LLM failed its health check: {bad[0].get('status')} {str(bad[0].get('error') or '')[:150]}"
            log.error(_llm_problem)
        elif any(not o.get("ok") for o in ops):
            log.warning("Hindsight LLM reachable but busy (health check timed out); learning will be slow")
        else:
            log.info(f"Hindsight LLM OK ({ops[0].get('latency_ms', 0):.0f} ms)")
    except Exception as e:  # noqa: BLE001 - recorded and raised by the tools
        _llm_problem = f"Hindsight's LLM health check failed: {e}"
        log.error(_llm_problem)


def _pending() -> int:
    """Queued + in-progress learning, straight from the operations table (not the cached bank stats)."""
    return sum(int(_req("GET", "/operations", params={"status": st, "type": "retain", "limit": 1}).get("total") or 0)
               for st in ("pending", "processing"))


@mcp.tool(name="memory_learn", description=f"{U} Use when Mo says remember / learn / note this / don't forget, or tells you a new fact about himself, people, places or anything he wants you to know later. text = the fact in one or two plain sentences, written from Mo's point of view (e.g. 'My sister Aisha's favourite flower is the blue iris').")
@logged(log)
def memory_learn(text: str) -> str:
    text = " ".join(str(text or "").split())
    if not text:
        raise ValueError("pass the fact to learn as text")
    if len(text) > 2000:
        raise ValueError("that's too long to learn in one go; split it into shorter facts")
    if _llm_problem:
        raise RuntimeError(_llm_problem)
    item = {"content": f"Mo: {text}", "context": CONTEXT, "timestamp": datetime.now(timezone.utc).isoformat()}
    op = _req("POST", "/memories", json={"items": [item], "async": True})["operation_id"]
    note(op)
    deadline = time.monotonic() + LEARN_WAIT_S
    while True:
        s = _req("GET", f"/operations/{op}")
        status = s.get("status")
        if status == "completed":
            return f"Learned it. I'll remember: {text}"
        if status in ("failed", "cancelled"):
            raise RuntimeError(f"learning failed: {s.get('error_message') or status}")
        if time.monotonic() >= deadline:
            return f"Okay, I'm learning that now: {text.rstrip('.!')}. Give me a minute before asking me to recall it."
        time.sleep(0.5)


@mcp.tool(name="memory_recall", description=f"{U} Use when Mo asks what you remember or know about something he told you before (people, pets, preferences, plans, facts). Returns up to {RECALL_TOP} remembered facts, most relevant first; if none actually answer the question, say you haven't learned that yet. query = what to look up, e.g. 'my cat' or 'Aisha's favourite flower'.")
@logged(log)
def memory_recall(query: str) -> str:
    query = " ".join(str(query or "").split())
    if not query:
        raise ValueError("pass what to recall as query")
    res = _req("POST", "/memories/recall", json={"query": query, "budget": "low", "max_tokens": 800, "types": ["world", "experience"]})
    facts: list[str] = []
    for r in res.get("results") or []:
        t = _clean(r.get("text") or "")
        if len(t) > 1 and t not in facts:
            facts.append(t)
            note(r.get("id"))
        if len(facts) >= RECALL_TOP:
            break
    pending = _pending()
    tail = f" (I'm still learning {pending} more thing{'s' if pending != 1 else ''}.)" if pending else ""
    if not facts:
        return f"I haven't learned anything about {query} yet.{tail}"
    out = "Here's what I remember, most relevant first: " + " ".join(f"{i}. {f}" for i, f in enumerate(facts, 1)) + tail
    return out[:TEXT_MAX]


@mcp.tool(name="memory_status", description=f"{U} Use when Mo asks how much you've learned, whether you've finished learning something, or if your memory is working.")
@logged(log)
def memory_status() -> str:
    s = _req("GET", "/stats")
    facts = sum(v for k, v in (s.get("nodes_by_fact_type") or {}).items() if k in ("world", "experience"))
    pending, failed = _pending(), int(s.get("failed_operations") or 0)
    msg = f"I've learned {facts} fact{'s' if facts != 1 else ''} from {s.get('total_documents', 0)} thing{'s' if s.get('total_documents', 0) != 1 else ''} you've told me."
    msg += f" {pending} still being learned." if pending else " Nothing waiting to be learned."
    if failed:
        ops = _req("GET", "/operations", params={"status": "failed", "limit": 1}).get("operations") or []
        why = (ops[0].get("error_message") or "unknown error")[:150] if ops else "unknown error"
        msg += f" {failed} failed to learn; latest error: {why}."
    if _llm_problem:
        msg += f" Problem: {_llm_problem}."
    return msg


if __name__ == "__main__":
    try:
        h = _client.get(f"{URL}/health")
        h.raise_for_status()
    except httpx.HTTPError as e:
        log.error(f"Hindsight memory is unreachable at startup ({type(e).__name__}); is the hindsight container running? Exiting.")
        raise SystemExit(1) from e
    if _client.get(API + "/stats").status_code == 404:  # a fresh bank: create it so status/recall work before the first learn
        _req("PUT", "", json={})
        log.info(f"created memory bank {BANK}")
    log.info(f"Hindsight OK, bank={BANK}")
    threading.Thread(target=_check_llm, daemon=True).start()
    mcp.run(transport="stdio", show_banner=False)
