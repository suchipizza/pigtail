"""R14.1: capture CLI wiring after M11; other stages still pending."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pigtail.cli import _parse_hour, build_parser, main


def test_m11_capture_scan_and_gharchive_backfill_removed(capsys):
    """M11 (ADR-047.6): the global GH Archive velocity scan and its backfill have no command."""
    for cmd in ("scan", "backfill-gharchive"):
        with pytest.raises(SystemExit) as e:
            build_parser().parse_args(["capture", cmd])
        assert e.value.code == 2
        assert "invalid choice" in capsys.readouterr().err
    # the kept capture commands still parse
    a = build_parser().parse_args(["capture", "hn-ranks", "--loop", "--max-polls", "2"])
    assert a.loop and a.max_polls == 2
    a = build_parser().parse_args(["capture", "purge-raw", "--retention-days", "3"])
    assert a.retention_days == 3


def test_parse_hour_formats():
    assert _parse_hour("2026-09-20T05") == datetime(2026, 9, 20, 5, tzinfo=UTC)
    assert _parse_hour("2026-09-20T05:00:00+00:00") == datetime(2026, 9, 20, 5, tzinfo=UTC)
    with pytest.raises(Exception):  # noqa: B017
        _parse_hour("yesterday")


def test_other_stages_still_pending(capsys):
    assert main(["score"]) == 2
    assert "M5" in capsys.readouterr().err


def test_cb02_deletion_sync_requires_pseudonym_key(monkeypatch, capsys):
    """Without PSEUDONYM_KEY, deletion-sync fails with a clear usage error, not a crash."""
    from pigtail.cli import main

    monkeypatch.delenv("PSEUDONYM_KEY", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://127.0.0.1:1/none")
    rc = main(["privacy", "deletion-sync", "--dry-run"])
    assert rc != 0
    assert "PSEUDONYM_KEY" in capsys.readouterr().err
