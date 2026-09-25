"""DPIA CB-05: LLM cache retention (created_at expiry, evidence links, purge)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pigtail.config import Settings
from pigtail.llm.store import LLMStore
from tests.conftest import Echo, make_client

T0 = datetime(2026, 9, 25, tzinfo=UTC)


class Clock:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t


def test_cb05_expired_rows_not_served_and_purged():
    clock = Clock(T0)
    s = LLMStore(":memory:", retention_days=30, clock=clock)
    s.cache_put("k1", {"a": 1}, "m")
    assert s.cache_get("k1") == ({"a": 1}, "m")
    clock.t = T0 + timedelta(days=31)
    assert s.cache_get("k1") is None  # expired: never served
    s.cache_put("k2", {"a": 2}, "m")
    assert s.purge_expired(dry_run=True) == 1
    assert s.purge_expired() == 1
    assert s.purge_expired() == 0
    assert s.cache_get("k2") is not None


def test_cb05_default_retention_is_24_months():
    assert LLMStore(":memory:").retention_days == 730
    assert Settings.from_env({}).llm_cache_retention_days == 730


def test_cb05_retention_cannot_exceed_policy():
    with pytest.raises(ValueError):
        Settings.from_env({"LLM_CACHE_RETENTION_DAYS": "900"})
    with pytest.raises(ValueError):
        Settings.from_env({"PERSON_LEVEL_RETENTION_DAYS": "800"})
    with pytest.raises(ValueError):
        Settings.from_env({"LOG_RETENTION_DAYS": "400"})
    assert Settings.from_env({"LLM_CACHE_RETENTION_DAYS": "90"}).llm_cache_retention_days == 90


def test_cb05_evidence_links_and_purge_for_evidence():
    s = LLMStore(":memory:")
    s.cache_put("k1", {"a": 1}, "m", evidence_id="ev_a")
    s.cache_put("k2", {"a": 2}, "m", evidence_id="ev_b")
    s.link_evidence("k2", "ev_c")
    s.cache_put("k3", {"a": 3}, "m")
    assert s.cache_evidence("k2") == ["ev_b", "ev_c"]
    assert s.purge_for_evidence(["ev_c"], dry_run=True) == 1
    assert s.purge_for_evidence(["ev_c", "ev_unknown"]) == 1
    assert s.cache_get("k2") is None and s.cache_evidence("k2") == []
    assert s.cache_get("k1") is not None and s.cache_get("k3") is not None


def test_cb05_find_and_purge_containing_pseudonym():
    s = LLMStore(":memory:")
    s.cache_put("k1", {"quote": "thanks @p_0123456789abcdef"}, "m")
    s.cache_put("k2", {"quote": "nothing"}, "m")
    assert [r["key"] for r in s.find_containing(["p_0123456789abcdef"])] == ["k1"]
    assert s.purge_containing(["p_0123456789abcdef"]) == 1
    assert s.find_containing(["p_0123456789abcdef"]) == []


def test_cb05_ledger_purged_after_retention():
    clock = Clock(T0)
    s = LLMStore(":memory:", retention_days=10, clock=clock)
    s._db.execute(
        "INSERT INTO llm_usage (ts, backend, job, model, prompt_id, prompt_version, status)"
        " VALUES (?, 'api', 'j', 'm', 'p', '1', 'ok')",
        ((T0 - timedelta(days=11)).isoformat(),),
    )
    assert s.purge_ledger(dry_run=True) == 1
    assert s.purge_ledger() == 1


def test_cb05_old_cache_files_are_upgraded(tmp_path: Path):
    path = tmp_path / "llm.sqlite3"
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE llm_cache (key TEXT PRIMARY KEY, output_json TEXT NOT NULL,"
        " model TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    db.execute("INSERT INTO llm_cache VALUES ('old', '{}', 'm', ?)", (T0.isoformat(),))
    db.commit()
    db.close()
    s = LLMStore(path, clock=Clock(T0 + timedelta(days=1)))
    assert s.cache_get("old") == ({}, "m")
    cols = {r[1] for r in s._db.execute("PRAGMA table_info(llm_cache)")}
    assert "retention_class" in cols
    LLMStore(path)  # idempotent


def test_cb05_client_links_evidence_on_put_and_on_hit(prompt):
    c = make_client([{"value": 1, "label": "a"}])
    c.complete(prompt, "same", Echo, job="j", evidence_id="ev_1")
    again = c.complete(prompt, "same", Echo, job="j", evidence_id="ev_2")
    assert again.cached
    keys = [r[0] for r in c.store._db.execute("SELECT key FROM llm_cache")]
    assert c.store.cache_evidence(keys[0]) == ["ev_1", "ev_2"]
    assert c.store.purge_for_evidence(["ev_2"]) == 1
