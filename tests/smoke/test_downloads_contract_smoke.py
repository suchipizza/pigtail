"""M23b (ADR-090): live contract check of the npm downloads API, the npm registry manifest and
pypistats.org as pigtail calls them, on well-known public packages (`left-pad` on npm, whose
repository is `stevemao/left-pad`; `requests` on PyPI). Runs only with PIGTAIL_RUN_SMOKE=1;
free, unauthenticated, four requests (pypistats at its etiquette pace); nothing is stored beyond
a temporary snapshot directory. It guards the request and answer shapes a fake cannot prove.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pigtail.briefs.downloads import points_to
from pigtail.capture.snapshots import LocalSnapshotStore
from pigtail.connectors.downloads import (
    NpmDownloadsConnector,
    PypiStatsConnector,
    parse_npm_latest,
    parse_npm_range,
    parse_pypistats_overall,
)

pytestmark = [
    pytest.mark.smoke,
    pytest.mark.skipif(
        os.environ.get("PIGTAIL_RUN_SMOKE") != "1", reason="set PIGTAIL_RUN_SMOKE=1"
    ),
]


def test_npm_downloads_and_registry_live(tmp_path: Path) -> None:
    c = NpmDownloadsConnector(store=LocalSnapshotStore(tmp_path / "snap"), env={})
    end = datetime.now(UTC).date() - timedelta(days=3)
    f = c.downloads_range("left-pad", end - timedelta(days=6), end)
    assert f is not None
    s = parse_npm_range(f.data)
    assert s.package == "left-pad" and len(s.days) == 7
    assert sum(s.days.values()) > 0
    latest = c.latest("left-pad")
    assert latest is not None
    meta = parse_npm_latest(latest.data)
    assert points_to(meta["repository_url"], "stevemao/left-pad")
    assert c.downloads_range("zz-pigtail-nonexistent-pkg-q9", end, end) is None


def test_pypistats_overall_live(tmp_path: Path) -> None:
    c = PypiStatsConnector(store=LocalSnapshotStore(tmp_path / "snap"), env={})
    f = c.overall("requests")
    assert f is not None
    s = parse_pypistats_overall(f.data)
    assert s.package == "requests"
    assert len(s.days) >= 150  # ~180 days retained
    assert max(s.days) >= datetime.now(UTC).date() - timedelta(days=5)
