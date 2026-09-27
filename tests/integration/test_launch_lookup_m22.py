"""M22 verifier round 3 on Postgres (ADR-081; synthetic data, fakes only, no network): the
selection stage looks up each shortlisted repo's Show HN / Launch HN posts, stores project-level
fields only, drops the raw pages, resumes per repo, anchors on a looked-up launch (a same-day
launch precedes a day-precision burst), and refuses a pre-registration recorded under the old
selection parameters. Every name here is made up (`org-k`, `org-m`, `org-n`, `hnuser8NN`).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from pigtail.briefs import selection as selmod
from pigtail.briefs.candidates import Candidate, CandidateStore
from pigtail.briefs.preregistration import PreregistrationMissing, require
from pigtail.briefs.preregistration import record as preregister
from pigtail.briefs.selection_store import run_stage, view
from pigtail.briefs.shortlist import Shortlist
from pigtail.capture.snapshots import LocalSnapshotStore
from pigtail.connectors.base import FetchError, RetryPolicy, TokenBucket
from pigtail.connectors.hn import LAUNCH_LOOKUP_EVIDENCE, HNShowDiscoveryConnector
from pigtail.privacy import requests
from pigtail.privacy.deletion import DeletionLog
from tests.discovery_fake import FakeShowHN
from tests.selection_fake import brief as synthetic_brief
from tests.surface_fake import default_coder

pytestmark = pytest.mark.db

NOW = datetime(2026, 9, 26, 9, tzinfo=UTC)
KF, MK, NL = "org-k/kubeforge", "org-m/meshkit", "org-n/nolaunch"
ONSET = date(2025, 10, 15)  # the burst's onset endpoint day (US Pacific)
SAME_DAY = datetime(2025, 10, 16, 2, 30, tzinfo=UTC)  # 19:30 PDT on 15 Oct


def _i(t: datetime) -> int:
    return int(t.timestamp())


def hit(item: int, title: str, url: str | None, t: datetime, points: int, *tags: str) -> dict:
    return {
        "objectID": str(item),
        "title": title,
        "url": url,
        "points": points,
        "created_at_i": _i(t),
        "author": f"hnuser{item % 1000}",
        "_tags": ["story", f"author_hnuser{item % 1000}", *tags],
        "story_text": f"I (hnuser{item % 1000}) made this.",
    }


HITS = [
    # the verifier's case: the repo URL with another capitalisation, on the onset day (Pacific)
    hit(8101, "Show HN: Clusters in one command", "https://github.com/ORG-K/KubeForge", SAME_DAY,
        40, "show_hn"),
    hit(8102, "Show HN: Kubeforger, another tool", "https://example.org/kf",
        datetime(2025, 10, 2, tzinfo=UTC), 90, "show_hn"),  # substring: no match
    hit(8103, "Launch HN: KubeForge (YC S25) - managed clusters", "https://kubeforge.example",
        datetime(2025, 10, 20, 17, tzinfo=UTC), 25, "launch_hn"),  # title match (ADR-082)
    hit(8104, "Show HN: kubeforge-ui, a dashboard", None,
        datetime(2025, 10, 3, tzinfo=UTC), 70, "show_hn"),  # another project's name
    hit(8105, "Show HN: KubeForge plugin", "https://github.com/org-other/kf-plugin",
        datetime(2025, 10, 4, tzinfo=UTC), 60, "show_hn"),  # not the product slot: rejected
    hit(8106, "Show HN: KubeForge, the first try", "https://github.com/org-k/kubeforge",
        datetime(2024, 1, 5, tzinfo=UTC), 99, "show_hn"),  # before the brief's window
    hit(8201, "Show HN: Meshkit", "https://github.com/org-m/meshkit",
        datetime(2025, 11, 3, 16, tzinfo=UTC), 70, "show_hn"),
]  # fmt: skip


def hn_connector(capture_db: Any, fake: FakeShowHN, tmp: Path) -> HNShowDiscoveryConnector:
    return HNShowDiscoveryConnector(
        store=LocalSnapshotStore(tmp / "snap"),
        pseudonymizer=None,
        http=fake.client(),
        env={},
        evidence_sink=capture_db.upsert_evidence,
        sleep=lambda s: None,
        limiter=TokenBucket(1000, burst=1000),
        retry=RetryPolicy(max_retries=1),
        clock=lambda: NOW,
    )


def seed(capture_db: Any) -> Any:
    capture_db.conn.autocommit = True
    b = synthetic_brief(minimums={}, primary_threshold="at_least_median")
    store = CandidateStore(capture_db.conn, b.brief_id, b.version)
    mk_time = datetime(2025, 11, 3, 16, tzinfo=UTC)
    rows = []
    for i, name in enumerate((KF, MK, NL)):
        hid = 8_900_001 + i
        sources = []
        if name == MK:  # discovery found this launch; the lookup finds it again (same item)
            sources = [{"source": "show_hn", "term": "t", "hn_item_id": 8201, "points": 50,
                        "title": "Show HN", "time": mk_time.isoformat()}]  # fmt: skip
        store.upsert(
            Candidate(
                ref=f"gh:{name}",
                repo_full_name=name,
                repo_host_id=hid,
                sources=sources,
                metadata={"created_at": "2025-01-01T00:00:00+00:00", "language": "Go"},
            ),
            brief_run_id=None,
            now=NOW,
        )
        if name == NL:
            continue  # no star history, no launch: no anchor
        d = date(2025, 3, 1)
        while d <= NOW.date():
            n = 80 if name == KF and d in (ONSET, ONSET + timedelta(days=1)) else 2
            rows.append((hid, d, n, "w", "tz", False, NOW))
            d += timedelta(days=1)
    with capture_db.conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO repo_star_daily (repo_host_id, day, stars_net, week_label,"
            " day_boundary_tz, is_partial, fetched_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            rows,
        )
    sl = Shortlist(capture_db.conn, b)
    sl.ensure(None)
    sl.decide([f"gh:{n}" for n in (KF, MK, NL)], "accept", "synthetic", reviewer="owner")
    sl.finalize(reviewer="owner")
    return b


def prereg(conn: Any, b: Any, tmp: Path) -> None:
    f = tmp / f"prereg-{b.brief_id}.md"
    f.write_text("# Pre-registration (synthetic test)\nNo brief content.\n")
    preregister(conn, b, f, commit="0123abc")


def stage(conn: Any, b: Any, hn: Any, cp: dict[str, Any]) -> Any:
    return run_stage(
        conn,
        b,
        brief_run_id=None,
        github=None,
        checkpoint=cp,
        save_checkpoint=lambda _c: None,
        run_date=NOW.date(),
        clock=lambda: NOW,
        hn=hn,
        coder=default_coder(),  # ADR-084: the stage's first step
    )


def test_lookup_stores_project_fields_drops_raw_and_anchors_on_a_same_day_launch(
    capture_db, tmp_path
):
    b = seed(capture_db)
    prereg(capture_db.conn, b, tmp_path)
    fake = FakeShowHN(HITS)
    hn = hn_connector(capture_db, fake, tmp_path)
    res = stage(capture_db.conn, b, hn, {})
    lk = res.fetch["launch_lookup"]
    # three searches inside the window, three from HN's epoch to its start (anchor-v8)
    assert lk["repos"] == 3 and lk["looked_up"] == 3 and lk["requests"] == 18
    assert lk["posts"] == 4 and lk["by_match"] == {"url": 3, "title": 1}
    assert len(fake.lookup_requests) == 18
    tags = sorted(r.url.params["tags"] for r in fake.lookup_requests)
    assert tags == ["launch_hn"] * 6 + ["show_hn"] * 12  # ADR-082: the launch_hn tag
    before = [r for r in fake.lookup_requests if "created_at_i>=1160418111" in
              r.url.params["numericFilters"]]  # fmt: skip
    assert len(before) == 9  # from HN's epoch (item 1)
    assert lk["title_rejected"] == {"not_product_slot": 1} and lk["rule"] == "anchor-v10"
    # ADR-083 E: KubeForge has a URL-matched launch, so its title match is not considered
    assert lk["title_unconfirmed"] == {"has_url_launch": 1} and lk["haiku_checks"] == 0

    store = CandidateStore(capture_db.conn, b.brief_id, b.version)
    kf = store.get(f"gh:{KF}")
    assert kf is not None
    got = sorted((s["hn_item_id"], s["kind"], s["match"], s["points"]) for s in kf.sources)
    # 8106, its Show HN before the window, is stored too (view B's pre-window rule)
    assert got == [
        (8101, "show_hn", "url", 40),
        (8103, "launch_hn", "title", 25),
        (8106, "show_hn", "url", 99),
    ]
    base = {"source", "hn_item_id", "time", "points", "kind", "match", "rule"}
    for s in kf.sources:  # project-level fields only: no author, no title, no text
        extra = {"confirmed", "confirmation"} if s["match"] == "title" else set()
        assert set(s) == base | extra
    (t,) = [s for s in kf.sources if s["match"] == "title"]
    assert t["confirmed"] is False and t["confirmation"] == "unconfirmed:has_url_launch"
    nl = store.get(f"gh:{NL}")
    assert nl is not None and [s for s in nl.sources if s["source"] == "hn_launch_lookup"] == []
    # no launch event: its first external mention was searched (ADR-084), none links it
    (m,) = nl.sources
    assert m["source"] == "hn_first_mention" and m["status"] == "none"
    dump = " ".join(
        str(r) for r in capture_db.conn.execute("SELECT * FROM brief_candidate").fetchall()
    )
    assert "hnuser" not in dump and "made this" not in dump

    # evidence: one row per search, named by repo only, raw bytes dropped (CB-24)
    ev = capture_db.conn.execute(
        "SELECT url, deletion_state, content_hash FROM evidence WHERE starts_with(url, %s)",
        (LAUNCH_LOOKUP_EVIDENCE,),
    ).fetchall()
    # 18 lookup searches, and the first-mention search of the repo without a launch (ADR-084)
    assert len(ev) == 19 and {r[1] for r in ev} == {"raw_dropped"}
    assert [u for u, _, _ in ev if "search=first_mention" in u] == [
        f"{LAUNCH_LOOKUP_EVIDENCE}{NL}&search=first_mention&tags=story,comment&page=0"
    ]
    for url, _, _ in ev:
        assert "numericFilters" not in url and "created_at" not in url
        assert any(f"{LAUNCH_LOOKUP_EVIDENCE}{n}&" in url for n in (KF, MK, NL))
    snaps = [p for p in (tmp_path / "snap").rglob("*") if p.is_file()]
    assert not any(b"hnuser" in p.read_bytes() for p in snaps)
    assert res.evidence_ids and set(res.evidence_ids) >= {
        str(r[0])
        for r in capture_db.conn.execute(
            "SELECT id FROM evidence WHERE starts_with(url, %s)", (LAUNCH_LOOKUP_EVIDENCE,)
        ).fetchall()
    }

    # the anchor uses the looked-up launch, on the burst's onset day (same-day rule)
    v = view(capture_db.conn, b.brief_id, 1)
    by = {c["repo_full_name"]: c["detail"] for c in v["cases_by_view"]["follow_through"]}
    a = by[KF]["anchor"]
    assert a["type"] == "launch" and a["via"] == "lookup:url" and a["source"] == "show_hn"
    assert datetime.fromisoformat(a["at"]) == SAME_DAY
    assert by[KF]["values"]["att.hn_points"]["value"] == 40
    assert by[KF]["values"]["att.stars@30"]["value"] == 80 + 80 + 28 * 2  # first day = 15 Oct
    m = by[MK]["anchor"]  # discovery's post and the lookup's are one launch (same item id)
    assert m["type"] == "launch" and m["via"] == "lookup:url"
    assert by[MK]["values"]["att.hn_points"]["value"] == 70  # the lookup's fresher points
    assert by[NL]["anchor"] is None
    w = v["selection"]["summary"]["warnings"]
    assert "follow_through: no anchor: 1 of 3 shortlisted" in w
    # view B ranks on launch size (2 observed); view A's fit needs 20 cases (no residuals)
    # (view B: 1 observed: KubeForge launched before the window, anchor-v8)
    assert "launch: attention population 1 < 20 (minimum): no percentiles" in w
    assert any(x.startswith("follow_through: follow-through fit not made") for x in w)
    assert v["selection"]["summary"]["anchors"] == {"launch:lookup:url": 2, "none": 1}
    assert (
        "launch lookup: 1 title-only candidates rejected by the title rule "
        "(not_product_slot 1; ADR-082)"
    ) in w
    assert (
        "launch lookup: 1 title-only matches not confirmed and excluded (has_url_launch 1; "
        "ADR-083 E)"
    ) in w

    # a second stage run reuses the checkpoint: no new HN request
    fake.requests.clear()
    done = [f"gh:{n}" for n in (KF, MK, NL)]
    rule = selmod.ANCHOR_RULE_VERSION
    cp: dict[str, Any] = {"fetch": {"launch_lookup_done": done, "launch_lookup_rule": rule}}
    again = stage(capture_db.conn, b, hn, cp)
    assert again.fetch["launch_lookup"]["already_done"] == 3 and fake.lookup_requests == []
    # progress recorded under another anchor rule is not reused (ADR-083)
    old = stage(capture_db.conn, b, hn, {"fetch": {"launch_lookup_done": done}})
    assert old.fetch["launch_lookup"]["already_done"] == 0 and len(fake.lookup_requests) == 18
    fake.requests.clear()

    # an opt-out of one repo removes its lookup evidence, not the others' (CB-13c)
    capture_db.conn.execute(
        "INSERT INTO repos (id, host, host_id, full_name, first_seen_at)"
        " VALUES ('github:8900001', 'github', 8900001, %s, %s)",
        (KF, NOW),
    )
    requests.purge_repo(
        capture_db, LocalSnapshotStore(tmp_path / "snap"), "github:8900001",
        DeletionLog(capture_db, "objection"),
    )  # fmt: skip
    left = [
        r[0]
        for r in capture_db.conn.execute(
            "SELECT url FROM evidence WHERE starts_with(url, %s)", (LAUNCH_LOOKUP_EVIDENCE,)
        ).fetchall()
    ]
    # the other two repos' 12 lookup searches and nolaunch's first-mention search (ADR-084)
    assert len(left) == 13 and not any(KF in u for u in left)


def test_lookup_resumes_per_repo_after_a_failed_request(capture_db, tmp_path):
    b = seed(capture_db)
    prereg(capture_db.conn, b, tmp_path)
    fake = FakeShowHN(HITS)
    fake.fail_after = 7  # the first repo's six searches pass, the second repo's first fails
    hn = hn_connector(capture_db, fake, tmp_path)
    cp: dict[str, Any] = {}
    with pytest.raises(FetchError):
        stage(capture_db.conn, b, hn, cp)
    assert cp["fetch"]["launch_lookup_done"] == [f"gh:{KF}"]
    assert capture_db.conn.execute("SELECT count(*) FROM brief_selection").fetchone()[0] == 0
    fake.fail_after = None
    fake.requests.clear()
    res = stage(capture_db.conn, b, hn, cp)
    lk = res.fetch["launch_lookup"]
    assert lk["already_done"] == 1 and lk["looked_up"] == 2 and lk["requests"] == 12
    assert len(fake.lookup_requests) == 12
    assert all(KF not in r.url.params["query"] for r in fake.lookup_requests)
    assert cp["fetch"]["launch_lookup_done"] == sorted(f"gh:{n}" for n in (KF, MK, NL))
    v = view(capture_db.conn, b.brief_id, 1)
    kf = {c["repo_full_name"]: c["detail"] for c in v["cases_by_view"]["launch"]}[KF]
    # view B: KubeForge's first Show HN (8106) precedes the window, so no anchor (anchor-v8)
    assert kf["anchor"] is None and kf["anchor_reason"] == "launched_before_window"
    assert kf["pre_window_launch"]["kind"] == "show_hn" and kf["pre_window_launch"]["ref"] == "8106"
    fa = {c["repo_full_name"]: c["detail"] for c in v["cases_by_view"]["follow_through"]}[KF]
    assert fa["anchor"]["via"] == "lookup:url"  # view A keeps its in-window launch


def test_a_pre_registration_under_the_old_selection_params_is_refused(
    capture_db, tmp_path, monkeypatch
):
    b = seed(capture_db)
    new_params = selmod.Context.params

    def old_params(self: Any) -> dict[str, Any]:  # selection-v2: no anchor rule in the hash
        p = new_params(self)
        for k in ("anchor_rule_version", "anchor_rule", "launch_lookup"):
            p.pop(k)
        p["selection_version"] = "selection-v2"
        return p

    monkeypatch.setattr(selmod.Context, "params", old_params)
    prereg(capture_db.conn, b, tmp_path)
    monkeypatch.setattr(selmod.Context, "params", new_params)
    with pytest.raises(PreregistrationMissing, match=r"selection rule changed.*selection-v11"):
        require(capture_db.conn, b)
    fake = FakeShowHN(HITS)
    with pytest.raises(PreregistrationMissing):
        stage(capture_db.conn, b, hn_connector(capture_db, fake, tmp_path), {})
    assert fake.requests == []  # nothing fetched
    assert capture_db.conn.execute("SELECT count(*) FROM brief_selection").fetchone()[0] == 0
    # a changed anchor rule version alone also changes the hash
    monkeypatch.setattr(selmod, "ANCHOR_RULE_VERSION", "anchor-v2")
    prereg(capture_db.conn, b, tmp_path)
    monkeypatch.setattr(selmod, "ANCHOR_RULE_VERSION", "anchor-v3")
    with pytest.raises(PreregistrationMissing):
        require(capture_db.conn, b)
    prereg(capture_db.conn, b, tmp_path)  # pre-registered again under the current rule
    assert require(capture_db.conn, b).selection_params_sha256
