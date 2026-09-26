"""M1-T2 (R2.1, R2.3), M1-T8 (PRD §10) and M21a (Directive §8.1, ADR-066.1, ADR-071.2: roles and
buckets, no handles): connector base contract tests. No real network."""

from __future__ import annotations

import json
import random
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, ClassVar

import httpx
import pytest

from pigtail import __version__
from pigtail.capture.replay import replay
from pigtail.capture.runs import RunRecorder
from pigtail.capture.snapshots import LocalSnapshotStore, SnapshotMeta, key_for
from pigtail.connectors.base import (
    USER_AGENT,
    Clearance,
    Connector,
    ConnectorDisabled,
    ConnectorGapError,
    CostEvent,
    FetchError,
    NotFound,
    Record,
    RetryPolicy,
    TermsMetadata,
    TokenBucket,
    parse_retry_after,
)

NOW = datetime(2026, 9, 20, 12, tzinfo=UTC)


class FakeSource(Connector):
    name: ClassVar[str] = "fake"
    version: ClassVar[str] = "1.0.0"
    terms: ClassVar[TermsMetadata] = TermsMetadata(
        terms_url="https://example.org/terms",
        terms_basis="synthetic test terms",
        clearance=Clearance.CLEARED,
        commercial_use=True,
        deletion_obligation=True,
    )
    enabled_by_default: ClassVar[bool] = True
    rate_per_second: ClassVar[float] = 1000.0
    cost_per_request_usd: ClassVar[float] = 0.01
    handle_fields: ClassVar[tuple[str, ...]] = ("author", "comments.author", "mentions")
    handle_namespace: ClassVar[str] = "fake"

    def _parse(self, data: bytes, meta: SnapshotMeta) -> Iterable[Record]:
        yield from json.loads(data)["items"]

    def _pre_code(self, record: Record) -> Record | None:
        return None if record.get("author") == "spam-bot" else record


class GapSource(FakeSource):
    name: ClassVar[str] = "gapsrc"
    terms: ClassVar[TermsMetadata] = TermsMetadata(
        terms_url="https://example.org/tos",
        terms_basis="terms forbid automated access",
        clearance=Clearance.GAP,
        commercial_use=False,
        deletion_obligation=None,
    )
    enabled_by_default: ClassVar[bool] = False


PAYLOAD = {
    "items": [
        {
            "id": 1,
            "author": "alice-synthetic",
            "comments": [{"author": "bob-synthetic", "text": "hi"}],
            "mentions": ["carol-synthetic", "dave-synthetic"],
        },
        {"id": 2, "author": "spam-bot", "comments": [], "mentions": []},
        {"id": 3, "author": None, "comments": [], "mentions": []},
    ]
}


def make(
    handler: Any, tmp_path: Any, pz: Any, **kw: Any
) -> tuple[FakeSource, list[float], list[CostEvent]]:
    sleeps: list[float] = []
    costs: list[CostEvent] = []
    c = FakeSource(
        store=LocalSnapshotStore(tmp_path),
        pseudonymizer=pz,
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        env={},
        sleep=sleeps.append,
        rng=random.Random(0),
        clock=lambda: NOW,
        cost_hook=costs.append,
        limiter=kw.pop("limiter", TokenBucket(1e9, burst=10_000)),
        **kw,
    )
    return c, sleeps, costs


def ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=PAYLOAD, headers={"Content-Type": "application/json"})


def test_r2_1_user_agent_snapshot_evidence_and_cost(tmp_path, pz):
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return ok_handler(req)

    evidence = []
    with RunRecorder("test.job", detect_commit=False) as run:
        c, _, costs = make(handler, tmp_path, pz, run=run, evidence_sink=evidence.append)
        f = c.fetch("https://api.example.org/items", case_id=None, repo_id="github:1")
    assert seen[0].headers["User-Agent"] == USER_AGENT
    assert f"pigtail/{__version__} (+https://github.com/suchipizza/pigtail)" == USER_AGENT
    # snapshot stored before parse, content-addressed
    assert (tmp_path / key_for(f.content_hash)).read_bytes() == f.data
    ev = f.evidence
    assert evidence == [ev]
    assert (
        ev.content_hash == f.content_hash and ev.snapshot_ref == f"local:{key_for(ev.content_hash)}"
    )
    assert ev.source == "fake" and ev.collector_version == "fake/1.0.0"
    assert ev.terms_basis == "synthetic test terms" and ev.repo_id == "github:1"
    assert ev.run_id == run.id and ev.fetched_at == NOW
    assert [x.usd for x in costs] == [0.01]
    assert run.counts["fake.http_requests"] == 1 and run.counts["fake.cost_usd"] == 0.01
    assert run.counts["fake.snapshots"] == 1


