"""Live-learning memory MCP: Stack-chan learns facts Mo tells her and recalls them later.

Backed by the Hindsight container (compose service `hindsight`, data in the stackchan-hindsight-data
volume). Hindsight extracts facts with Mo's local Ollama model and embeds/reranks them with its own
local models; recall needs no LLM. One path, no fallbacks: if Hindsight is down the server refuses to
start, and failed learning operations are reported as errors.
Tools: learn_this (ingest), learn_recall (fetch), learn_status, learn_reflect, learn_reflection.

Reflection is Hindsight's agentic reflect (search memories + observations, then reason) on the local 7B,
which takes minutes, far past the 8 s tool deadline. learn_reflect queues a job in a small JSON file on
the bookmarks volume and starts one detached worker process (`memory_server.py --reflect-worker`, one per
bank, guarded by a lock) that waits for pending learning to settle, runs the reflect and records the
answer or the error. learn_reflection reads that file. Jobs survive restarts: a job left running by a
dead worker is run again by the next worker.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
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
U = "Stack-chan's ONLY real long-term memory of what Mo teaches her."
STATE_DIR = os.environ.get("MEMORY_STATE_DIR", "/data/bookmarks")
JOBS_FILE = os.path.join(STATE_DIR, f"reflections-{BANK}.json")
WORKER_LOG = f"/tmp/memory-reflect-{BANK}.log"
REFLECT_HTTP_TIMEOUT = httpx.Timeout(connect=5, read=2000, write=10, pool=10)  # Hindsight's own wall timeout is 1800 s
SETTLE_MAX_S = 1200  # wait this long at most for pending learning/consolidation before reflecting anyway
KEEP_DONE = 10
ANSWER_MAX = 650
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


@mcp.tool(name="learn_this", description=f"{U} Call this whenever Mo says learn that, remember, note this or don't forget, or tells you something to know later. Nothing is kept unless you call it, so never just say you remembered. fact = what to learn, in Mo's words (e.g. My sister Aisha loves blue irises).")
@logged(log)
def learn_this(fact: str) -> str:
    text = " ".join(str(fact or "").split())
    if not text:
        raise ValueError("pass the fact to learn")
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


@mcp.tool(name="learn_recall", description=f"{U} Call this when Mo asks what have you learnt about, what do you know about, do you remember, or anything he taught you before; never answer from guesswork. Gives up to {RECALL_TOP} learnt facts, best first; if none fit, say you have not learnt it. topic = what to look up.")
@logged(log)
def learn_recall(topic: str) -> str:
    query = " ".join(str(topic or "").split())
    if not query:
        raise ValueError("pass the topic to recall")
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
        return f"I haven't learnt anything about {query} yet.{tail}"
    out = "Here's what I remember, most relevant first: " + " ".join(f"{i}. {f}" for i, f in enumerate(facts, 1)) + tail
    return out[:TEXT_MAX]


@mcp.tool(name="learn_status", description=f"{U} How much you have learnt, and whether learning is still running or failed.")
@logged(log)
def learn_status() -> str:
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


def _now() -> float:
    return time.time()


def _locked(fn):
    """Run fn(jobs) under an exclusive lock; if it returns True the jobs list is written back atomically."""
    with open(JOBS_FILE + ".lock", "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            with open(JOBS_FILE) as f:
                jobs = json.load(f)["jobs"]
        except FileNotFoundError:
            jobs = []
        out = fn(jobs)
        if out is not None and out[0]:
            done = [j for j in jobs if j["status"] in ("done", "failed")]
            keep = {id(j) for j in done[-KEEP_DONE:]}
            jobs[:] = [j for j in jobs if j["status"] not in ("done", "failed") or id(j) in keep]
            fd, tmp = tempfile.mkstemp(dir=STATE_DIR, prefix=".reflections-")
            with os.fdopen(fd, "w") as f:
                json.dump({"bank": BANK, "jobs": jobs}, f, ensure_ascii=False, indent=1)
            os.replace(tmp, JOBS_FILE)
        return out[1] if out is not None else None


def _worker_alive() -> bool:
    with open(JOBS_FILE + ".worker", "a") as lk:
        try:
            fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(lk, fcntl.LOCK_UN)
        return False


def _spawn_worker() -> None:
    if _worker_alive():
        return
    with open(WORKER_LOG, "a") as logf:
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "--reflect-worker"], stdin=subprocess.DEVNULL,
                         stdout=logf, stderr=logf, start_new_session=True, close_fds=True)
    log.info("started reflect worker")


def _open_jobs(jobs: list) -> list:
    return [j for j in jobs if j["status"] in ("queued", "running")]


def _mins(t: float) -> int:
    return max(1, round((_now() - t) / 60))


def _speakable(text: str) -> str:
    text = re.sub(r"[*_#`>|]+", "", text)
    text = " ".join(text.split())
    if len(text) > ANSWER_MAX:
        cut = text[:ANSWER_MAX]
        text = cut[: cut.rfind(". ") + 1] if ". " in cut else cut + "..."
    return text


def _busy_learning() -> int:
    return sum(int(_req("GET", "/operations", params={"status": st, "limit": 1}).get("total") or 0)
               for st in ("pending", "processing"))


def _reflect_worker() -> None:
    lk = open(JOBS_FILE + ".worker", "a")
    try:
        fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return  # another worker already owns this bank's queue
    while True:
        def take(jobs):
            js = _open_jobs(jobs)
            if not js:
                return False, None
            j = js[0]
            j["resumed"] = j["status"] == "running"  # we hold the worker lock, so a running job was orphaned
            j["status"], j["started"] = "running", _now()
            return True, dict(j)
        job = _locked(take)
        if job is None:
            return
        log.info(f"reflecting on job {job['id']}{' (resumed)' if job['resumed'] else ''}: {job['question'][:80]}")
        try:
            t = _now()
            while _busy_learning() and _now() - t < SETTLE_MAX_S:  # let fresh facts and observations land first
                time.sleep(5)
            r = _client.post(API + "/reflect", json={"query": job["question"], "budget": "low", "max_tokens": 400},
                             timeout=REFLECT_HTTP_TIMEOUT)
            if r.status_code >= 400:
                raise RuntimeError(f"Hindsight reflect returned HTTP {r.status_code}: {r.text[:200]}")
            answer = _speakable(r.json().get("text") or "")
            if not answer:
                raise RuntimeError("Hindsight reflect returned an empty answer")
            result = {"status": "done", "answer": answer}
        except Exception as e:  # noqa: BLE001 - recorded on the job and raised by learn_reflection
            result = {"status": "failed", "error": f"{type(e).__name__}: {str(e)[:200]}"}
            log.error(f"reflect job {job['id']} failed: {result['error']}")

        def finish(jobs, jid=job["id"], result=result):
            for j in jobs:
                if j["id"] == jid:
                    j.update(result, finished=_now())
            return True, None
        _locked(finish)
        log.info(f"reflect job {job['id']} {result['status']} in {_now() - job['started']:.0f} s")


@mcp.tool(name="learn_reflect", description=f"{U} Starts thinking in the background (takes minutes) about a question using everything learnt, e.g. what gift suits Aisha. Later give the answer with learn_reflection.")
@logged(log)
def learn_reflect(question: str) -> str:
    question = " ".join(str(question or "").split())
    if not question:
        raise ValueError("pass the question to reflect on")
    if len(question) > 500:
        raise ValueError("that question is too long; ask it in a sentence or two")
    if _llm_problem:
        raise RuntimeError(_llm_problem)

    def add(jobs):
        open_ = _open_jobs(jobs)
        for j in open_:
            if j["question"].lower() == question.lower():
                return False, ("same", j)
        j = {"id": f"r{int(_now() * 1000)}", "question": question, "status": "queued", "created": _now()}
        jobs.append(j)
        return True, ("new", len(open_))
    kind, info = _locked(add)
    _spawn_worker()
    if kind == "same":
        return f"I'm already thinking about that, {_mins(info.get('started') or info['created'])} min so far. Ask me for my reflection in a few minutes."
    note(question[:40])
    ahead = f" after the {info} question{'s' if info != 1 else ''} I'm already on" if info else ""
    return f"I'm thinking about that now{ahead}; ask me for my reflection in a few minutes."


@mcp.tool(name="learn_reflection", description=f"{U} Gives the latest finished learn_reflect answer, or says you are still thinking. question = optional words to pick an earlier one.")
@logged(log)
def learn_reflection(question: str | None = None) -> str:
    q = " ".join(str(question or "").split()).lower()
    jobs = _locked(lambda jobs: (False, list(jobs)))
    if q:
        jobs = [j for j in jobs if q in j["question"].lower() or j["question"].lower() in q]
    if not jobs:
        return ("I haven't been asked to reflect on that yet." if q else "I haven't been asked to reflect on anything yet.") + " Ask me to reflect on something first."
    open_ = _open_jobs(jobs)
    if open_ and not _worker_alive():
        _spawn_worker()  # a restart killed the worker; the queued job is picked up again
    finished = [j for j in jobs if j["status"] in ("done", "failed")]
    j = finished[-1] if finished and (q or not open_ or finished[-1]["created"] > open_[-1]["created"]) else None
    if j is None:
        o = open_[-1]
        return f"Still thinking about '{o['question']}', {_mins(o.get('started') or o['created'])} min so far."
    note(j["id"])
    if j["status"] == "failed":
        raise RuntimeError(f"my reflection on '{j['question']}' failed: {j['error']}")
    secs = j["finished"] - j["created"]
    took = f"{secs:.0f} s" if secs < 90 else f"{secs / 60:.0f} min"
    more = f" I'm also still thinking about '{open_[-1]['question']}'." if open_ else ""
    return f"My reflection on '{j['question']}' (took {took}): {j['answer']}{more}"


if __name__ == "__main__":
    if sys.argv[1:] == ["--reflect-worker"]:
        _reflect_worker()
        sys.exit(0)
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
    try:  # reflection jobs live beside the bookmarks; refuse to start if that volume is not writable
        _locked(lambda jobs: (True, None))
    except OSError as e:
        raise SystemExit(f"reflection store {JOBS_FILE} is not writable: {e}") from e
    if _locked(lambda jobs: (False, _open_jobs(jobs))):
        _spawn_worker()  # resume reflections a restart interrupted
    threading.Thread(target=_check_llm, daemon=True).start()
    mcp.run(transport="stdio", show_banner=False)
