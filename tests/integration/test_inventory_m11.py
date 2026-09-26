"""M11 (WORK_ORDER §4.4, ADR-047.6): `pigtail report inventory` — data-cache inventory, counts
only, read-only. Synthetic rows only (fake repos org-a/repo-1, org-b/repo-2)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from pigtail.capture.inventory import TABLES, inventory, render_text
from pigtail.cli import main

pytestmark = pytest.mark.db

T0 = datetime(2026, 9, 20, 12, tzinfo=UTC)


def _seed(db: Any) -> None:
    x = db.conn.execute
    x(
        "INSERT INTO repos (id, host, host_id, full_name, first_seen_at) VALUES"
        " ('github:1000001', 'github', 1000001, 'org-a/repo-1', %s),"
        " ('github:1000002', 'github', 1000002, 'org-b/repo-2', %s)",
        (T0, T0 + timedelta(days=2)),
    )
    x(
        "INSERT INTO cases (id, repo_id, opened_at, trigger, status) VALUES"
        " ('case_00000000000000000001', 'github:1000001', %s, 'manual', 'live')",
        (T0,),
    )
    for i in range(5):
        x(
            "INSERT INTO repo_star_daily (repo_host_id, day, stars_net, week_label,"
            " day_boundary_tz, is_partial, fetched_at) VALUES (1000001, %s, %s, 'w', 'x', false,"
            " %s)",
            (date(2026, 9, 1) + timedelta(days=i), i, T0),
        )


def test_m11_inventory_counts_only_and_covers_every_table(capture_db, pg_url):
    _seed(capture_db)
    inv = inventory(capture_db.conn)
    assert inv.unlisted == () and inv.missing == ()  # the spec covers the migrated schema
    assert inv.data_version is not None and inv.data_version >= "0014"
    by = {t.table: t for t in inv.tables}
    assert set(by) == {s.table for s in TABLES}
    assert (by["repos"].rows, by["repos"].distinct_repos) == (2, 2)
    assert (by["repos"].first_day, by["repos"].last_day) == (date(2026, 9, 20), date(2026, 9, 22))
    assert (by["cases"].rows, by["cases"].distinct_repos) == (1, 1)
    sd = by["repo_star_daily"]
    assert (sd.rows, sd.distinct_repos, sd.first_day, sd.last_day) == (
        5, 1, date(2026, 9, 1), date(2026, 9, 5),
    )  # fmt: skip
    assert by["hn_rank_poll"].distinct_repos is None and by["hn_rank_poll"].first_day is None
    text = render_text(inv, "abc123")
    dump = json.dumps(inv.to_dict())
    for out in (text, dump):  # no names, ids or handles: counts and dates only
        assert "org-a" not in out and "repo-1" not in out and "github:" not in out
        assert "case_" not in out


def test_m11_cli_report_inventory_is_read_only(capture_db, pg_url, monkeypatch, capsys):
    _seed(capture_db)
    monkeypatch.setenv("DATABASE_URL", pg_url)
    before = capture_db.conn.execute("SELECT count(*) FROM runs").fetchone()[0]
    assert main(["report", "inventory", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert {"code_commit", "data_version", "tables", "unlisted", "missing"} <= set(out)
    assert next(t for t in out["tables"] if t["table"] == "repos")["rows"] == 2
    assert main(["report", "inventory"]) == 0
    text = capsys.readouterr().out
    assert "| repos | cache | 2 | 2 |" in text and "org-a" not in text
    assert capture_db.conn.execute("SELECT count(*) FROM runs").fetchone()[0] == before
    monkeypatch.delenv("DATABASE_URL")
    assert main(["report", "inventory"]) == 2