def test_r2_1_retries_429_respecting_retry_after_then_succeeds(tmp_path, pz):
    responses = iter(
        [
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(503),
            httpx.Response(200, json=PAYLOAD),
        ]
    )
    c, sleeps, costs = make(lambda r: next(responses), tmp_path, pz)
    c.fetch("https://api.example.org/x")
    assert sleeps[0] == 7.0  # Retry-After honoured
    assert 0 <= sleeps[1] <= c.retry.base_delay * 2  # jittered exponential backoff, attempt 1
    assert len(costs) == 3  # every request is accounted


def test_r2_1_retries_exhausted_and_long_retry_after(tmp_path, pz):
    c, sleeps, _ = make(
        lambda r: httpx.Response(500), tmp_path, pz, retry=RetryPolicy(max_retries=2)
    )
    with pytest.raises(FetchError) as ei:
        c.fetch("https://api.example.org/x")
    assert ei.value.status == 500 and len(sleeps) == 2
    c2, _, _ = make(lambda r: httpx.Response(429, headers={"Retry-After": "99999"}), tmp_path, pz)
    with pytest.raises(FetchError, match="too long"):
        c2.fetch("https://api.example.org/x")


def test_r2_1_transport_errors_retried(tmp_path, pz):
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("down", request=req)
        return ok_handler(req)

    c, sleeps, _ = make(handler, tmp_path, pz)
    c.fetch("https://api.example.org/x")
    assert calls["n"] == 3 and len(sleeps) == 2


def test_r2_1_backoff_is_exponential_with_full_jitter():
    p = RetryPolicy(base_delay=1.0, max_delay=10.0)
    rng = random.Random(1)
    for attempt, cap in [(0, 1), (1, 2), (2, 4), (3, 8), (6, 10)]:
        for _ in range(50):
            assert 0 <= p.backoff(attempt, rng) <= cap


def test_parse_retry_after_http_date():
    assert parse_retry_after("Sun, 20 Sep 2026 12:00:30 GMT", NOW) == 30.0
    assert parse_retry_after("garbage") is None and parse_retry_after(None) is None


def test_r2_1_404_and_other_errors_not_snapshotted(tmp_path, pz):
    c, _, _ = make(lambda r: httpx.Response(404), tmp_path, pz)
    with pytest.raises(NotFound):
        c.fetch("https://api.example.org/missing")
    c2, _, _ = make(lambda r: httpx.Response(403), tmp_path, pz)
    with pytest.raises(FetchError):
        c2.fetch("https://api.example.org/forbidden")
    assert not any(p.is_file() for p in tmp_path.rglob("*"))


def test_r2_1_token_bucket_rate_with_safety_margin():
    t = {"now": 0.0}
    sleeps: list[float] = []

    def sleep(s: float) -> None:
        sleeps.append(s)
        t["now"] += s

    b = TokenBucket(10.0, burst=2, safety_margin=0.5, clock=lambda: t["now"], sleep=sleep)
    for _ in range(12):
        b.acquire()
    # 2 burst tokens free, then 10 more at 5/s effective = 2.0 s
    assert t["now"] == pytest.approx(2.0)
    with pytest.raises(ValueError):
        TokenBucket(0)


def test_r2_1_enable_flag_from_env(tmp_path, pz):
    store = LocalSnapshotStore(tmp_path)
    off = FakeSource(store=store, pseudonymizer=pz, env={"PIGTAIL_CONNECTOR_FAKE_ENABLED": "false"})
    assert not off.enabled
    with pytest.raises(ConnectorDisabled):
        off.fetch("https://api.example.org/x")
    on = FakeSource(store=store, pseudonymizer=pz, env={"PIGTAIL_CONNECTOR_FAKE_ENABLED": "1"})
    assert on.enabled
    assert FakeSource(store=store, pseudonymizer=pz, env={}, enabled=False).enabled is False


