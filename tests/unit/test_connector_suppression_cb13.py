"""DPIA CB-13: connectors drop records of people and repos on the refusal list at ingest."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from pigtail.capture.runs import RunRecorder
from pigtail.capture.snapshots import LocalSnapshotStore, SnapshotMeta
from pigtail.connectors.gharchive import GHArchiveConnector
from pigtail.privacy.suppression import (
    Suppressions,
    platform_namespace,
    subject_fingerprint,
    subject_pseudonym,
)

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "gharchive" / "2026-09-20-0.json.gz"
META = SnapshotMeta(
    source="gharchive",
    url="https://data.gharchive.org/2026-09-20-0.json.gz",
    fetched_at=datetime(2026, 9, 20, 1, tzinfo=UTC),
    collector_version="gharchive/0.1.0",
    terms_basis="TM-01",
)


def conn(tmp_path: Path, pz, sup: Suppressions | None = None, run=None) -> GHArchiveConnector:
    return GHArchiveConnector(
        store=LocalSnapshotStore(tmp_path), pseudonymizer=pz, env={}, suppression=sup, run=run
    )


def test_cb13_adr071_1_suppressed_fingerprint_dropped_at_ingest(tmp_path, pz):
    """ADR-071.1: the opt-out fingerprint is matched in memory at ingest; records keep no actor."""
    data = FIX.read_bytes()
    c = conn(tmp_path, pz)
    p = subject_fingerprint(pz, "github", "user0001")
    subject = [r for r, fps in c.subject_records(data, META) if p in fps]
    assert subject
    base = list(c.records(data, META))
    assert all(r["actor"] is None for r in base)  # Directive §8.1: no handle, no pseudonym
    run = RunRecorder("t", detect_commit=False)
    kept = list(conn(tmp_path, pz, Suppressions(persons=frozenset({p})), run).records(data, META))
    dropped = len(base) - len(kept)
    assert dropped == len(subject) and run.counts["gharchive.suppressed"] == dropped


def test_cb13_opted_out_repo_dropped_at_ingest(tmp_path, pz):
    data = FIX.read_bytes()
    sup = Suppressions(repos=frozenset({"github:1000001"}))
    kept = list(conn(tmp_path, pz, sup).records(data, META))
    assert kept and all(r["repo_id"] != 1000001 for r in kept)


def test_cb13_empty_list_changes_nothing(tmp_path, pz):
    data = FIX.read_bytes()
    assert list(conn(tmp_path, pz).records(data, META)) == list(
        conn(tmp_path, pz, Suppressions()).records(data, META)
    )


def test_cb13_subject_pseudonym_matches_connector_namespace(pz):
    assert subject_pseudonym(pz, "github", "@User0001") == pz.pseudonym("user0001", "github")
    assert platform_namespace("github") == GHArchiveConnector.handle_namespace
    with pytest.raises(ValueError):
        platform_namespace("myspace")
    with pytest.raises(ValueError):
        subject_pseudonym(pz, "github", "  @ ")
