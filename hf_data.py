"""On-demand reads of Mo's Hugging Face datasets. Nothing is loaded or kept: each HFTable is a set of DuckDB views over
the dataset's remote parquet (pinned to the commit current when first used), so every query is a fresh HTTP range read.
DuckDB's remote-file cache is off (only HTTP metadata and parquet footers are kept) and its memory is capped, so no text
stays in RAM between calls. A query that cannot
reach Hugging Face, or runs past QUERY_TIMEOUT_S, fails with a spoken error (no fallback)."""
from __future__ import annotations

import contextlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

import duckdb

from mcp_tool_log import get_logger

QUERY_TIMEOUT_S = 7.0
MEMORY_LIMIT = os.environ.get("DUCKDB_MEMORY_LIMIT", "64MB")
log = get_logger("HF_DATA")
_db: duckdb.DuckDBPyConnection | None = None
_db_lock = threading.Lock()
TABLES: list["HFTable"] = []


def _database() -> duckdb.DuckDBPyConnection:
    global _db
    with _db_lock:
        if _db is None:
            db = duckdb.connect(config={"extension_directory": "/opt/duckdb/extensions", "memory_limit": MEMORY_LIMIT, "threads": 2,
                                        "temp_directory": "/tmp/duckdb"})  # the image is read-only; /tmp is a tmpfs
            db.execute("LOAD httpfs")
            db.execute("SET enable_external_file_cache = false")  # no file bytes (texts) kept between queries
            db.execute("SET enable_http_metadata_cache = true")  # only HEAD results and parquet footers are kept
            db.execute("SET parquet_metadata_cache = true")
            _db = db
        return _db


def hf_revision(repo: str, timeout: float = 5.0) -> str:
    """Current commit sha of a HF dataset repo; raises on any failure."""
    with urllib.request.urlopen(f"https://huggingface.co/api/datasets/{repo}/revision/main", timeout=timeout) as r:
        sha = json.load(r)["sha"]
    if not sha:
        raise RuntimeError(f"No revision sha for {repo}")
    return sha


class HFTable:
    """views: view name -> SELECT using {src} (the remote parquet) or a callable(src, columns) returning one.
    on_ready(q) runs once after the views exist (dataset checks, small metadata); its failure fails the call."""

    def __init__(self, repo: str, label: str, views: dict, on_ready=None):
        self.repo, self.label, self.views, self.on_ready = repo, label, views, on_ready
        self.schema = re.sub(r"\W", "_", repo.split("/")[-1])
        self.rev: str | None = None
        self._lock = threading.Lock()
        TABLES.append(self)

    def _ensure(self) -> None:
        with self._lock:
            if self.rev:
                return
            t0 = time.monotonic()
            rev = hf_revision(self.repo)
            src = f"read_parquet('hf://datasets/{self.repo}@{rev}/data/train.parquet')"
            cur = _database().cursor()
            try:
                cols = [d[0] for d in cur.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()]
                cur.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")
                for name, sel in self.views.items():
                    cur.execute(f"CREATE OR REPLACE VIEW {self.schema}.{name} AS " + (sel(src, cols) if callable(sel) else sel.format(src=src)))
            finally:
                cur.close()
            if self.on_ready:
                self.on_ready(self._run)
            self.rev = rev
            log.info(f"{self.repo}@{rev[:12]} ready (remote parquet, nothing cached) ms={(time.monotonic() - t0) * 1000:.0f}")

    def _run(self, sql: str, params=()) -> list:
        cur = _database().cursor()
        timer = threading.Timer(QUERY_TIMEOUT_S, cur.interrupt)
        try:
            cur.execute(f"USE {self.schema}")
            timer.start()
            return cur.execute(sql, list(params)).fetchall()
        finally:
            timer.cancel()
            cur.close()

    @contextlib.contextmanager
    def _spoken_errors(self):
        try:
            yield
        except duckdb.InterruptException:
            raise TimeoutError(f"The {self.label} data on Hugging Face took longer than {QUERY_TIMEOUT_S:.0f} seconds to answer. Try again.") from None
        except (duckdb.IOException, duckdb.HTTPException, urllib.error.URLError, TimeoutError, ConnectionError) as e:
            log.error(f"{self.repo}: {type(e).__name__}: {str(e)[:200]}")
            raise RuntimeError(f"I can't reach the {self.label} data on Hugging Face right now. Try again in a minute.") from e

    def ensure(self) -> None:
        """Create the views (and run on_ready) if not done yet."""
        with self._spoken_errors():
            self._ensure()

    def q(self, sql: str, params=()) -> list:
        with self._spoken_errors():
            self._ensure()
            return self._run(sql, params)


def warm_all() -> None:
    """Create every table's views once at startup (in a thread) so first calls are quick; failures are logged loudly."""
    for t in TABLES:
        try:
            t.ensure()
        except Exception as e:
            log.error(f"startup check failed for {t.repo}: {type(e).__name__}: {e}")
