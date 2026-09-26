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
    assert lk["repos"] == 3 and lk["looked_up"] == 3 and lk["requests"] == 9
    assert lk["posts"] == 3 and lk["by_match"] == {"url": 2, "title": 1}
    assert len(fake.requests) == 9
    tags = sorted(r.url.params["tags"] for r in fake.requests)
    assert tags == ["launch_hn"] * 3 + ["show_hn"] * 6  # ADR-082: the launch_hn tag
    assert lk["title_rejected"] == {"not_product_slot": 1} and lk["rule"] == "anchor-v3"

    store = CandidateStore(capture_db.conn, b.brief_id, b.version)
    kf = store.get(f"gh:{KF}")
    assert kf is not None
    got = sorted((s["hn_item_id"], s["kind"], s["match"], s["points"]) for s in kf.sources)
    assert got == [(8101, "show_hn", "url", 40), (8103, "launch_hn", "title", 25)]
    for s in kf.sources:  # project-level fields only: no author, no title, no text
        assert set(s) == {"source", "hn_item_id", "time", "points", "kind", "match", "rule"}
    nl = store.get(f"gh:{NL}")
    assert nl is not None and nl.sources == []
    dump = " ".join(
        str(r) for r in capture_db.conn.execute("SELECT * FROM brief_candidate").fetchall()
    )
    assert "hnuser" not in dump and "made this" not in dump

    # evidence: one row per search, named by repo only, raw bytes dropped (CB-24)
    ev = capture_db.conn.execute(
        "SELECT url, deletion_state, content_hash FROM evidence WHERE starts_with(url, %s)",
        (LAUNCH_LOOKUP_EVIDENCE,),
    ).fetchall()
    assert len(ev) == 9 and {r[1] for r in ev} == {"raw_dropped"}
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
    by = {c["repo_full_name"]: c["detail"] for c in v["cases"]}
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
    assert "no anchor: 1 of 3 shortlisted" in w
    assert "attention population 2 < 20 (minimum): no percentiles" in w
    assert v["selection"]["summary"]["anchors"] == {"launch:lookup:url": 2, "none": 1}
    assert (
        "launch lookup: 1 title-only candidates rejected by the title rule "
        "(not_product_slot 1; ADR-082)"
    ) in w

    # a second stage run reuses the checkpoint: no new HN request
    fake.requests.clear()
    cp: dict[str, Any] = {"fetch": {"launch_lookup_done": [f"gh:{n}" for n in (KF, MK, NL)]}}
    again = stage(capture_db.conn, b, hn, cp)
    assert again.fetch["launch_lookup"]["already_done"] == 3 and fake.requests == []

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
    assert len(left) == 6 and not any(KF in u for u in left)


def test_lookup_resumes_per_repo_after_a_failed_request(capture_db, tmp_path):
    b = seed(capture_db)
    prereg(capture_db.conn, b, tmp_path)
    fake = FakeShowHN(HITS)
    fake.fail_after = 4  # the first repo's three searches pass, the second repo's first fails
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
    assert lk["already_done"] == 1 and lk["looked_up"] == 2 and lk["requests"] == 6
    assert len(fake.requests) == 6
    assert all(KF not in r.url.params["query"] for r in fake.requests)
    assert cp["fetch"]["launch_lookup_done"] == sorted(f"gh:{n}" for n in (KF, MK, NL))
    v = view(capture_db.conn, b.brief_id, 1)
    kf = {c["repo_full_name"]: c["detail"] for c in v["cases"]}[KF]
    assert kf["anchor"]["via"] == "lookup:url"


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
    with pytest.raises(PreregistrationMissing, match=r"selection rule changed.*selection-v4"):
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
    prereg(capture_db.conn, b, tmp_path)  # pre-registered again under selection-v4
    assert require(capture_db.conn, b).selection_params_sha256
