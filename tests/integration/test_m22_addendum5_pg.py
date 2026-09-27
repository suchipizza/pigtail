"""ADR-085 addendum 5 on Postgres (verifier M22 round 8; synthetic data, fakes only, no network).

- R1: the Product Hunt topic hits are computed once, by the first invocation that completes the
  topic scans, for every shortlisted repo, and frozen in the run's checkpoint; a resume never
  reads the cache's index again (the verifier's probe: a post another run caches after the
  first invocation must not reach a pending repo).
- R2: planned scan rows purged before the hits are frozen re-plan the topic; rows found missing
  while the index is read leave the topic `failed:plan_rows_missing` (incomplete, refused above
  10 %), and the next invocation re-plans it; once frozen, missing rows don't matter.
- The estimate measures the cache's coverage for the run's own window (its `run_date`).
- Bluesky reads every page to the list's end, so the earliest launch-worded post before the
  window is stored; the page cap still makes the repo incomplete.
- resolveHandle: only a 400 "Unable to resolve handle" is an unresolvable account; any other 400
  makes the repo incomplete.

Every name, handle and text is made up (`org-c…`, `org-x…`, `SYNTH-…`).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from pigtail.briefs import ph_cache
from pigtail.briefs import runner as runner_mod
from pigtail.briefs.candidates import CandidateStore
from pigtail.briefs.discovery import window_bounds
from pigtail.briefs.estimate import selection_state
from pigtail.briefs.launch_sources import (
    BSKY_SOURCE,
    PH_HITS_FROZEN,
    PH_SOURCE,
    LaunchSourceIncomplete,
)
from pigtail.briefs.ph_cache import ph_key_hash
from pigtail.connectors.base import FetchError
from tests.integration.test_m22_ph_cache_pg import (
    C1,
    C2,
    C3,
    TOPICS,
    T,
    W,
    ph_signals,
    posts,
    run,
    setup,
    t,
)
from tests.integration.test_m22_round7_pg import HANDLE_C, TEN, run_bsky
from tests.launch_sources_fake import FakeBluesky, FakeProductHunt, bsky_post, ph_post

pytestmark = pytest.mark.db


def pause_after_the_topic_scan(capture_db: Any, tmp_path: Any) -> tuple[Any, Any, Any, dict]:
    """A first invocation that completes the topic scans, then stops on a failed slug lookup
    (the first repo's), before any repo is done: every repo is still pending."""
    fake = FakeProductHunt(posts())
    fake.fail_slugs = {"cachelint"}
    b, cands = setup(capture_db, tmp_path)
    cp: dict[str, Any] = {}
    with pytest.raises(FetchError):
        run(capture_db, b, cands, fake, tmp_path, checkpoint=cp)
    fake.fail_slugs = set()
    return fake, b, cands, cp


# --- R1: the topic hits are frozen -------------------------------------------------------------
def test_a_post_cached_after_the_first_invocation_never_reaches_a_pending_repo(
    capture_db, tmp_path
):
    fake, b, cands, cp = pause_after_the_topic_scan(capture_db, tmp_path)
    assert cp[PH_HITS_FROZEN] is True and not cp.get("ph_done")
    assert cp["ph_topic_hits"] == {f"gh:{C1}": ["7001"], f"gh:{C2}": ["7002"]}
    assert cp["ph_topic_provenance"]["repos_indexed"] == 3
    # the verifier's probe: meanwhile another run caches a post whose name is the pending repo
    # C3's, created inside this window and seen after this run's scans started
    fake.posts.append(
        ph_post("7099", "Latecomer", slug="latecomer-x", created=t(2025, 7, 1),
                description="SYNTH-DESC github.com/org-c3/latecomer")
    )  # fmt: skip
    seen = T + timedelta(hours=1)
    capture_db.conn.execute(
        "INSERT INTO ph_topic_post (topic, post_id, name_key_sha256, created_at, featured_at,"
        " first_seen_at, last_seen_at) VALUES ('open-source', '7099', %s, %s, NULL, %s, %s)",
        (ph_key_hash("latecomer"), t(2025, 7, 1), seen, seen),
    )
    n = len(fake.topic_requests)
    r = run(capture_db, b, cands, fake, tmp_path, checkpoint=cp, now=T + timedelta(hours=2))
    assert r.topic_hits_frozen and len(fake.topic_requests) == n and r.topic_requests == 0
    sig = ph_signals(capture_db, b)
    assert sig[C3] == ("complete", [])  # the frozen hits: 7099 is not matched
    assert sig[C1] == ("complete", [("7001", "topic", True)])
    assert sig[C2] == ("complete", [("7002", "topic", True)])
    assert r.topic_status == {"open-source": "complete", "developer-tools": "complete"}
    assert r.topic_cache == cp["ph_topic_provenance"]["topic_cache"] and r.topic_cache
    # control: a fresh run over the same cache does match it (the probe is a real match)
    b2, c2 = setup(capture_db, tmp_path, version=2)
    run(capture_db, b2, c2, fake, tmp_path, now=T + timedelta(hours=3))
    assert ph_signals(capture_db, b2)[C3] == ("complete", [("7099", "topic", True)])


def test_frozen_hits_survive_purged_cache_rows(capture_db, tmp_path):
    fake, b, cands, cp = pause_after_the_topic_scan(capture_db, tmp_path)
    capture_db.conn.execute("DELETE FROM ph_topic_post")
    capture_db.conn.execute("DELETE FROM ph_topic_scan")
    n = len(fake.topic_requests)
    r = run(capture_db, b, cands, fake, tmp_path, checkpoint=cp, now=T + timedelta(days=40))
    assert len(fake.topic_requests) == n and r.incomplete == 0 and not r.topic_replanned
    assert ph_signals(capture_db, b)[C1] == ("complete", [("7001", "topic", True)])
    assert r.topic_status == {"open-source": "complete", "developer-tools": "complete"}


def test_a_checkpoint_without_the_marker_computes_the_hits_once_and_freezes_them(
    capture_db, tmp_path
):
    fake, b, cands, cp = pause_after_the_topic_scan(capture_db, tmp_path)
    hits = dict(cp["ph_topic_hits"])
    for k in (PH_HITS_FROZEN, "ph_topic_hits", "ph_topic_provenance"):
        cp.pop(k)  # as a checkpoint written before addendum 5
    r = run(capture_db, b, cands, fake, tmp_path, checkpoint=cp, now=T + timedelta(hours=1))
    assert r.topic_hits_frozen and cp[PH_HITS_FROZEN] is True and cp["ph_topic_hits"] == hits
    assert ph_signals(capture_db, b)[C1] == ("complete", [("7001", "topic", True)])


# --- R2: missing planned scan rows ------------------------------------------------------------
def test_planned_rows_purged_before_the_hits_are_frozen_replan_the_topic(capture_db, tmp_path):
    fake = FakeProductHunt(posts())
    fake.fail_topic_at = {2, 3}  # February's second page fails (the run pauses mid-scan)
    b, cands = setup(capture_db, tmp_path)
    cp: dict[str, Any] = {}
    with pytest.raises(FetchError):
        run(capture_db, b, cands, fake, tmp_path, checkpoint=cp)
    assert PH_HITS_FROZEN not in cp and "open-source" in cp["ph_cache"]
    # the retention purge removes the planned rows (and the posts they saw) meanwhile
    capture_db.conn.execute("DELETE FROM ph_topic_post")
    capture_db.conn.execute("DELETE FROM ph_topic_scan")
    fake.fail_topic_at = set()
    r = run(capture_db, b, cands, fake, tmp_path, checkpoint=cp, now=T + timedelta(hours=1))
    # the topic is planned again and scanned, never read as complete with no rows
    assert r.topic_replanned == ["open-source"]
    assert r.topic_status == {"open-source": "complete", "developer-tools": "complete"}
    assert ph_signals(capture_db, b)[C1] == ("complete", [("7001", "topic", True)])
    assert cp[PH_HITS_FROZEN] is True


def test_rows_missing_while_the_index_is_read_leave_the_topic_incomplete(
    capture_db, tmp_path, monkeypatch
):
    fake = FakeProductHunt(posts())
    b1, c1 = setup(capture_db, tmp_path)
    run(capture_db, b1, c1, fake, tmp_path)
    (june,) = capture_db.conn.execute(
        "SELECT id FROM ph_topic_scan WHERE topic = 'open-source' AND posted_after = %s",
        (t(2025, 6, 1, 0),),
    ).fetchone()
    # a shifted window reuses June; the row is purged while this invocation scans its gaps
    shifted = (W[0] + timedelta(days=5), W[1] + timedelta(days=5))
    original = ph_cache.save_page

    def purging(*a: Any, **kw: Any) -> Any:
        out = original(*a, **kw)
        capture_db.conn.execute("DELETE FROM ph_topic_scan WHERE id = %s", (june,))
        return out

    monkeypatch.setattr(ph_cache, "save_page", purging)
    b2, c2 = setup(capture_db, tmp_path, version=2)
    cp: dict[str, Any] = {}
    with pytest.raises(LaunchSourceIncomplete, match="planned again"):
        run(capture_db, b2, c2, fake, tmp_path, window=shifted, now=T + timedelta(hours=1),
            checkpoint=cp)  # fmt: skip
    reasons = {
        s["reason"]
        for c in CandidateStore(capture_db.conn, b2.brief_id, 2).all()
        for s in c.sources
        if s["source"] == PH_SOURCE
    }
    assert reasons == {"topic_plan_rows_missing"}
    assert PH_HITS_FROZEN not in cp and "open-source" not in cp["ph_cache"]
    assert not cp.get("ph_done")
    monkeypatch.undo()
    # the next invocation plans open-source again (June is a gap now) and completes
    n = len(fake.topic_requests)
    r = run(capture_db, b2, c2, fake, tmp_path, window=shifted, now=T + timedelta(hours=2),
            checkpoint=cp)  # fmt: skip
    assert {v["postedAfter"] for v in fake.topic_requests[n:]} >= {t(2025, 6, 1, 0).isoformat()}
    assert r.topic_status["open-source"] == "complete" and r.incomplete == 0
    assert ph_signals(capture_db, b2)[C1] == ("complete", [("7001", "topic", True)])


# --- the estimate measures the run's own window -------------------------------------------------
def test_the_estimate_measures_coverage_for_the_runs_window(capture_db, tmp_path, monkeypatch):
    fake = FakeProductHunt(posts())
    b, cands = setup(capture_db, tmp_path)
    window = window_bounds(b, T.date())
    run(capture_db, b, cands, fake, tmp_path, window=window)
    later = T + timedelta(days=5)
    # no run yet: today's window, five more days to scan
    today = selection_state(capture_db.conn, b, now=later)
    assert today.ph_cache is not None
    assert sum(today.ph_cache["open-source"]["gaps_days"]) > 5
    # a run whose checkpoint fixed its run date: that window, only the rest of that day left
    row = {"checkpoint": {"run_date": T.date().isoformat(), "selection": {}}, "stages": {}}
    monkeypatch.setattr(runner_mod, "find_run", lambda _c, _b: ("resume", row))
    st = selection_state(capture_db.conn, b, now=later)
    assert st.ph_cache is not None
    gap = sum(st.ph_cache["open-source"]["gaps_days"])
    assert 0 < gap < 1  # from the first scan's start (T) to the end of the run date
    assert st.ph_cache["open-source"]["window_days"] == pytest.approx(
        ph_cache.days(*window), abs=0.01
    )
    assert tuple(st.ph_cache) == TOPICS


# --- Bluesky: every page to the list's end ------------------------------------------------------
REPO0 = "org-x0/repo-00"


def bsky_launches(n: int) -> FakeBluesky:
    """`n` launch-worded posts of HANDLE_C linking repo 0, all before the window, one a month
    from January 2023, one per page (newest first)."""
    fake = FakeBluesky(
        [bsky_post(900 + i, HANDLE_C, t(2023, 1 + i, 1), [f"https://github.com/{REPO0}"])
         for i in range(n)]
    )  # fmt: skip
    fake.page_size = 1
    return fake


def bsky_signal(capture_db: Any, b: Any) -> dict[str, Any]:
    (c,) = [c for c in CandidateStore(capture_db.conn, b.brief_id, 1).all()
            if c.repo_full_name == REPO0]  # fmt: skip
    (s,) = [x for x in c.sources if x["source"] == BSKY_SOURCE]
    return dict(s)


def test_bluesky_reads_to_the_end_for_the_earliest_launch_before_the_window(
    capture_db, tmp_path, monkeypatch
):
    fake = bsky_launches(3)
    b, fake, _gh, res, _cp = run_bsky(capture_db, tmp_path, monkeypatch, TEN, fake=fake,
                                      readmes={REPO0: f"@{HANDLE_C}".encode()})  # fmt: skip
    s = bsky_signal(capture_db, b)
    assert s["status"] == "complete"
    # all three pages read (the first launch-worded post met is the newest, 2023-03-01)
    assert sorted(p["time"][:10] for p in s["posts"]) == ["2023-01-01", "2023-02-01", "2023-03-01"]
    searches = [r for r in fake.requests if r.url.path.endswith("searchPosts")]
    assert len(searches) == 3
    assert res.fetch["bluesky"]["launch_posts_before_window"] == 3


def test_bluesky_page_cap_still_makes_the_repo_incomplete(capture_db, tmp_path, monkeypatch):
    fake = bsky_launches(6)  # six pages, the cap is five
    b, fake, _gh, res, _cp = run_bsky(capture_db, tmp_path, monkeypatch, TEN, fake=fake,
                                      readmes={REPO0: f"@{HANDLE_C}".encode()})  # fmt: skip
    s = bsky_signal(capture_db, b)
    assert s["status"] == "incomplete" and s["reason"] == "search_capped"
    assert res.fetch["bluesky"]["incomplete_reasons"] == {"search_capped": 1}


# --- resolveHandle: only "Unable to resolve handle" is unresolvable -----------------------------
def test_another_resolve_handle_400_makes_the_repo_incomplete(capture_db, tmp_path, monkeypatch):
    fake = FakeBluesky([])
    fake.resolve_bad_request = True
    _b, fake, _gh, res, _cp = run_bsky(capture_db, tmp_path, monkeypatch, TEN, fake=fake,
                                       readmes={REPO0: f"@{HANDLE_C}".encode()})  # fmt: skip
    bs = res.fetch["bluesky"]
    assert bs["incomplete"] == 1 and bs["incomplete_reasons"] == {"resolve_failed": 1}
    assert bs["accounts_unresolvable"] == 0
    assert not [r for r in fake.requests if r.url.path.endswith("searchPosts")]
