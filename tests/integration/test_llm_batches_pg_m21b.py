"""M21b on Postgres (migration 0020; PRD R15.9, R15.11): batch ids stored so a restarted run
collects its batches instead of resubmitting, and the actual-cost ledger per brief run and case
(tokens in/out, cache, batch) that M23 reports from. Fake batch backend; synthetic briefs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest

from pigtail.briefs.cache import BriefRuns
from pigtail.briefs.model import load_brief_text
from pigtail.llm import BatchPending
from pigtail.llm.batch import BatchRecord, BatchRequestRecord, PgBatchStore, PgCostLedger
from pigtail.llm.store import LLMStore, UsageRow
from tests.conftest import Echo
from tests.unit.test_llm_m21b import PROMPT, FakeBatchBackend, client, items

pytestmark = pytest.mark.db

EXAMPLE = Path(__file__).resolve().parents[2] / "docs" / "examples" / "brief-example.yaml"


@pytest.fixture
def conn(capture_db: Any) -> psycopg.Connection[Any]:
    capture_db.conn.autocommit = True
    return capture_db.conn  # type: ignore[no-any-return]


def brief_run(conn: psycopg.Connection[Any]) -> str:
    b = load_brief_text(EXAMPLE.read_text()).model_copy(update={"version": 1})
    return BriefRuns(conn).create(b, data_version=None).id


def test_r15_9_batch_state_round_trip_and_constraints(conn):
    store = PgBatchStore(conn)
    run_id = brief_run(conn)
    rec = BatchRecord(
        batch_id="msgbatch_synthetic1", job="extraction", stage="extraction",
        model="claude-sonnet-5", prompt_id="p", prompt_version="1", prompt_fingerprint="f",
        schema_hash="s", requests=1, brief_run_id=run_id, est_usd=0.25,
    )  # fmt: skip
    req = BatchRequestRecord("k" + "0" * 40, "cache-key", "a" * 64, case_ref="case-1")
    store.add(rec, [req])
    got = store.get("msgbatch_synthetic1")
    assert got is not None and got.status == "submitted" and got.est_usd == 0.25
    assert [b.batch_id for b in store.open_batches(
        job="extraction", prompt_fingerprint="f", model="claude-sonnet-5", brief_run_id=run_id
    )] == ["msgbatch_synthetic1"]  # fmt: skip
    store.mark_request("msgbatch_synthetic1", req.custom_id, "succeeded")
    store.set_status("msgbatch_synthetic1", "collected", {"succeeded": 1})
    row = conn.execute(
        "SELECT status, counts, ended_at IS NULL, collected_at IS NOT NULL FROM llm_batches"
    ).fetchone()
    assert row == ("collected", {"succeeded": 1}, True, True)
    assert store.requests("msgbatch_synthetic1")[0].status == "succeeded"
    assert not store.open_batches(
        job="extraction", prompt_fingerprint="f", model="claude-sonnet-5", brief_run_id=run_id
    )
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO llm_batch_requests (batch_id, custom_id, cache_key, input_hash)"
            " VALUES ('msgbatch_synthetic1', 'bad id!', 'k', %s)",
            ("a" * 64,),
        )


def test_r15_9_restart_collects_the_stored_batch_by_id(pg_url, conn):
    fb = FakeBatchBackend(polls_until_end=2)
    llm = LLMStore(":memory:")
    run_id = brief_run(conn)
    c1 = client({"api": fb}, store=llm, batch_store=PgBatchStore.connect(pg_url))
    with pytest.raises(BatchPending):
        c1.run_batch(PROMPT, items("a", "b"), Echo, job="extraction", brief_run_id=run_id,
                     timeout_seconds=0, sleep=lambda s: None)  # fmt: skip
    # a new process: fresh connections, same database
    c2 = client(
        {"api": fb},
        store=llm,
        batch_store=PgBatchStore.connect(pg_url),
        cost_sink=PgCostLedger.connect(pg_url),
    )
    run = c2.run_batch(PROMPT, items("a", "b"), Echo, job="extraction", brief_run_id=run_id,
                       sleep=lambda s: None)  # fmt: skip
    assert len(fb.submitted) == 1 and run.batch_ids == ["msgbatch_1"]
    assert {r.batch_id for r in run.results.values()} == {"msgbatch_1"}
    assert conn.execute("SELECT status FROM llm_batches").fetchone() == ("collected",)
    # the actual-cost ledger has one row per collected result, with batch id and case
    per_case = PgCostLedger(conn).per_case(run_id)
    assert set(per_case) == {"case0", "case1"}
    assert per_case["case0"]["batched_calls"] == 1 and per_case["case0"]["cache_read_tokens"] == 7
    assert sum(v["cost_usd"] for v in per_case.values()) == pytest.approx(0.002)


def test_r15_11_cost_ledger_totals_per_brief_and_month(conn):
    ledger = PgCostLedger(conn)
    run_id = brief_run(conn)
    ledger.record(UsageRow("api", "extraction", "claude-sonnet-5", "p", "1", "ok", 100, 10,
                           cost_usd=1.25, stage="extraction", cache_read_tokens=50,
                           batch_id="msgbatch_x", brief_run_id=run_id, case_ref="c1"))  # fmt: skip
    ledger.record(UsageRow("api", "report", "claude-opus-5-5", "p", "1", "ok", 10, 10,
                           cost_usd=0.5, brief_run_id=run_id))  # fmt: skip
    ledger.record(UsageRow("api", "x", "m", "p", "1", "cached", cost_usd=9.0))  # no call: skipped
    ledger.record(UsageRow("subscription", "x", "m", "p", "1", "ok", 5, 5))  # no money
    assert ledger.brief_total("example-config-linter") == pytest.approx(1.75)
    assert ledger.brief_total("other-brief") == 0.0
    now = datetime.now(UTC)
    assert ledger.month_total(now - timedelta(days=1)) == pytest.approx(1.75)
    assert ledger.month_total(now + timedelta(days=1)) == 0.0
    per = ledger.per_case(run_id)
    assert per["c1"]["cost_usd"] == 1.25 and per["-"]["cost_usd"] == 0.5
    row = conn.execute(
        "SELECT prices_as_of FROM llm_cost_ledger WHERE backend = 'api' LIMIT 1"
    ).fetchone()
    assert row == ("2026-06-24",)
