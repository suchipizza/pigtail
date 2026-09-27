"""ADR-085 addendum 4 on Postgres (synthetic data, fakes only, no network): the shared Product
Hunt topic-listing cache, scanned in calendar-month intervals, holding ids, dates and hashed
normalized names only. A second run or brief over the same window reads no topic page and
finds the same hits; a shifted window scans only its gaps; a cache older than 14 days is scanned
again; a crash mid-scan resumes from the stored cursor; truncated and failed scans are never
reused as complete; a page that can't be parsed leaves Product Hunt incomplete (no view-B anchor,
refused above 10 %); no person field is requested or stored; the tables are in the export and the
inventory and are purged after 90 days; the selection records the listing snapshot it used and
recomputes deterministically. Every name is made up (`org-c…`, `SYNTH-…`).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from pigtail.briefs import launch_sources as ls
from pigtail.briefs.candidates import CandidateStore
from pigtail.briefs.discovery import window_bounds
from pigtail.briefs.estimate import estimate, selection_state
from pigtail.briefs.launch_sources import PH_SOURCE, LaunchSourceIncomplete, run_product_hunt
from pigtail.briefs.outcomes import incomplete_source, load_inputs, shortlisted, view_b_anchor
from pigtail.briefs.ph_cache import coverage, ph_key_hash, usable_scans
from pigtail.briefs.selection import Context, Definition, select_views
from pigtail.briefs.selection_store import latest, view
from pigtail.capture.inventory import TABLES, inventory
from pigtail.connectors.base import FetchError
from pigtail.connectors.producthunt import TOPIC_FIELDS, TOPIC_QUERY
from pigtail.export.jsonl import TABLE_LEVELS
from pigtail.privacy.retention import RetentionConfig, purge
from tests.discovery_fake import FakeShowHN
from tests.integration.test_launch_lookup_m22 import NOW, hn_connector, prereg
from tests.integration.test_m22_round7_pg import ph_conn, seed, stage
from tests.launch_sources_fake import FakeProductHunt, ph_post

pytestmark = pytest.mark.db

C1, C2, C3 = "org-c1/cachelint", "org-c2/meshkit", "org-c3/latecomer"
REPOS = {
    C1: {"description": "Lints cache configs"},
    C2: {"description": "A mesh kit"},
    C3: {"description": "Arrives late"},
}
TOPICS = ("open-source", "developer-tools")
T = datetime(2026, 1, 10, 12, tzinfo=UTC)  # the clock of the first run
W = (datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 12, 31, 23, 59, 59, tzinfo=UTC))
PERSON_MARKERS = ("SYNTH-MAKER", "synth-maker-handle", "SYNTH-TAGLINE", "SYNTH-DESC")


def t(y: int, m: int, d: int, h: int = 12) -> datetime:
    return datetime(y, m, d, h, tzinfo=UTC)


def posts() -> list[dict[str, Any]]:
    out = [
        # C1: a topic-only hit (its slug differs), confirmed by its GitHub link
        ph_post("7001", "CacheLint", slug="cachelint-2", description="SYNTH-DESC github.com/"
                "org-c1/cachelint", created=t(2025, 6, 1), featured=t(2025, 6, 2),
                topics=("open-source",)),
        # C2: listed in developer-tools, also a topic-only hit
        ph_post("7002", "MeshKit", slug="meshkit-app", description="SYNTH-DESC github.com/"
                "org-c2/meshkit", created=t(2025, 9, 1), topics=("developer-tools",)),
        # C3: created in the days a shifted window adds (2026-01-03)
        ph_post("7003", "Latecomer", slug="latecomer-io", description="SYNTH-DESC github.com/"
                "org-c3/latecomer", created=t(2026, 1, 3), topics=("open-source",)),
    ]  # fmt: skip
    # filler: 44 unrelated open-source posts in February 2025 (three pages of 20), 4 in
    # developer-tools in March
    out += [
        ph_post(f"8{i:03d}", f"Filler Product {i}", created=t(2025, 2, 1) + timedelta(hours=i),
                topics=("open-source",))
        for i in range(44)
    ]  # fmt: skip
    out += [
        ph_post(f"9{i:03d}", f"Other Tool {i}", created=t(2025, 3, 1) + timedelta(hours=i),
                topics=("developer-tools",))
        for i in range(4)
    ]  # fmt: skip
    return out


def setup(capture_db: Any, tmp: Path, version: int = 1) -> tuple[Any, list[Any]]:
    b = seed(capture_db, REPOS, version=version)
    return b, shortlisted(capture_db.conn, b)


def run(
    capture_db: Any,
    b: Any,
    cands: list[Any],
    fake: FakeProductHunt,
    tmp: Path,
    *,
    window: tuple[datetime, datetime] = W,
    now: datetime = T,
    checkpoint: dict[str, Any] | None = None,
) -> Any:
    return run_product_hunt(
        capture_db.conn, b, ph_conn(capture_db, fake, tmp), cands, window=window,
        checkpoint={} if checkpoint is None else checkpoint, save=lambda _c: None,
        topics=TOPICS, clock=lambda: now,
    )  # fmt: skip


def ph_signals(capture_db: Any, b: Any) -> dict[str, Any]:
    cs = CandidateStore(capture_db.conn, b.brief_id, b.version).all()
    out = {}
    for c in cs:
        (s,) = [x for x in c.sources if x["source"] == PH_SOURCE]
        out[c.repo_full_name] = (
            s["status"],
            sorted((p["ph_post_id"], p["route"], p["confirmed"]) for p in s["posts"]),
        )
    return out


def test_a_second_brief_over_the_same_window_reads_no_topic_page(capture_db, tmp_path):
    fake = FakeProductHunt(posts())
    b1, c1 = setup(capture_db, tmp_path)
    r1 = run(capture_db, b1, c1, fake, tmp_path)
    first = len(fake.topic_requests)
    # one scan per calendar month and topic: open-source 11 months of 1 page and February of 3;
    # developer-tools 12 months of 1 page
    assert first == 14 + 12
    months = capture_db.conn.execute(
        "SELECT posted_after, posted_before FROM ph_topic_scan WHERE topic = 'developer-tools'"
        " ORDER BY posted_after"
    ).fetchall()
    assert len(months) == 12 and months[0] == (W[0], t(2025, 2, 1, 0))
    assert months[-1] == (t(2025, 12, 1, 0), W[1])
    assert r1.topic_status == {"open-source": "complete", "developer-tools": "complete"}
    assert all(not x["reused"] for x in r1.topic_cache)
    assert r1.topic_cache_days["open-source"]["reused_days"] == 0
    hits1 = ph_signals(capture_db, b1)
    assert hits1[C1] == ("complete", [("7001", "topic", True)])
    assert hits1[C2] == ("complete", [("7002", "topic", True)])
    assert hits1[C3] == ("complete", [])  # created after this window
    # a second brief (another version, a fresh run), one day later: nothing listed again
    b2, c2 = setup(capture_db, tmp_path, version=2)
    r2 = run(capture_db, b2, c2, fake, tmp_path, now=T + timedelta(days=1))
    assert len(fake.topic_requests) == first
    assert r2.topic_requests == 0 and r2.topic_pages == {"open-source": 0, "developer-tools": 0}
    assert ph_signals(capture_db, b2) == hits1
    assert all(x["reused"] and x["status"] == "complete" for x in r2.topic_cache)
    assert {x["topic"] for x in r2.topic_cache} == set(TOPICS)
    d = r2.topic_cache_days["open-source"]
    assert d["reused_days"] == d["window_days"] == pytest.approx(365, abs=0.01)
    # topic hits are still read fresh by id (tagline, description and counts aren't cached)
    ids = [r["variables"]["id"] for r in fake.requests if "id" in (r.get("variables") or {})]
    assert ids == ["7001", "7002", "7001", "7002"]


def test_a_shifted_window_scans_only_the_gaps(capture_db, tmp_path):
    fake = FakeProductHunt(posts())
    b, cands = setup(capture_db, tmp_path)
    run(capture_db, b, cands, fake, tmp_path)
    n = len(fake.topic_requests)
    shifted = (W[0] + timedelta(days=5), W[1] + timedelta(days=5))  # ends 2026-01-05, before T
    b2, c2 = setup(capture_db, tmp_path, version=2)
    r = run(capture_db, b2, c2, fake, tmp_path, window=shifted, now=T + timedelta(hours=1))
    new = fake.topic_requests[n:]
    # the five new days only, cut at the month boundary: the last second of December 2025 and
    # January 1-5, 2026, one page each, per topic
    jan1 = t(2026, 1, 1, 0).isoformat()
    assert len(new) == 4
    assert sorted((v["postedAfter"], v["postedBefore"]) for v in new) == sorted(
        [(W[1].isoformat(), jan1), (jan1, shifted[1].isoformat())] * 2
    )
    assert ph_signals(capture_db, b2)[C3] == ("complete", [("7003", "topic", True)])
    # C1 and C2 come from the reused listing; the window's start moved but stays covered
    assert ph_signals(capture_db, b2)[C1][1] == [("7001", "topic", True)]
    assert r.topic_cache_days["open-source"]["reused_days"] == pytest.approx(360, abs=0.01)
    assert sorted(x["reused"] for x in r.topic_cache) == [False] * 4 + [True] * 24
    # a scan covers its interval only up to the time it started: a window reaching past the
    # first run's clock is scanned again from there
    b3, c3 = setup(capture_db, tmp_path, version=3)
    later = (W[0], T + timedelta(days=2))
    run(capture_db, b3, c3, fake, tmp_path, window=later, now=T + timedelta(days=3))
    tail = fake.topic_requests[n + 4 :]
    assert len(tail) == 2
    assert {v["postedBefore"] for v in tail} == {later[1].isoformat()}
    assert {v["postedAfter"] for v in tail} == {shifted[1].isoformat()}


def test_a_cache_older_than_the_maximum_age_is_scanned_again(capture_db, tmp_path):
    fake = FakeProductHunt(posts())
    b, cands = setup(capture_db, tmp_path)
    run(capture_db, b, cands, fake, tmp_path)
    n = len(fake.topic_requests)
    run(capture_db, b, cands, fake, tmp_path, now=T + timedelta(days=13))
    assert len(fake.topic_requests) == n  # 13 days: reused
    r = run(capture_db, b, cands, fake, tmp_path, now=T + timedelta(days=15))
    assert len(fake.topic_requests) == 2 * n  # 15 days: the whole window again
    assert all(not x["reused"] for x in r.topic_cache)
    assert ph_signals(capture_db, b)[C1] == ("complete", [("7001", "topic", True)])


def test_a_crash_mid_scan_resumes_from_the_stored_cursor(capture_db, tmp_path):
    fake = FakeProductHunt(posts())
    # January (1 page), then February's second page fails, and its one retry
    fake.fail_topic_at = {2, 3}
    b, cands = setup(capture_db, tmp_path)
    cp: dict[str, Any] = {}
    with pytest.raises(FetchError):
        run(capture_db, b, cands, fake, tmp_path, checkpoint=cp)
    rows = capture_db.conn.execute(
        "SELECT status, cursor, pages FROM ph_topic_scan WHERE topic = 'open-source'"
        " ORDER BY posted_after"
    ).fetchall()
    assert rows[0][0] == "complete" and rows[1] == ("running", "20", 1)
    assert {r[2] for r in rows[2:]} == {0}
    assert capture_db.conn.execute("SELECT count(*) FROM ph_topic_post").fetchone()[0] == 20
    fake.fail_topic_at = set()
    n = len(fake.topic_requests)
    r = run(capture_db, b, cands, fake, tmp_path, checkpoint=cp)
    resumed = fake.topic_requests[n:]
    assert resumed[0]["topic"] == "open-source" and resumed[0]["after"] == "20"
    os_after = [v["after"] for v in resumed if v["topic"] == "open-source"]
    assert os_after == ["20", "40"] + [None] * 10  # February resumed, then March-December
    assert r.topic_status == {"open-source": "complete", "developer-tools": "complete"}
    assert r.topic_pages["open-source"] == 14
    assert ph_signals(capture_db, b)[C1] == ("complete", [("7001", "topic", True)])
    # a new run of another brief adopts nothing: every month is complete and reused
    b2, c2 = setup(capture_db, tmp_path, version=2)
    run(capture_db, b2, c2, fake, tmp_path, now=T + timedelta(hours=2))
    assert len(fake.topic_requests) == n + 12 + 12


def test_truncated_and_failed_scans_are_never_reused_as_complete(capture_db, tmp_path, monkeypatch):
    fake = FakeProductHunt(posts())
    b, cands = setup(capture_db, tmp_path)
    monkeypatch.setattr(ls, "PH_TOPIC_MAX_PAGES", 1)
    # February (3 pages) hits the cap: the topic is incomplete, as a failed page makes it
    with pytest.raises(LaunchSourceIncomplete, match="Product Hunt"):
        run(capture_db, b, cands, fake, tmp_path)
    assert {s for s, _r in ph_signals(capture_db, b).values()} == {"incomplete"}
    (reason,) = {
        s["reason"]
        for c in CandidateStore(capture_db.conn, b.brief_id, 1).all()
        for s in c.sources
        if s["source"] == PH_SOURCE
    }
    assert reason == "topic_scan_truncated"
    trunc = capture_db.conn.execute(
        "SELECT posted_after FROM ph_topic_scan WHERE status = 'truncated'"
    ).fetchall()
    assert trunc == [(t(2025, 2, 1, 0),)]  # one month, not the whole window
    use = usable_scans(capture_db.conn, "open-source", now=T)
    assert len(use) == 11 and t(2025, 2, 1, 0) not in {u.posted_after for u in use}
    monkeypatch.undo()
    n = len(fake.topic_requests)
    b2, c2 = setup(capture_db, tmp_path, version=2)
    r2 = run(capture_db, b2, c2, fake, tmp_path, now=T + timedelta(hours=1))
    os_new = [v for v in fake.topic_requests[n:] if v["topic"] == "open-source"]
    # only February is listed again (its three pages); the other months are reused
    assert len(os_new) == 3 and r2.topic_status["open-source"] == "complete"
    assert {v["postedAfter"] for v in os_new} == {t(2025, 2, 1, 0).isoformat()}
    # developer-tools was complete: reused
    assert not [v for v in fake.topic_requests[n:] if v["topic"] == "developer-tools"]
    # a failed scan (a page that can't be parsed)
    fake2 = FakeProductHunt(posts())
    fake2.garble_topic_at = {0}
    b3, c3 = setup(capture_db, tmp_path, version=3)
    later = T + timedelta(days=20)  # everything above has expired
    with pytest.raises(LaunchSourceIncomplete, match="Product Hunt"):
        run(capture_db, b3, c3, fake2, tmp_path, now=later)
    use = usable_scans(capture_db.conn, "open-source", now=later)
    assert len(use) == 11 and W[0] not in {u.posted_after for u in use}  # January failed
    (st,) = capture_db.conn.execute(
        "SELECT status FROM ph_topic_scan WHERE topic = 'open-source' AND started_at = %s"
        " AND posted_after = %s",
        (later, W[0]),
    ).fetchone()
    assert st == "failed"
    # the next run retries it (from its cursor) rather than trusting it
    fake2.garble_topic_at = set()
    m = len(fake2.topic_requests)
    b4, c4 = setup(capture_db, tmp_path, version=4)
    r4 = run(capture_db, b4, c4, fake2, tmp_path, now=later + timedelta(hours=1))
    os_retry = [v for v in fake2.topic_requests[m:] if v["topic"] == "open-source"]
    assert len(os_retry) == 1 and os_retry[0]["postedAfter"] == W[0].isoformat()
    assert r4.topic_status["open-source"] == "complete"


def test_a_parse_failure_makes_product_hunt_incomplete_not_complete(capture_db, tmp_path):
    fake = FakeProductHunt(posts())
    fake.garble_topic_at = {0}
    b, cands = setup(capture_db, tmp_path)
    cp: dict[str, Any] = {}
    with pytest.raises(LaunchSourceIncomplete, match="more than 10%"):
        run(capture_db, b, cands, fake, tmp_path, checkpoint=cp)
    assert not cp.get("ph_done")  # nothing checkpointed as done: retried by the next run
    sig = ph_signals(capture_db, b)
    assert list(sig.values()) == [("incomplete", [])] * 3
    for c in shortlisted(capture_db.conn, b):
        assert incomplete_source(c, ("product_hunt",)) == "product_hunt"
        a, why, _rel = view_b_anchor(c, *W, required=("product_hunt",))
        assert a is None and why == "launch_source_incomplete:product_hunt"
    # no slug lookup was spent on repos that can't be completed
    assert not [r for r in fake.requests if "slug" in (r.get("variables") or {})]
    # the resumed run completes them
    fake.garble_topic_at = set()
    r = run(capture_db, b, cands, fake, tmp_path, checkpoint=cp, now=T + timedelta(hours=1))
    assert r.incomplete == 0 and ph_signals(capture_db, b)[C1][0] == "complete"


def test_no_person_field_is_requested_or_stored(capture_db, tmp_path):
    fake = FakeProductHunt(posts())
    b, cands = setup(capture_db, tmp_path)
    run(capture_db, b, cands, fake, tmp_path)
    topic_qs = {r["query"] for r in fake.requests if "posts(" in r["query"]}
    assert topic_qs == {TOPIC_QUERY}
    for f in ("makers", "user", "comments", "votes", "tagline", "description", "votesCount"):
        assert f not in TOPIC_QUERY.split("node")[1], f
    assert set(TOPIC_FIELDS) == {"id", "name", "slug", "createdAt", "featuredAt"}
    cols = {
        r[0]
        for r in capture_db.conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'ph_topic_post'"
        )
    }
    assert cols == {"topic", "post_id", "name_key_sha256", "created_at", "featured_at",
                    "first_seen_at", "last_seen_at"}  # fmt: skip
    dump = " ".join(
        json.dumps([str(x) for x in row])
        for table in ("ph_topic_post", "ph_topic_scan")
        for row in capture_db.conn.execute(f"SELECT * FROM {table}").fetchall()
    )
    # nothing readable of the listing: no product name or slug, in any case, only its hash
    low = dump.lower()
    for name in ("CacheLint", "MeshKit", "Filler Product", "Other Tool", "cachelint-2",
                 "meshkit-app", "filler-product"):  # fmt: skip
        assert name.lower() not in low, name
    assert ph_key_hash("cachelint") in dump and ph_key_hash("meshkit") in dump
    assert hashlib.sha256(b"cachelint").hexdigest() == ph_key_hash("cachelint")
    for m in PERSON_MARKERS:
        assert m not in dump, m


def test_export_inventory_and_retention_cover_the_cache(capture_db, tmp_path):
    assert TABLE_LEVELS["ph_topic_post"] == TABLE_LEVELS["ph_topic_scan"] == "project"
    assert {"ph_topic_post", "ph_topic_scan"} <= {s.table for s in TABLES}
    fake = FakeProductHunt(posts())
    b, cands = setup(capture_db, tmp_path)
    run(capture_db, b, cands, fake, tmp_path)
    inv = inventory(capture_db.conn)
    assert inv.unlisted == () and inv.missing == ()
    by = {x.table: x for x in inv.tables}
    assert by["ph_topic_post"].rows == 48 + 2 and by["ph_topic_scan"].rows == 24
    assert by["ph_topic_post"].distinct_repos is None  # products, not repos
    # retention: kept while it can serve a scan, deleted 90 days after it was last seen
    from pigtail.capture.snapshots import LocalSnapshotStore

    store = LocalSnapshotStore(tmp_path / "snap")
    rep = purge(capture_db, store, cfg=RetentionConfig(), now=T + timedelta(days=89))
    assert rep.ph_topic_posts_deleted == 0 and rep.ph_topic_scans_deleted == 0
    rep = purge(capture_db, store, cfg=RetentionConfig(), now=T + timedelta(days=91))
    assert rep.ph_topic_posts_deleted == 50 and rep.ph_topic_scans_deleted == 24
    logged = capture_db.conn.execute(
        "SELECT target, rows_affected FROM deletion_log WHERE target LIKE 'ph_topic%%'"
        " ORDER BY target"
    ).fetchall()
    assert logged == [("ph_topic_post", 50), ("ph_topic_scan", 24)]


def test_the_estimate_counts_only_the_gaps(capture_db, tmp_path):
    fake = FakeProductHunt(posts())
    b, cands = setup(capture_db, tmp_path)
    run(capture_db, b, cands, fake, tmp_path)
    cov = coverage(capture_db.conn, TOPICS, W, now=T + timedelta(days=1))
    assert cov["open-source"]["gaps_days"] == [] and cov["open-source"]["pages_per_day"] > 0
    shifted = (W[0] + timedelta(days=10), W[1] + timedelta(days=10))
    cov = coverage(capture_db.conn, TOPICS, shifted, now=T + timedelta(days=1))
    # the gap as the month intervals it would be scanned in: the last second of 2025, then
    # January 1-10
    assert sum(cov["open-source"]["gaps_days"]) == pytest.approx(10, abs=0.01)
    assert len(cov["open-source"]["gaps_days"]) == 2
    assert cov["open-source"]["reused_days"] == pytest.approx(355, abs=0.01)
    # through the stored state: the run date's window, the cache as of now
    st = selection_state(capture_db.conn, b, now=T + timedelta(days=1))
    assert set(st.ph_cache or {}) == set(TOPICS)
    e = estimate(b, selection=st, launch_sources=(True, False), ph_topics=2)
    note = e.selection["ph_cache_note"]
    assert note.startswith("Product Hunt: cached listing reused for ")
    assert e.selection["producthunt_requests_per_hour"] == 230
    assert e.selection["producthunt_hours"] == pytest.approx(
        e.other_requests["producthunt"] / 230, abs=0.01
    )


# --- through the selection stage: provenance and determinism ------------------------------------
def test_the_selection_records_the_listing_it_used_and_recomputes_deterministically(
    capture_db, tmp_path, monkeypatch
):
    monkeypatch.setenv("PIGTAIL_SELECTION_PRODUCT_HUNT", "true")
    b = seed(capture_db, REPOS)
    prereg(capture_db.conn, b, tmp_path)
    fake = FakeProductHunt(posts())
    hn = hn_connector(capture_db, FakeShowHN([]), tmp_path)
    r1 = stage(capture_db.conn, b, tmp_path, hn=hn, ph=ph_conn(capture_db, fake, tmp_path))
    n = len(fake.topic_requests)
    assert n > 0
    stored = latest(capture_db.conn, b.brief_id, 1)
    assert stored is not None
    listing = stored["summary"]["product_hunt_listing"]
    assert {x["topic"] for x in listing["intervals"]} == set(TOPICS)
    assert all(x["status"] == "complete" and x["finished_at"] and x["pages"] >= 1
               and x["reused"] is False for x in listing["intervals"])  # fmt: skip
    window = window_bounds(b, NOW.date())
    assert max(x["posted_before"] for x in listing["intervals"]) == window[1].isoformat()
    assert len(listing["intervals"]) == 2 * 19  # March 2025 to September 2026, per topic
    assert r1.fetch["product_hunt"]["topic_cache"] == listing["intervals"]
    params = stored["params"]["launch_sources"]["product_hunt"]["topic_cache"]
    assert params["max_age_days"] == 14 and "gap" in params["gap_rule"]
    # recompute from stored data: the same inputs and result hashes
    ctx = Context.from_brief(b)
    again = load_inputs(
        capture_db.conn, b, shortlisted(capture_db.conn, b), window=window,
        as_of=stored["as_of"], launch_sources=ctx.required_launch_sources,
    )  # fmt: skip
    sel = select_views(again, ctx, Definition.from_brief(b))
    assert sel.inputs_hash == stored["inputs_hash"] and sel.result_hash == stored["result_hash"]
    # a new run (fresh checkpoint) reuses the listing and the same result hash; only the
    # recorded provenance differs. The window ends at the end of the run date, after the first
    # scan started, so the rest of that day is read again (one page per topic)
    r2 = stage(capture_db.conn, b, tmp_path, hn=hn, ph=ph_conn(capture_db, fake, tmp_path))
    tail = fake.topic_requests[n:]
    assert len(tail) == 2 and {v["postedAfter"] for v in tail} == {NOW.isoformat()}
    assert r2.result_hash == r1.result_hash
    rows = capture_db.conn.execute(
        "SELECT id, summary FROM brief_selection WHERE brief_id = %s", (b.brief_id,)
    ).fetchall()
    reused = {
        sid: sorted({x["reused"] for x in s["product_hunt_listing"]["intervals"]})
        for sid, s in rows
    }
    assert reused == {r1.selection_id: [False], r2.selection_id: [False, True]}
    assert view(capture_db.conn, b.brief_id, 1)["selection"]["summary"]["product_hunt_listing"]
