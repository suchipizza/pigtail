"""SQLite-backed result cache and usage ledger (R15.4, R15.5).

Local files under PIGTAIL_DATA_DIR (gitignored). Moves to Postgres with the capture schema (M1);
see ops/DECISIONS.md ADR-004.

Cache retention (DPIA CB-05): cached outputs can hold verbatim quoted spans and pseudonyms, so
each row has a `retention_class` (default `person_level_24m`) and expires `retention_days` after
`created_at` (default 730 = 24 months, `LLM_CACHE_RETENTION_DAYS`). Expired rows are never served
and are deleted by `purge_expired()` (wired into `pigtail retention purge`). Rows can be linked to
the evidence they were derived from (`llm_cache_evidence`); `purge_for_evidence()` deletes them
when that evidence is dropped or erased, so a cached output never outlives its source.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_cache (
    key TEXT PRIMARY KEY,
    output_json TEXT NOT NULL,
    model TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_cache_evidence (
    key TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    PRIMARY KEY (key, evidence_id)
);
CREATE INDEX IF NOT EXISTS llm_cache_evidence_ev_idx ON llm_cache_evidence (evidence_id);
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


DEFAULT_CACHE_RETENTION_DAYS = 730


def _ts(dt: datetime) -> str:
    """Timestamps are stored as UTC ISO 8601 text so that string order is time order."""
    return dt.astimezone(UTC).isoformat()


class LLMStore:
    def __init__(
        self,
        path: Path | str,
        *,
        retention_days: int = DEFAULT_CACHE_RETENTION_DAYS,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        if retention_days < 0:
            raise ValueError("retention_days must be >= 0")
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.executescript(_SCHEMA)
        self._upgrade()
        self.retention_days = retention_days
        self.clock = clock

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _upgrade(self) -> None:
        """Add CB-05 columns to cache files created before them (idempotent)."""
        cols = {r[1] for r in self._db.execute("PRAGMA table_info(llm_cache)")}
        with self._db:
            if "retention_class" not in cols:
                self._db.execute(
                    "ALTER TABLE llm_cache ADD COLUMN retention_class TEXT NOT NULL "
                    "DEFAULT 'person_level_24m'"
                )
            self._db.execute(
                "CREATE INDEX IF NOT EXISTS llm_cache_created_idx ON llm_cache (created_at)"
            )

    def _cutoff(self, now: datetime | None = None) -> str:
        return _ts((now or self.clock()) - timedelta(days=self.retention_days))

    # --- cache ---------------------------------------------------------------
    def cache_get(self, key: str) -> tuple[dict[str, Any], str] | None:
        """The cached output, unless missing or expired (CB-05: expired rows are never served)."""
        with self._lock:
            row = self._db.execute(
                "SELECT output_json, model FROM llm_cache WHERE key = ? AND created_at > ?",
                (key, self._cutoff()),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row[0]), row[1]

    def cache_put(
        self,
        key: str,
        output: dict[str, Any],
        model: str,
        *,
        evidence_id: str | None = None,
        retention_class: str = "person_level_24m",
    ) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO llm_cache (key, output_json, model, created_at,"
                " retention_class) VALUES (?, ?, ?, ?, ?)",
                (
                    key,
                    json.dumps(output, sort_keys=True),
                    model,
                    _ts(self.clock()),
                    retention_class,
                ),
            )
            if evidence_id is not None:
                self._link(key, evidence_id)

    def _link(self, key: str, evidence_id: str) -> None:
        self._db.execute(
            "INSERT OR IGNORE INTO llm_cache_evidence (key, evidence_id) VALUES (?, ?)",
            (key, evidence_id),
        )

    def link_evidence(self, key: str, evidence_id: str) -> None:
        """Record that a cached output was (also) derived from `evidence_id` (CB-05)."""
        with self._lock, self._db:
            self._link(key, evidence_id)

    def cache_evidence(self, key: str) -> list[str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT evidence_id FROM llm_cache_evidence WHERE key = ? ORDER BY 1", (key,)
            ).fetchall()
        return [r[0] for r in rows]

    def _delete_keys(self, keys: list[str]) -> int:
        n = 0
        for k in keys:
            n += self._db.execute("DELETE FROM llm_cache WHERE key = ?", (k,)).rowcount
            self._db.execute("DELETE FROM llm_cache_evidence WHERE key = ?", (k,))
        return n

    def purge_expired(self, now: datetime | None = None, *, dry_run: bool = False) -> int:
        """Delete cache rows older than the retention period; return the number of rows (CB-05)."""
        with self._lock, self._db:
            keys = [
                r[0]
                for r in self._db.execute(
                    "SELECT key FROM llm_cache WHERE created_at <= ?", (self._cutoff(now),)
                )
            ]
            return len(keys) if dry_run else self._delete_keys(keys)

    def purge_for_evidence(self, evidence_ids: Iterable[str], *, dry_run: bool = False) -> int:
        """Delete cache rows derived from any of `evidence_ids` (source dropped or erased)."""
        ids = sorted(set(evidence_ids))
        if not ids:
            return 0
        with self._lock, self._db:
            keys: set[str] = set()
            for i in range(0, len(ids), 500):
                chunk = ids[i : i + 500]
                q = ",".join("?" * len(chunk))
                keys.update(
                    r[0]
                    for r in self._db.execute(
                        f"SELECT key FROM llm_cache_evidence WHERE evidence_id IN ({q})", chunk
                    )
                )
            present = [
                k
                for k in sorted(keys)
                if self._db.execute("SELECT 1 FROM llm_cache WHERE key = ?", (k,)).fetchone()
            ]
            if dry_run:
                return len(present)
            n = self._delete_keys(present)
            # links whose cache row is already gone
            for k in keys - set(present):
                self._db.execute("DELETE FROM llm_cache_evidence WHERE key = ?", (k,))
            return n

    def find_containing(self, tokens: Iterable[str]) -> list[dict[str, Any]]:
        """Cache rows whose output mentions a pseudonym token; for access requests (CB-08)."""
        toks = sorted({t for t in tokens if t})
        if not toks:
            return []
        where = " OR ".join("instr(output_json, ?) > 0" for _ in toks)
        with self._lock:
            rows = self._db.execute(
                "SELECT key, output_json, model, created_at, retention_class FROM llm_cache"
                f" WHERE {where} ORDER BY created_at, key",
                toks,
            ).fetchall()
        return [
            {
                "key": r[0],
                "output": json.loads(r[1]),
                "model": r[2],
                "created_at": r[3],
                "retention_class": r[4],
            }
            for r in rows
        ]

    def purge_containing(self, tokens: Iterable[str], *, dry_run: bool = False) -> int:
        """Delete cache rows whose output mentions any token (erasure/objection, CB-08/CB-13)."""
        keys = [r["key"] for r in self.find_containing(tokens)]
        if dry_run:
            return len(keys)
        with self._lock, self._db:
            return self._delete_keys(keys)

    def clear(
        self,
        *,
        older_than: timedelta | None = None,
        now: datetime | None = None,
        dry_run: bool = False,
    ) -> int:
        """CB-28: delete cached outputs (all, or those created more than `older_than` ago) and
        their evidence links; returns the number of cache rows. The usage ledger and pause state
        are kept. Used by `pigtail llm cache clear` and by a key rotation (CB-26), after which
        cached outputs may quote pseudonyms under the old key."""
        if older_than is not None and older_than < timedelta(0):
            raise ValueError("older_than must be >= 0")
        with self._lock, self._db:
            if older_than is None:
                n = int(self._db.execute("SELECT count(*) FROM llm_cache").fetchone()[0])
                if not dry_run:
                    self._db.execute("DELETE FROM llm_cache")
                    self._db.execute("DELETE FROM llm_cache_evidence")
                return n
            cutoff = _ts((now or self.clock()) - older_than)
            keys = [
                r[0]
                for r in self._db.execute(
                    "SELECT key FROM llm_cache WHERE created_at <= ?", (cutoff,)
                )
            ]
            if dry_run:
                return len(keys)
            n = self._delete_keys(keys)
            # links whose cache row no longer exists
            self._db.execute(
                "DELETE FROM llm_cache_evidence WHERE key NOT IN (SELECT key FROM llm_cache)"
            )
            return n

    def cache_count(self) -> int:
        with self._lock:
            return int(self._db.execute("SELECT count(*) FROM llm_cache").fetchone()[0])

    def purge_ledger(self, now: datetime | None = None, *, dry_run: bool = False) -> int:
        """Delete usage-ledger and pause-log rows past the retention period (policy §2)."""
        cutoff = self._cutoff(now)
        with self._lock, self._db:
            n = 0
            for table in ("llm_usage", "llm_pause_log"):
                if dry_run:
                    row = self._db.execute(
                        f"SELECT count(*) FROM {table} WHERE ts <= ?", (cutoff,)
                    ).fetchone()
                    n += int(row[0])
                else:
                    n += self._db.execute(f"DELETE FROM {table} WHERE ts <= ?", (cutoff,)).rowcount
            return n

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

    def usage_since(self, backend: str, since: datetime) -> dict[str, float]:
        """Backend calls, tokens and cost recorded since `since` (M12 BudgetGuard, ADR-053.1).

        Cache hits cost nothing and limit hits carry no tokens, so they are not counted.
        """
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*), SUM(input_tokens), SUM(output_tokens), SUM(cost_usd)"
                " FROM llm_usage WHERE backend = ? AND ts >= ?"
                " AND status IN ('ok', 'invalid_output', 'error')",
                (backend, _ts(since)),
            ).fetchone()
        calls, tin, tout, cost = row
        return {
            "calls": float(calls or 0),
            "tokens": float((tin or 0) + (tout or 0)),
            "cost_usd": float(cost or 0),
        }

    def last_limit(self, backend: str) -> datetime | None:
        """Time of the most recent usage-limit hit on `backend` (allowance calibration)."""
        with self._lock:
            row = self._db.execute(
                "SELECT MAX(ts) FROM llm_usage WHERE backend = ? AND status = 'limit'", (backend,)
            ).fetchone()
        return datetime.fromisoformat(row[0]) if row and row[0] else None

    def usage_between(self, backend: str, start: datetime, end: datetime) -> float:
        """Tokens used on `backend` in [start, end)."""
        with self._lock:
            row = self._db.execute(
                "SELECT SUM(input_tokens + output_tokens) FROM llm_usage"
                " WHERE backend = ? AND ts >= ? AND ts < ?"
                " AND status IN ('ok', 'invalid_output', 'error')",
                (backend, _ts(start), _ts(end)),
            ).fetchone()
        return float(row[0] or 0)

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
