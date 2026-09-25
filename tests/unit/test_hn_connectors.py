"""M1-T4 (R1.2, R2.1), M1-T14 and ADR-022 gating: HN connectors on synthetic fixtures only.

No network: `tests/hn_fake.py` serves tests/fixtures/hn/ (fake usernames hnuserNNN).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

import pigtail.connectors.hn_ranks as hn_ranks_module
from pigtail.capture.hn_ranks import check_interval, run_loop
from pigtail.capture.mentions import build_queries, classify_mention, split_full_name
from pigtail.capture.snapshots import LocalSnapshotStore
from pigtail.connectors.base import (
    ADR022_ENV,
    USER_AGENT,
    Clearance,
    ConnectorDisabled,
    PersonSourceHold,
    TokenBucket,
)
from pigtail.connectors.hn import (
    HITS_CAP,
    AlgoliaQuery,
    HNAlgoliaConnector,
    HNFirebaseConnector,
    github_repos_in_text,
    item_url,
    normalize_github_repo,
)
from pigtail.connectors.hn_ranks import HNRanksConnector
from pigtail.connectors.registry import CONNECTORS
from pigtail.privacy.deletion_sync import (
    BLUESKY_POLICY,
    HN_POLICY,
    UpstreamSignal,
    UpstreamState,
    _hn_state,
)
from tests.hn_fake import FakeHN

NOW = datetime(2026, 9, 25, 12, tzinfo=UTC)
ON = {"PIGTAIL_ENABLE_HN": "1", ADR022_ENV: "1"}


def make(cls: Any, tmp_path: Any, pz: Any, fake: FakeHN, env: dict[str, str] | None = None) -> Any:
    return cls(
        store=LocalSnapshotStore(tmp_path / "snap"),
        pseudonymizer=pz,
        http=fake.client(),
        env=ON if env is None else env,
        limiter=TokenBucket(1000, burst=100),
        clock=lambda: NOW,
    )


# --- enable flags and the ADR-022 hold -----------------------------------------------------------
@pytest.mark.parametrize("cls", [HNFirebaseConnector, HNAlgoliaConnector])
def test_m1_t4_hn_connectors_disabled_by_default(cls, tmp_path, pz):
    c = make(cls, tmp_path, pz, FakeHN(), env={})
    assert c.enabled is False
    with pytest.raises(ConnectorDisabled, match="PIGTAIL_ENABLE_HN"):
        c.fetch(item_url(9000001))
    assert make(cls, tmp_path, pz, FakeHN(), env={"PIGTAIL_ENABLE_HN": "0"}).enabled is False


@pytest.mark.parametrize("cls", [HNFirebaseConnector, HNAlgoliaConnector])
def test_adr022_enabling_hn_without_operator_flag_fails_clearly(cls, tmp_path, pz):
    with pytest.raises(PersonSourceHold) as ei:
        make(cls, tmp_path, pz, FakeHN(), env={"PIGTAIL_ENABLE_HN": "1"})
    msg = str(ei.value)
    assert ADR022_ENV in msg and "ADR-022" in msg
    for cb in ("CB-01", "CB-02", "CB-03", "CB-06", "CB-08", "CB-12", "CB-13"):
        assert cb in msg
    with pytest.raises(PersonSourceHold):  # an explicit enabled=True is held too
        cls(store=LocalSnapshotStore(tmp_path), pseudonymizer=pz, enabled=True, env={})
    with pytest.raises(PersonSourceHold):
        make(cls, tmp_path, pz, FakeHN(), env={"PIGTAIL_ENABLE_HN": "1", ADR022_ENV: "yes"})
    assert make(cls, tmp_path, pz, FakeHN(), env=ON).enabled is True


def test_m1_t4_per_connector_flag_overrides_group_flag(tmp_path, pz):
    env = {**ON, "PIGTAIL_CONNECTOR_HN_ALGOLIA_ENABLED": "false"}
    assert make(HNAlgoliaConnector, tmp_path, pz, FakeHN(), env=env).enabled is False
    assert make(HNFirebaseConnector, tmp_path, pz, FakeHN(), env=env).enabled is True


def test_m1_t14_rank_connector_needs_no_adr022_flag(tmp_path):
    for env in ({}, {"PIGTAIL_ENABLE_HN": "0"}):
        c = make(HNRanksConnector, tmp_path, None, FakeHN(), env=env)
        assert c.enabled is True and c.person_level_hold is False and c.handle_fields == ()
    off = {"PIGTAIL_CONNECTOR_HN_RANKS_ENABLED": "false"}
    assert make(HNRanksConnector, tmp_path, None, FakeHN(), env=off).enabled is False
    doc = hn_ranks_module.__doc__ or ""
    assert "can't be backfilled" in doc and "stores no person-level data" in doc


def test_m1_t4_terms_metadata_and_rate_limits(tmp_path, pz):
    for cls, memo in ((HNAlgoliaConnector, "TM-03"), (HNFirebaseConnector, "TM-04")):
        t = cls.terms
        assert t.clearance is Clearance.CLEARED_WITH_CONDITIONS
        assert memo in t.terms_basis and "LQ-6" in t.terms_basis
        assert cls.retention_class == "person_level_24m" and cls.handle_namespace == "hn"
    assert "TM-04" in HNRanksConnector.terms.terms_basis
    alg = HNAlgoliaConnector(store=LocalSnapshotStore(tmp_path), pseudonymizer=pz, env={})
    assert alg.limiter.rate * 3600 <= 10_000 * 0.5 + 1e-6  # TM-03 cap with a 50 % margin
    fb = HNFirebaseConnector(store=LocalSnapshotStore(tmp_path), pseudonymizer=pz, env={})
    assert fb.limiter.rate <= 2.0
    assert {"hn_firebase", "hn_algolia", "hn_ranks"} <= set(CONNECTORS)


# --- Firebase ------------------------------------------------------------------------------------
def test_m1_t4_firebase_item_snapshot_then_pseudonymized_record(tmp_path, pz):
    fake = FakeHN()
    fb = make(HNFirebaseConnector, tmp_path, pz, fake)
    f, rec = fb.fetch_item(9000001)
    assert fb.store.get(f.content_hash) == f.data  # raw JSON snapshotted
    assert b"hnuser001" in f.data  # raw stays raw (private store only)
    assert f.evidence.retention_class == "person_level_24m"
    assert rec is not None
    assert rec["by"] == pz.pseudonym("hnuser001", "hn")
    assert rec["repo_full_names"] == ["org-a/repo-1"]
    assert rec["evidence_type"] == "community_post" and rec["capture_mode"] == "api_json"
    assert "hnuser" not in json.dumps({k: v for k, v in rec.items() if k != "text"})
    assert fake.requests[-1].headers["User-Agent"] == USER_AGENT
    _, com = fb.fetch_item(9000011)  # HTML entities are unescaped for matching
    assert com is not None and com["repo_full_names"] == ["org-a/repo-1"]
    _, gone = fb.fetch_item(9000099)
    assert gone is None  # `null`


def test_m1_t4_firebase_story_lists(tmp_path, pz):
    fb = make(HNFirebaseConnector, tmp_path, pz, FakeHN())
    f, ids = fb.fetch_list("topstories")
    assert len(ids) == 500 and ids[0] == 9000001
    assert f.evidence.retention_class == "project_level"
    recs = list(fb.records(f.data, f.meta))
    assert recs[1] == {
        "list": "topstories",
        "rank": 2,
        "item_id": 9000004,
        "evidence_type": "platform_metric",
        "capture_mode": "api_json",
    }
    with pytest.raises(ValueError):
        fb.fetch_list("jobstories")  # type: ignore[arg-type]


# --- Algolia -------------------------------------------------------------------------------------
def test_m1_t4_algolia_search_pseudonymizes_and_drops_tags(tmp_path, pz):
    fake = FakeHN()
    alg = make(HNAlgoliaConnector, tmp_path, pz, fake)
    q = AlgoliaQuery("full_name", "org-a/repo-1")
    res = alg.search(q, since=datetime(2026, 9, 1, tzinfo=UTC), until=NOW)
    recs = [r for _, r in res.records]
    assert {r["item_id"] for r in recs} == {9000001, 9000011, 9000012, 9000003}
    for r in recs:
        assert r["author"].startswith("p_")
        assert "_tags" not in r and "_highlightResult" not in r
    assert "hnuser" not in json.dumps([{k: v for k, v in r.items() if k != "text"} for r in recs])
    story = next(r for r in recs if r["item_id"] == 9000001)
    assert story["front_page_tag"] and story["show_hn"] and story["type"] == "story"
    params = fake.requests[-1].url.params
    lo = int(datetime(2026, 9, 1, tzinfo=UTC).timestamp())
    assert params["numericFilters"] == f"created_at_i>={lo},created_at_i<{int(NOW.timestamp()) + 1}"
    assert params["tags"] == "(story,comment)" and "restrictSearchableAttributes" not in params
    assert res.pages[0].fetched.evidence.retention_class == "person_level_24m"


def test_m1_t4_algolia_url_query_restricted_to_url(tmp_path, pz):
    fake = FakeHN()
    alg = make(HNAlgoliaConnector, tmp_path, pz, fake)
    res = alg.search(AlgoliaQuery("url", "github.com/org-a/repo-1", "story", True), until=NOW)
    assert [r["item_id"] for _, r in res.records] == [9000001]
    assert fake.requests[-1].url.params["restrictSearchableAttributes"] == "url"


def _many_hits(n: int, start: int = 1790000000, step: int = 60) -> list[dict[str, Any]]:
    return [
        {
            "objectID": str(7_000_000 + i),
            "author": f"hnuser{i % 50:03d}",
            "created_at_i": start + i * step,
            "comment_text": "about org-a/repo-1",
            "_tags": ["comment", f"author_hnuser{i % 50:03d}"],
        }
        for i in range(n)
    ]


def test_m1_t4_algolia_pages_within_cap(tmp_path, pz):
    fake = FakeHN(hits=_many_hits(250))
    alg = make(HNAlgoliaConnector, tmp_path, pz, fake)
    res = alg.search(AlgoliaQuery("full_name", "org-a/repo-1"), until=NOW)
    assert [p.page for p in res.pages] == [0, 1, 2]
    assert len({r["item_id"] for _, r in res.records}) == 250
    assert res.truncated_windows == 0


def test_m1_t4_algolia_splits_windows_over_hit_cap(tmp_path, pz):
    fake = FakeHN(hits=_many_hits(1500))
    alg = make(HNAlgoliaConnector, tmp_path, pz, fake)
    since = datetime.fromtimestamp(1790000000, UTC)
    res = alg.search(AlgoliaQuery("full_name", "org-a/repo-1"), since=since, until=NOW)
    assert len({r["item_id"] for _, r in res.records}) == 1500  # nothing lost past the cap
    pages = [int(r.url.params["page"]) for r in fake.requests]
    assert max(pages) < HITS_CAP // 100  # never pages past the ~1,000-hit cap
    assert alg.run is None and res.truncated_windows == 0


def test_m1_t4_algolia_truncates_at_minimum_window(tmp_path, pz):
    fake = FakeHN(hits=_many_hits(1200, step=1))  # 1,200 hits within 20 minutes
    alg = make(HNAlgoliaConnector, tmp_path, pz, fake)
    since = datetime.fromtimestamp(1790000000, UTC)
    until = datetime.fromtimestamp(1790000000 + 1800, UTC)
    res = alg.search(AlgoliaQuery("full_name", "org-a/repo-1"), since=since, until=until)
    assert res.truncated_windows == 1
    assert len({r["item_id"] for _, r in res.records}) == HITS_CAP


# --- repo URLs and mention rules -----------------------------------------------------------------
@pytest.mark.parametrize(
    ("url", "want"),
    [
        ("https://github.com/org-a/repo-1", "org-a/repo-1"),
        ("http://www.github.com/Org-B/Repo-2.git", "org-b/repo-2"),
        ("github.com/org-a/repo-1/issues/12?x=1#y", "org-a/repo-1"),
        ("https://github.com/org-a/repo.js/tree/main", "org-a/repo.js"),
        ("https://github.com/org-a", None),
        ("https://github.com/topics/cli", None),
        ("https://github.com/sponsors/someone", None),
        ("https://gist.github.com/org-a/abc", None),
        ("https://org-a.github.io/repo-1", None),
        ("https://example.org/github.com/org-a/repo-1", None),
        ("", None),
        (None, None),
    ],
)
def test_m1_t14_normalize_github_repo(url, want):
    assert normalize_github_repo(url) == want


def test_m1_t4_repos_in_text():
    t = "see github.com/org-a/repo-1, and https://github.com/org-b/repo-2). Also github.com/org-a"
    assert github_repos_in_text(t) == ["org-a/repo-1", "org-b/repo-2"]


def test_r1_2_mention_classification():
    o, n = "org-a", "repo-1"
    assert classify_mention({"repo_full_names": ["org-a/repo-1"]}, o, n) == "url"
    assert classify_mention({"text": "I like org-a/repo-1."}, o, n) == "full_name"
    assert classify_mention({"title": "Repo-1 by org-a"}, o, n) == "name_and_owner"
    assert classify_mention({"text": "repo-1 alone"}, o, n) == "name"
    assert classify_mention({"text": "repo-10 and xorg-a"}, o, n) is None
    assert classify_mention({"text": "org-a/repo-1x"}, o, n) is None
    assert [q.label for q in build_queries(o, n)] == ["url", "full_name", "name_and_owner"]
    assert build_queries(o, n, loose=True)[-1].query == "repo-1"
    assert split_full_name("https://github.com/Org-A/Repo-1") == ("org-a", "repo-1")
    with pytest.raises(ValueError):
        split_full_name("not a repo")


# --- rank connector ------------------------------------------------------------------------------
def test_m1_t14_rank_records_never_hold_by(tmp_path):
    rc = make(HNRanksConnector, tmp_path, None, FakeHN(), env={})
    f = rc.fetch_story(8_000_007)
    assert b"hnfill007" in f.data  # raw JSON has `by` (the poller drops these bytes)
    assert f.evidence.retention_class == "person_level_24m"
    rec = rc.story_record(f)
    assert rec is not None
    assert set(rec) == {
        "item_id", "type", "url", "title", "score", "descendants", "time", "deleted", "dead",
        "repo_full_name", "evidence_type", "capture_mode",
    }  # fmt: skip
    assert "hnfill" not in json.dumps(rec)
    top, ids = rc.fetch_topstories()
    assert top.evidence.retention_class == "project_level" and len(ids) == 500


def test_m1_t14_interval_and_loop():
    with pytest.raises(ValueError, match="TM-04"):
        check_interval(59)
    calls: list[int] = []
    slept: list[float] = []

    def poll() -> None:
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("boom")

    errors: list[BaseException] = []
    polls, failures = run_loop(
        poll,
        interval=300,
        max_polls=3,
        sleep=slept.append,
        monotonic=lambda: 0.0,
        on_error=errors.append,
    )
    assert (polls, failures, len(errors)) == (3, 1, 1)
    assert slept == [300.0, 300.0]


# --- deletion sync pieces ------------------------------------------------------------------------
def test_cb02_hn_state_parsing():
    assert _hn_state(200, b'{"id": 1, "by": "x"}') is UpstreamState.PRESENT
    assert _hn_state(200, b'{"id": 1, "deleted": true}') is UpstreamState.DELETED
    assert _hn_state(200, b'{"id": 1, "dead": true}') is UpstreamState.DEAD
    assert _hn_state(200, b"null") is UpstreamState.MISSING
    assert _hn_state(404, b"") is UpstreamState.MISSING
    assert _hn_state(None, b"") is UpstreamState.UNKNOWN
    assert _hn_state(503, b"") is UpstreamState.UNKNOWN
    assert _hn_state(200, b"<html>") is UpstreamState.UNKNOWN


def test_cb02_policies_and_signals():
    assert HN_POLICY.act_within.days == 7 and HN_POLICY.mode == "poll"
    assert BLUESKY_POLICY.act_within.total_seconds() == 48 * 3600 and BLUESKY_POLICY.mode == "push"
    with pytest.raises(ValueError):
        UpstreamSignal(UpstreamState.DELETED)
    with pytest.raises(ValueError):
        UpstreamSignal(UpstreamState.DELETED, item_id="1", account="p_0000000000000001")


def test_cb02_check_stores_nothing_and_works_while_disabled(tmp_path, pz):
    fb = make(HNFirebaseConnector, tmp_path, pz, FakeHN(), env={})
    assert fb.enabled is False
    res = fb.check(item_url(9000013))
    assert res.status == 200 and json.loads(res.data)["deleted"] is True
    assert not (tmp_path / "snap").exists()
