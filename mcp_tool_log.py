"""Shared stderr logging for the bridge's MCP tools (same line format as MCP_PIPE).

Each tool call logs one line: tool, args, row ids touched, duration and outcome. Errors re-raise so the
caller sees them.
"""

from __future__ import annotations

import contextvars
import functools
import json
import logging
import sys
import time

_ids: contextvars.ContextVar[list] = contextvars.ContextVar("mcp_tool_ids")


def get_logger(name: str) -> logging.Logger:
    log = logging.getLogger(name)
    if not log.handlers:
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
        log.propagate = False
    return log


def note(*row_ids) -> None:
    """Record row ids for the current tool call's log line (works inside copy_context threads)."""
    ids = _ids.get(None)
    if ids is not None:
        ids.extend(str(i) for i in row_ids)


def logged(log: logging.Logger):
    def deco(fn):
        @functools.wraps(fn)
        def wrap(*args, **kwargs):
            ids: list = []
            tok = _ids.set(ids)
            t0 = time.monotonic()
            a = json.dumps(kwargs or list(args), ensure_ascii=False, default=str)
            try:
                out = fn(*args, **kwargs)
            except BaseException as e:
                ms = (time.monotonic() - t0) * 1000
                log.error(f"tool={fn.__name__} args={a} ids={ids} ms={ms:.0f} error={type(e).__name__}: {e}")
                raise
            finally:
                _ids.reset(tok)
            ms = (time.monotonic() - t0) * 1000
            log.info(f"tool={fn.__name__} args={a} ids={ids} ms={ms:.0f} ok bytes={len(str(out).encode())}")
            return out
        return wrap
    return deco
