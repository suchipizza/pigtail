"""M1-T22: `att.hn_frontpage_minutes` from the rank history, with polling gaps.

Synthetic rank history only (fake story ids, fake repos org-a/repo-1, org-b/repo-2).
Layout (minutes after P0; polls every 5 min, one 30-min outage):

    polls      0 5 10 ... 60 | gap 60..90 | 90 95 ... 120      now = 122
    story A    rank 5 at 0..40, rank 45 after             -> 9 covered segments = 45 min
    story B    rank 20 at 60, 90, 95, 100                  -> 15 min, 30 min uncovered (gap)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from pigtail.capture.hn_frontpage import (
    FrontpageConfig,
    repo_frontpage_minutes,
    segments,
    story_frontpage_minutes,
)
from pigtail.cli import main
from pigtail.privacy import suppression
from tests.conftest import TEST_KEY

P0 = datetime(2026, 9, 25, 10, tzinfo=UTC)
M = timedelta(minutes=1)
NOW = P0 + 122 * M
A, B, C = 9100001, 9100002, 9100003
POLLS = [P0 + m * M for m in [*range(0, 61, 5), *range(90, 121, 5)]]
HASH = "0" * 64


# --- pure segment logic (no database) ------------------------------------------------------------
def test_m1_t22_segments_covered_gap_tail_and_before_polling():
    cfg = FrontpageConfig()
    segs = segments(POLLS, P0 - 10 * M, NOW, NOW, cfg)
    assert segs[0].poll is None and segs[0].minutes == 10  # before the first poll
    gap = [s for s in segs if not s.covered and s.poll is not None]
    assert [(s.poll, s.minutes) for s in gap] == [(P0 + 60 * M, 30)]
    assert segs[-1].covered and segs[-1].minutes == 2  # tail to now, within 2 x interval
    assert sum(s.minutes for s in segs) == 132
    # poller down for longer than 2 x interval: the whole tail is uncovered
    late = segments(POLLS, P0, P0 + 200 * M, P0 + 200 * M, cfg)
    assert not late[-1].covered and late[-1].minutes == 80
    # no polls at all: one before-polling segment
    assert [(s.covered, s.poll) for s in segments([], P0, NOW, NOW, cfg)] == [(False, None)]
    with pytest.raises(ValueError):
        FrontpageConfig(interval=timedelta(seconds=30))


# --- database ------------------------------------------------------------------------------------
def seed(db: Any) -> None:
    for p in POLLS:
        db.conn.execute(
            "INSERT INTO hn_rank_poll (observed_at, n_items, content_hash) VALUES (%s, 3, %s)",
            (p, HASH),
        )
    for p in POLLS:
        m = int((p - P0) / M)
        rows = [(C, 1)]  # another repo's story, always at the top
        rows.append((A, 5 if m <= 40 else 45))
        if m in (60, 90, 95, 100):
            rows.append((B, 20))
        for iid, rank in rows:
            db.conn.execute(
                "INSERT INTO hn_rank_observation (item_id, observed_at, rank) VALUES (%s, %s, %s)",
                (iid, p, rank),
            )
    for iid, repo in ((A, "org-a/repo-1"), (B, "org-a/repo-1"), (C, "org-b/repo-2")):
        db.conn.execute(
            "INSERT INTO hn_story (item_id, type, title, repo_full_name, first_seen_at,"
            " last_seen_at) VALUES (%s, 'story', 'Synthetic story', %s, %s, %s)",
            (iid, repo, P0, NOW),
        )


@pytest.mark.db
def test_m1_t22_repo_minutes_with_gap_reported_as_uncovered(capture_db):
    db = capture_db
    seed(db)
    rep = repo_frontpage_minutes(db, "https://github.com/Org-A/Repo-1", now=NOW)
    assert rep.repo == "org-a/repo-1" and rep.polling_started_at == P0
    assert rep.minutes == 60 and rep.quality == "estimated" and rep.lower_bound is True
    assert (rep.window_minutes, rep.covered_minutes, rep.uncovered_minutes) == (122, 92, 30)
    assert rep.coverage == round(92 / 122, 4) and rep.before_polling_minutes == 0
    assert rep.gaps == [{"start": P0 + 60 * M, "end": P0 + 90 * M, "minutes": 30.0}]
    by_id = {s.item_id: s for s in rep.stories}
    assert (by_id[A].minutes, by_id[A].uncovered_minutes, by_id[A].best_rank) == (45, 0, 5)
    assert by_id[A].polls_on_front_page == 9 and by_id[A].last_on_front_page == P0 + 40 * M
    assert (by_id[B].minutes, by_id[B].uncovered_minutes) == (15, 30)  # the gap isn't counted
    assert C not in by_id


@pytest.mark.db
def test_m1_t22_windows_verified_unknown_and_before_polling(capture_db):
    db = capture_db
    seed(db)
    full = story_frontpage_minutes(db, [B], since=P0 + 90 * M, until=P0 + 100 * M, now=NOW)
    assert (full.minutes, full.quality, full.coverage, full.lower_bound) == (
        10,
        "verified",
        1.0,
        False,
    )
    mid = story_frontpage_minutes(db, [B], since=P0 + 62 * M, until=P0 + 95 * M, now=NOW)
    assert (mid.minutes, mid.uncovered_minutes, mid.stories[0].uncovered_minutes) == (5, 28, 28)
    early = story_frontpage_minutes(db, [A], since=P0 - 60 * M, until=P0 + 10 * M, now=NOW)
    assert (early.before_polling_minutes, early.minutes, early.quality) == (60, 10, "estimated")
    before = story_frontpage_minutes(db, [A], since=P0 - 3 * 60 * M, until=P0 - 60 * M, now=NOW)
    assert (before.minutes, before.quality, before.coverage) == (None, "unknown", 0.0)
    # a repo with no matched stories, inside polling coverage: 0, not unknown
    none = repo_frontpage_minutes(db, "org-z/none", since=P0, until=P0 + 30 * M, now=NOW)
    assert (none.minutes, none.quality) == (0, "verified")


@pytest.mark.db
def test_m1_t22_cli_report_is_read_only(capture_db, pg_url, monkeypatch, capsys, pz):
    db = capture_db
    seed(db)
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("PSEUDONYM_KEY", TEST_KEY)  # name opt-outs are keyed (CB-13b)
    runs_before = db.conn.execute("SELECT count(*) FROM runs").fetchone()
    argv = ["report", "hn-frontpage", "--repo", "org-a/repo-1"]
    assert main([*argv, "--since", "2026-09-25T10:00", "--until", "2026-09-25T12:00"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["minutes"] == 60 and out["uncovered_minutes"] == 30 and out["quality"] == "estimated"
    assert out["config"]["max_rank"] == 30 and out["window_start"].startswith("2026-09-25T10:00")
    assert db.conn.execute("SELECT count(*) FROM runs").fetchone() == runs_before  # no writes
    # opted-out repos are refused; `report` alone is still pending (M15)
    suppression.add(
        db, "repo_name", suppression.repo_name_key("org-a/repo-1", pz), platform="github",
        reason="objection",
    )  # fmt: skip
    assert main(argv) == 2 and "refusal list" in capsys.readouterr().err
    assert main(["report", "hn-frontpage", "--repo", "not a repo"]) == 2
    assert main(["report"]) == 2 and "M15" in capsys.readouterr().err
