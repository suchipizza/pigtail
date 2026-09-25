"""SQLite-backed result cache and usage ledger (R15.4, R15.5).

Local files under PIGTAIL_DATA_DIR (gitignored). Moves to Postgres with the capture schema (M1);
see ops/DECISIONS.md ADR-004.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_cache (
    key TEXT PRIMARY KEY,
    output_json TEXT NOT NULL,
    model TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    backend TEXT NOT NULL,
    job TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_id TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    status TEXT NOT NULL,          -- ok | cached | limit | error | invalid_output
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS llm_pause (
    backend TEXT PRIMARY KEY,
    paused_until TEXT NOT NULL,
    reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_pause_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    backend TEXT NOT NULL,
    paused_until TEXT NOT NULL,
    reason TEXT NOT NULL
);
"""


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class UsageRow:
    backend: str
    job: str
    model: str
    prompt_id: str
    prompt_version: str
    status: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class LLMStore:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.executescript(_SCHEMA)

    # --- cache ---------------------------------------------------------------
    def cache_get(self, key: str) -> tuple[dict[str, Any], str] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT output_json, model FROM llm_cache WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        return json.loads(row[0]), row[1]

    def cache_put(self, key: str, output: dict[str, Any], model: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO llm_cache VALUES (?, ?, ?, ?)",
                (key, json.dumps(output, sort_keys=True), model, utcnow().isoformat()),
            )

    # --- ledger --------------------------------------------------------------
    def record(self, row: UsageRow) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO llm_usage (ts, backend, job, model, prompt_id, prompt_version, status,"
                " input_tokens, output_tokens, cost_usd) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    utcnow().isoformat(),
                    row.backend,
                    row.job,
                    row.model,
                    row.prompt_id,
                    row.prompt_version,
                    row.status,
                    row.input_tokens,
                    row.output_tokens,
                    row.cost_usd,
                ),
            )

    def summary(self) -> dict[str, dict[str, float]]:
        """Per-backend totals.

        `sessions` = backend calls made (any outcome except cache hits and limit hits). For the
        subscription backend `cost_usd` is a list-price equivalent, not spend.
        """
        with self._lock:
            rows = self._db.execute(
                "SELECT backend,"
                " SUM(status IN ('ok', 'invalid_output', 'error')),"
                " SUM(status = 'cached'), SUM(status = 'limit'),"
                " SUM(status IN ('error', 'invalid_output')),"
                " SUM(input_tokens), SUM(output_tokens), SUM(cost_usd)"
                " FROM llm_usage GROUP BY backend"
            ).fetchall()
        keys = (
            "sessions",
            "cache_hits",
            "limit_hits",
            "errors",
            "input_tokens",
            "output_tokens",
            "cost_usd",
        )
        return {r[0]: {k: float(v or 0) for k, v in zip(keys, r[1:], strict=True)} for r in rows}

    # --- pause state (R15.5) -------------------------------------------------
    def pause(self, backend: str, until: datetime, reason: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO llm_pause VALUES (?, ?, ?)",
                (backend, until.isoformat(), reason),
            )
            self._db.execute(
                "INSERT INTO llm_pause_log (ts, backend, paused_until, reason) VALUES (?,?,?,?)",
                (utcnow().isoformat(), backend, until.isoformat(), reason),
            )

    def paused_until(self, backend: str, now: datetime | None = None) -> datetime | None:
        with self._lock:
            row = self._db.execute(
                "SELECT paused_until FROM llm_pause WHERE backend = ?", (backend,)
            ).fetchone()
        if row is None:
            return None
        until = datetime.fromisoformat(row[0])
        return until if until > (now or utcnow()) else None

    def pause_log(self) -> list[dict[str, str]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT ts, backend, paused_until, reason FROM llm_pause_log ORDER BY id"
            ).fetchall()
        return [
            dict(zip(("ts", "backend", "paused_until", "reason"), r, strict=True)) for r in rows
        ]