def test_r2_3_gap_connector_cannot_be_enabled(tmp_path, pz):
    store = LocalSnapshotStore(tmp_path)
    assert GapSource(store=store, pseudonymizer=pz, env={}).enabled is False
    with pytest.raises(ConnectorGapError):
        GapSource(store=store, pseudonymizer=pz, env={}, enabled=True)
    with pytest.raises(ConnectorGapError):
        GapSource(store=store, pseudonymizer=pz, env={"PIGTAIL_CONNECTOR_GAPSRC_ENABLED": "true"})


def test_r2_1_snapshot_or_drop_storage_failure_prevents_parsing(tmp_path, pz):
    class BrokenStore(LocalSnapshotStore):
        def put(self, data: bytes, meta: SnapshotMeta) -> str:
            raise OSError("disk full")

    parsed = []

    class Spy(FakeSource):
        def _parse(self, data: bytes, meta: SnapshotMeta) -> Iterable[Record]:
            parsed.append(1)
            return []

    c = Spy(
        store=BrokenStore(tmp_path),
        pseudonymizer=pz,
        http=httpx.Client(transport=httpx.MockTransport(ok_handler)),
        env={},
    )
    with pytest.raises(OSError):
        c.fetch_records("https://api.example.org/x")
    assert parsed == []


def test_m21a_directive_8_1_handles_coded_then_dropped_at_ingest(tmp_path, pz):
    """Directive §8.1 / ADR-066.1 / ADR-071.2: records leave the connector with a role, a bucket
    and the automated-account flag plus rule versions, never a handle or a pseudonym."""
    c, _, _ = make(ok_handler, tmp_path, pz)
    f, recs = c.fetch_records("https://api.example.org/items")
    out = list(recs)
    assert [r["id"] for r in out] == [1, 3]  # spam-bot dropped in the pre-code hook
    r = out[0]
    assert r["author"] is None and r["comments"][0]["author"] is None
    assert r["mentions"] is None
    assert r["actor_role"] == "account" and r["actor_bucket"] == "r0"
    assert r["automated_account"] is False and r["bot_rule_version"] == "bot-filter-v0"
    assert r["role_rule_version"] == "roles-v1" and "_actor_token" not in r
    assert out[1]["author"] is None
    blob = json.dumps(out)
    for name in ("alice", "bob", "carol", "dave"):
        assert name not in blob
    assert "p_" not in blob  # no pseudonym either
    # raw handles remain only in the private snapshot bytes
    assert b"alice-synthetic" in c.store.get(f.content_hash)


def test_m21a_adr071_1_subject_records_carry_fingerprints_in_memory(tmp_path, pz):
    c, _, _ = make(ok_handler, tmp_path, pz)
    f = c.fetch("https://api.example.org/items")
    got = list(c.subject_records(f.data, f.meta))
    fps = got[0][1]
    assert pz.person_fingerprint("alice-synthetic", "fake") in fps
    assert pz.person_fingerprint("dave-synthetic", "fake") in fps
    assert "alice" not in json.dumps(got[0][0])


def test_m21a_bot_rule_sets_automated_role(tmp_path, pz):
    c, _, _ = make(ok_handler, tmp_path, pz)
    rec = c.code(c._pre_code({"id": 9, "author": "dependabot[bot]"}) or {})
    assert rec["automated_account"] is True and rec["actor_role"] == "automated_account"
    assert rec["author"] is None


def test_m1_t8_non_string_handle_rejected(tmp_path, pz):
    c, _, _ = make(ok_handler, tmp_path, pz)
    with pytest.raises(TypeError):
        c.code({"author": 42})


def test_replay_hook_reproduces_ingest_records(tmp_path, pz):
    c, _, _ = make(ok_handler, tmp_path, pz)
    f, recs = c.fetch_records("https://api.example.org/items")
    stored = list(recs)
    c.enabled = False  # replay needs no network and no enable flag
    assert replay(c, f.content_hash) == stored
