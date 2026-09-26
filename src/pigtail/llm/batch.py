"""Batch state and the actual-cost ledger (PRD R15.9, R15.11; migration 0020).

- `BatchStore`: which Message Batches are in flight, the cache key each request fills, and how
  each ended. `PgBatchStore` keeps it in Postgres (`llm_batches`, `llm_batch_requests`), so a
  paused or restarted run collects its submitted batches by id instead of submitting (and
  paying for) the same work again. `MemoryBatchStore` is for tests and one-off commands.
- `CostSink`: where every model call's actual cost goes besides the local usage ledger.
  `PgCostLedger` writes `llm_cost_ledger` (tokens in and out, prompt-cache writes and reads,
  batch id, USD per call, with the brief run and case), which M23 reads to report the pilot's
  cost per case and the projection (R15.11).

No prompt text, model output or evidence content is stored by either: ids, hashes, counts,
statuses and token numbers only.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    import psycopg

    from pigtail.llm.store import UsageRow

BatchState = Literal["submitted", "ended", "collected", "failed", "canceled"]
RequestState = Literal["pending", "succeeded", "invalid_output", "errored", "canceled", "expired"]


def custom_id_for(cache_key: str) -> str:
    """A Message Batches `custom_id` (1-64 of [A-Za-z0-9_-]) derived from the cache key."""
    return "k" + hashlib.sha256(cache_key.encode()).hexdigest()[:40]


@dataclass(frozen=True)
class BatchRequestRecord:
    custom_id: str
    cache_key: str
    input_hash: str
    evidence_id: str | None = None
    case_ref: str | None = None
    status: RequestState = "pending"
    error_type: str | None = None


@dataclass(frozen=True)
class BatchRecord:
    batch_id: str
    job: str
    stage: str
    model: str
    prompt_id: str
    prompt_version: str
    prompt_fingerprint: str
    schema_hash: str
    requests: int
    brief_run_id: str | None = None
    status: BatchState = "submitted"
    est_usd: float | None = None
    counts: dict[str, int] = field(default_factory=dict)
    submitted_at: datetime | None = None
    backend: str = "api"


class BatchStore(Protocol):
    def add(self, rec: BatchRecord, requests: list[BatchRequestRecord]) -> None: ...

    def get(self, batch_id: str) -> BatchRecord | None: ...

    def open_batches(
        self, *, job: str, prompt_fingerprint: str, model: str, brief_run_id: str | None
    ) -> list[BatchRecord]: ...

    def requests(self, batch_id: str) -> list[BatchRequestRecord]: ...

    def mark_request(
        self, batch_id: str, custom_id: str, status: RequestState, error_type: str | None = None
    ) -> None: ...

    def set_status(
        self, batch_id: str, status: BatchState, counts: dict[str, int] | None = None
    ) -> None: ...


class MemoryBatchStore:
    """In-process `BatchStore` (tests, one-off commands); lost when the process ends."""

    def __init__(self) -> None:
        self.batches: dict[str, BatchRecord] = {}
        self.reqs: dict[str, dict[str, BatchRequestRecord]] = {}

    def add(self, rec: BatchRecord, requests: list[BatchRequestRecord]) -> None:
        if rec.batch_id in self.batches:
            raise ValueError(f"batch {rec.batch_id} already recorded")
        self.batches[rec.batch_id] = replace(rec, submitted_at=rec.submitted_at or _now())
        self.reqs[rec.batch_id] = {r.custom_id: r for r in requests}

    def get(self, batch_id: str) -> BatchRecord | None:
        return self.batches.get(batch_id)

    def open_batches(
        self, *, job: str, prompt_fingerprint: str, model: str, brief_run_id: str | None
    ) -> list[BatchRecord]:
        return [
            b
            for b in self.batches.values()
            if b.status in ("submitted", "ended")
            and b.job == job
            and b.prompt_fingerprint == prompt_fingerprint
            and b.model == model
            and b.brief_run_id == brief_run_id
        ]

    def requests(self, batch_id: str) -> list[BatchRequestRecord]:
        return list(self.reqs.get(batch_id, {}).values())

    def mark_request(
        self, batch_id: str, custom_id: str, status: RequestState, error_type: str | None = None
    ) -> None:
        r = self.reqs[batch_id][custom_id]
        self.reqs[batch_id][custom_id] = replace(r, status=status, error_type=error_type)

    def set_status(
        self, batch_id: str, status: BatchState, counts: dict[str, int] | None = None
    ) -> None:
        b = self.batches[batch_id]
        self.batches[batch_id] = replace(
            b, status=status, counts=dict(counts) if counts is not None else b.counts
        )


def _now() -> datetime:
    return datetime.now(UTC)


class PgBatchStore:
    """`BatchStore` on Postgres (migration 0020). Uses its own autocommit connection so batch
    ids are durable the moment they are known."""

    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self.conn = conn

    @classmethod
    def connect(cls, conninfo: str) -> PgBatchStore:
        import psycopg

        return cls(psycopg.connect(conninfo, autocommit=True))

    def add(self, rec: BatchRecord, requests: list[BatchRequestRecord]) -> None:
        from psycopg.types.json import Jsonb

        with self.conn.transaction():
            self.conn.execute(
                "INSERT INTO llm_batches (batch_id, backend, job, stage, model, prompt_id,"
                " prompt_version, prompt_fingerprint, schema_hash, brief_run_id, status,"
                " requests, est_usd, counts) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
                " %s, %s, %s, %s)",
                (
                    rec.batch_id,
                    rec.backend,
                    rec.job,
                    rec.stage,
                    rec.model,
                    rec.prompt_id,
                    rec.prompt_version,
                    rec.prompt_fingerprint,
                    rec.schema_hash,
                    rec.brief_run_id,
                    rec.status,
                    rec.requests,
                    rec.est_usd,
                    Jsonb(rec.counts),
                ),
            )
            with self.conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO llm_batch_requests (batch_id, custom_id, cache_key, input_hash,"
                    " evidence_id, case_ref, status) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    [
                        (
                            rec.batch_id,
                            r.custom_id,
                            r.cache_key,
                            r.input_hash,
                            r.evidence_id,
                            r.case_ref,
                            r.status,
                        )
                        for r in requests
                    ],
                )

    _COLS = (
        "batch_id, job, stage, model, prompt_id, prompt_version, prompt_fingerprint, schema_hash,"
        " requests, brief_run_id, status, est_usd, counts, submitted_at, backend"
    )

    @staticmethod
    def _rec(row: Any) -> BatchRecord:
        return BatchRecord(
            batch_id=row[0],
            job=row[1],
            stage=row[2],
            model=row[3],
            prompt_id=row[4],
            prompt_version=row[5],
            prompt_fingerprint=row[6],
            schema_hash=row[7],
            requests=int(row[8]),
            brief_run_id=row[9],
            status=row[10],
            est_usd=float(row[11]) if row[11] is not None else None,
            counts=dict(row[12] or {}),
            submitted_at=row[13],
            backend=row[14],
        )

    def get(self, batch_id: str) -> BatchRecord | None:
        row = self.conn.execute(
            f"SELECT {self._COLS} FROM llm_batches WHERE batch_id = %s", (batch_id,)
        ).fetchone()
        return self._rec(row) if row else None

    def open_batches(
        self, *, job: str, prompt_fingerprint: str, model: str, brief_run_id: str | None
    ) -> list[BatchRecord]:
        rows = self.conn.execute(
            f"SELECT {self._COLS} FROM llm_batches WHERE status IN ('submitted', 'ended')"
            " AND job = %s AND prompt_fingerprint = %s AND model = %s"
            " AND brief_run_id IS NOT DISTINCT FROM %s ORDER BY submitted_at, batch_id",
            (job, prompt_fingerprint, model, brief_run_id),
        ).fetchall()
        return [self._rec(r) for r in rows]

    def requests(self, batch_id: str) -> list[BatchRequestRecord]:
        rows = self.conn.execute(
            "SELECT custom_id, cache_key, input_hash, evidence_id, case_ref, status, error_type"
            " FROM llm_batch_requests WHERE batch_id = %s ORDER BY custom_id",
            (batch_id,),
        ).fetchall()
        return [BatchRequestRecord(*r) for r in rows]

    def mark_request(
        self, batch_id: str, custom_id: str, status: RequestState, error_type: str | None = None
    ) -> None:
        self.conn.execute(
            "UPDATE llm_batch_requests SET status = %s, error_type = %s"
            " WHERE batch_id = %s AND custom_id = %s",
            (status, error_type, batch_id, custom_id),
        )

    def set_status(
        self, batch_id: str, status: BatchState, counts: dict[str, int] | None = None
    ) -> None:
        from psycopg.types.json import Jsonb

        ts = {"ended": "ended_at", "collected": "collected_at"}.get(status)
        sets = "status = %s" + (f", {ts} = COALESCE({ts}, now())" if ts else "")
        params: list[Any] = [status]
        if counts is not None:
            sets += ", counts = %s"
            params.append(Jsonb(counts))
        self.conn.execute(f"UPDATE llm_batches SET {sets} WHERE batch_id = %s", (*params, batch_id))


# --- actual-cost ledger ---------------------------------------------------------------------


class CostSink(Protocol):
    def record(self, row: UsageRow) -> None: ...


class PgCostLedger:
    """Actual cost per model call in Postgres (`llm_cost_ledger`, migration 0020)."""

    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self.conn = conn

    @classmethod
    def connect(cls, conninfo: str) -> PgCostLedger:
        import psycopg

        return cls(psycopg.connect(conninfo, autocommit=True))

    def record(self, row: UsageRow) -> None:
        from pigtail.llm.pricing import PRICES_AS_OF

        if row.status in ("cached", "limit"):
            return  # no model call was made, nothing was spent
        self.conn.execute(
            "INSERT INTO llm_cost_ledger (brief_id, brief_run_id, case_ref, job, stage, backend,"
            " model, prompt_id, prompt_version, batch_id, status, input_tokens, output_tokens,"
            " cache_write_tokens, cache_read_tokens, cost_usd, prices_as_of)"
            " VALUES ((SELECT brief_id FROM brief_runs WHERE id = %s), %s, %s, %s, %s, %s, %s,"
            " %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                row.brief_run_id,
                row.brief_run_id,
                row.case_ref,
                row.job,
                row.stage,
                row.backend,
                row.model,
                row.prompt_id,
                row.prompt_version,
                row.batch_id,
                row.status,
                row.input_tokens,
                row.output_tokens,
                row.cache_write_tokens,
                row.cache_read_tokens,
                round(row.cost_usd, 6),
                PRICES_AS_OF if row.backend == "api" else None,
            ),
        )

    def per_case(self, brief_run_id: str) -> dict[str, dict[str, float]]:
        """Cost of a brief run per case (`-` for calls not tied to a case), for M23."""
        rows = self.conn.execute(
            "SELECT COALESCE(case_ref, '-'), count(*), sum(input_tokens), sum(output_tokens),"
            " sum(cache_write_tokens), sum(cache_read_tokens), count(batch_id), sum(cost_usd)"
            " FROM llm_cost_ledger WHERE brief_run_id = %s GROUP BY 1 ORDER BY 1",
            (brief_run_id,),
        ).fetchall()
        keys = (
            "calls",
            "input_tokens",
            "output_tokens",
            "cache_write_tokens",
            "cache_read_tokens",
            "batched_calls",
            "cost_usd",
        )
        return {r[0]: {k: float(v or 0) for k, v in zip(keys, r[1:], strict=True)} for r in rows}

    def brief_total(self, brief_id: str) -> float:
        """API spend on a brief over all its runs (checked against `budget.money_usd`)."""
        row = self.conn.execute(
            "SELECT COALESCE(sum(cost_usd), 0) FROM llm_cost_ledger"
            " WHERE brief_id = %s AND backend = 'api'",
            (brief_id,),
        ).fetchone()
        return float(row[0]) if row else 0.0

    def month_total(self, since: datetime) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(sum(cost_usd), 0) FROM llm_cost_ledger"
            " WHERE backend = 'api' AND created_at >= %s",
            (since,),
        ).fetchone()
        return float(row[0]) if row else 0.0
