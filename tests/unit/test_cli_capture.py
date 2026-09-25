"""R14.1 / R1.1: `pigtail capture scan` and `db migrate` wiring; other stages still pending."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pigtail.cli import _parse_hour, build_parser, main


def test_r1_1_capture_scan_args():
    a = build_parser().parse_args(
        [
            "capture",
            "scan",
            "--start",
            "2026-09-20T00",
            "--end",
            "2026-09-21T00",
            "--min-stars",
            "50",
        ]
    )
    assert a.start == datetime(2026, 9, 20, tzinfo=UTC)
    assert a.end == datetime(2026, 9, 21, tzinfo=UTC)
    assert (a.min_stars, a.sigma, a.force) == (50, 3.0, False)


def test_parse_hour_formats():
    assert _parse_hour("2026-09-20T05") == datetime(2026, 9, 20, 5, tzinfo=UTC)
    assert _parse_hour("2026-09-20T05:00:00+00:00") == datetime(2026, 9, 20, 5, tzinfo=UTC)
    with pytest.raises(Exception):  # noqa: B017
        _parse_hour("yesterday")


def test_capture_scan_requires_env(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert main(["capture", "scan", "--start", "2026-09-20T00", "--end", "2026-09-20T01"]) == 2
    monkeypatch.setenv("DATABASE_URL", "postgresql://x@localhost:1/x")
    monkeypatch.delenv("PSEUDONYM_KEY", raising=False)
    assert main(["capture", "scan", "--start", "2026-09-20T00", "--end", "2026-09-20T01"]) == 2


def test_other_stages_still_pending(capsys):
    assert main(["score"]) == 2
    assert "M5" in capsys.readouterr().err
