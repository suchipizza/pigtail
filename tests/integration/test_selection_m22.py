"""M22 selection stage on Postgres (synthetic data, fakes only; no network): `pigtail run --brief`
runs the selection after the shortlist is final, on the same brief run (R19.1); star history is
fetched through the GitHub fake with a resumable checkpoint; results are stored with provenance
(R18.6) and are deterministic for a brief version and data version (R4.8); the outcome sort,
matching, balance and sensitivity work on stored star history (R4.3, R4.9); a repo opt-out
removes its selection rows (CB-13c).
"""

from __future__ import annotations

import math
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from pigtail.briefs.candidates import Candidate, CandidateStore
from pigtail.briefs.discovery import window_bounds
from pigtail.briefs.outcomes import load_inputs, shortlisted
from pigtail.briefs.preregistration import record as preregister
from pigtail.briefs.selection import Context, Definition, select
from pigtail.briefs.selection_store import run_stage, view
from pigtail.briefs.shortlist import Shortlist
from pigtail.capture.snapshots import LocalSnapshotStore
from pigtail.connectors.github_budget import JobCaps
from pigtail.privacy import requests
from pigtail.privacy.deletion import DeletionLog
from tests.github_fake import series
from tests.integration.test_brief_run_m22 import World, example, make_world, run, runs
from tests.integration.test_github_per_repo_m1t24 import connector
from tests.relevance_fake import RelevanceBatchBackend
from tests.selection_fake import brief as synthetic_brief

pytestmark = pytest.mark.db

NOW = datetime(2026, 9, 26, 9, tzinfo=UTC)


@pytest.fixture
def w(capture_db: Any, tmp_path: Any) -> World:
    return make_world(capture_db, tmp_path)


def prereg(w: World, b: Any, tmp_path: Any) -> None:
    """Record a synthetic pre-registration (R8.2) so the selection may run."""
    f = tmp_path / f"prereg-{b.brief_id}-v{b.version}.md"
    f.write_text("# Pre-registration (synthetic test)\nHypotheses: MC-01. No brief content.\n")
    preregister(w.conn, b, f, commit="0123abc")


def finalize(w: World) -> Shortlist:
    sl = Shortlist(w.conn, example())
    sl.decide_where("accept", "fits the core field", verdict="relevant", reviewer="owner")
    sl.decide_where("reject", "not a linter", verdict="uncertain", reviewer="owner")
    sl.add(
        "https://github.com/org-z/showcase-engine",
        "the engine was promoted (ADR-054.3)",
        resolves="named:exemplar:0",
        reviewer="owner",
    )
    sl.finalize(reviewer="owner")
    return sl


# --- the stage in `pigtail run` ----------------------------------------------------------------
def test_selection_runs_after_finalize_on_the_same_run_and_stores_provenance(w, tmp_path):
    fb = RelevanceBatchBackend()
    first = run(w, fb)
    assert first.status == "awaiting_review" and "selection" not in runs(w)[0]["stages"]
    # before the shortlist is final, asking for the selection explains why nothing happens
    early = run(w, fb, stages=("selection",))
    assert early.exit_code == 2 and "once the shortlist is final" in early.message
    assert w.conn.execute("SELECT count(*) FROM brief_selection").fetchone()[0] == 0

    finalize(w)
    for name in shortlisted(w.conn, example()):  # star history for every shortlisted repo
        r = w.gh.by_name(name.repo_full_name or "")
        if r is not None:
            w.gh.daily[r["id"]] = series("burst", days=700)
    prereg(w, example(), tmp_path)
    out = run(w, fb)
    assert out.exit_code == 0 and out.status == "succeeded", out.message
    assert out.brief_run_id == first.brief_run_id and "selection sel_" in out.message
    (r,) = runs(w)
    st = r["stages"]["selection"]
    assert st["status"] == "done" and st["result"]["fetch"]["fetched"] >= 6
    assert len(fb.submitted) == 1  # nothing re-run, no model call in the selection

    v = view(w.conn, "example-config-linter", 1)
    sel = v["selection"]
    assert sel["id"] == st["result"]["selection_id"] and sel["brief_run_id"] == r["id"]
    assert sel["brief_hash"] == example().content_hash()
    assert sel["data_version"].startswith("dv1-") and sel["as_of"] == date(2026, 9, 25)
    assert sel["data_version"].endswith("@2026-09-25")  # as_of folded in (R4.8)
    assert sel["selection_version"] == "selection-v2" and sel["outcome_model_version"] == "2.1"
    assert sel["params_version"] == "1.1.0"
    assert sel["code_commit"] is None or re.fullmatch(r"[0-9a-f]{7,40}", sel["code_commit"])
    assert re.fullmatch(r"[0-9a-f]{64}", sel["result_hash"])
    names = {c["repo_full_name"] for c in v["cases"]}
    assert names == {c.repo_full_name for c in shortlisted(w.conn, example())}
    by = {c["repo_full_name"]: c for c in v["cases"]}
    assert by["org-z/schema-checker"]["is_reference"] is True
    assert by["org-z/showcase-engine"]["role"] == "exemplar"
    # the example brief ranks on adoption, which has no connector yet: nothing is invented,
    # every anchored field candidate is undetermined and the report says so
    for c in v["cases"]:
        d = c["detail"]
        if d["anchor"] is not None:
            assert d["values"]["adopt.downloads@90"]["reason"] == "no_connector"
        assert d["star_anomaly"]["label"] == "unfiltered, anomaly-checked"
    assert sel["summary"]["counts"]["winners"] == 0
    assert any("fewer winners than the minimum" in x for x in sel["summary"]["warnings"])
    assert sel["summary"]["undetermined_by_dimension"]["adoption"] >= 1
    fetched = w.conn.execute("SELECT count(DISTINCT repo_host_id) FROM star_history_fetch")
    assert fetched.fetchone()[0] >= 6
    # star-history evidence is linked to the run for retention (R19.9)
    assert (
        w.conn.execute(
            "SELECT count(*) FROM brief_evidence WHERE brief_run_id = %s", (r["id"],)
        ).fetchone()[0]
        > 0
    )
    # a second run is a no-op
    again = run(w, fb)
    assert "nothing to do" in again.message
    assert w.conn.execute("SELECT count(*) FROM brief_selection").fetchone()[0] == 1


def test_selection_fetch_pauses_on_the_github_budget_and_resumes(w, tmp_path):
    fb = RelevanceBatchBackend()
    assert run(w, fb).status == "awaiting_review"
    finalize(w)
    prereg(w, example(), tmp_path)
    small = connector(w.db, w.gh, tmp_path, job=JobCaps({"core": 2, "graphql": 5}))
    out = run(w, fb, github=small, stages=("selection",))
    assert out.exit_code == 4 and out.status == "paused_budget", out.message
    (r,) = runs(w)
    done = r["checkpoint"]["selection"]["fetch"]["star_history_done"]
    assert r["stages"]["selection"]["status"] == "paused_budget" and 0 < len(done) < 7
    out2 = run(w, fb)
    assert out2.exit_code == 0 and out2.status == "succeeded" and out2.brief_run_id == r["id"]
    (r2,) = runs(w)
    assert len(r2["checkpoint"]["selection"]["fetch"]["star_history_done"]) == 7
    assert r2["stages"]["selection"]["result"]["fetch"]["already_done"] == len(done)


# --- a real outcome sort on stored star history ------------------------------------------------
N = 32
START = date(2025, 3, 1)


def seed_population(w: World, b: Any, *, as_of: date) -> list[str]:
    """N synthetic repos with Show HN launches in 2025H2, stored star history, final shortlist."""
    store = CandidateStore(w.conn, b.brief_id, b.version)
    names = []
    rows = []
    for i in range(N):
        name = f"org-q/repo-{i:02d}"
        hid = 8_800_000 + i
        t = datetime(2025, 10, 1, 15, tzinfo=UTC) + timedelta(days=i)
        store.upsert(
            Candidate(
                ref=f"gh:{name}",
                repo_full_name=name,
                repo_host_id=hid,
                sources=[{"source": "show_hn", "term": "t", "hn_item_id": 9000 + i,
                          "points": 10 + i, "title": "Show HN", "time": t.isoformat()}],
                metadata={"created_at": "2025-06-01T00:00:00+00:00",
                          "language": ("Go", "Rust")[i % 2], "stars": 100},
            ),
            brief_run_id=None,
            now=NOW,
        )  # fmt: skip
        first = t.date()  # 15:00 UTC is 08:00 in US Pacific: the same endpoint day
        launch = (i * 5) % 11 + 3  # the first two days (LSM), independent of what follows
        after = (i * 7) % 23 + 1  # the rest of the 30-day window (the outcome)
        d = START
        while d <= as_of:
            k = (d - first).days
            n = 2 if k < 0 or k >= 30 else (launch if k < 2 else after)
            rows.append((hid, d, n, "w", "tz", False, NOW))
            d += timedelta(days=1)
        names.append(name)
    with w.conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO repo_star_daily (repo_host_id, day, stars_net, week_label,"
            " day_boundary_tz, is_partial, fetched_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            rows,
        )
    sl = Shortlist(w.conn, b)
    sl.ensure(None)
    sl.decide([f"gh:{n}" for n in names], "accept", "synthetic", reviewer="owner")
    sl.finalize(reviewer="owner")
    return names


def stage(w: World, b: Any, cp: dict[str, Any] | None = None) -> Any:
    return run_stage(
        w.conn,
        b,
        brief_run_id=None,
        github=None,
        checkpoint=cp if cp is not None else {},
        save_checkpoint=lambda _c: None,
        run_date=NOW.date(),
        clock=lambda: NOW,
    )


def test_outcome_sort_matching_balance_sensitivity_on_stored_star_history(w, tmp_path):
    b = synthetic_brief(minimums={}, primary_threshold="at_least_median")
    names = seed_population(w, b, as_of=NOW.date())
    prereg(w, b, tmp_path)
    res = stage(w, b)
    assert res.fetch["failed"] == {"no_github_connector": N}  # no network: stored data only
    v = view(w.conn, b.brief_id, 1)
    sel, cases = v["selection"], v["cases"]
    assert len(cases) == N
    by = {c["repo_full_name"]: c for c in cases}
    c0 = by["org-q/repo-00"]["detail"]
    assert c0["anchor"]["type"] == "launch" and c0["anchor"]["source"] == "show_hn"
    stars = c0["values"]["att.stars@30"]
    assert stars["status"] == "observed" and stars["value"] == 2 * 3 + 28 * 1
    assert stars["tag"] == "verified" and stars["reason"] == "unfiltered, anomaly-checked"
    assert c0["covariates"]["lsm"] == pytest.approx(math.log10(1 + 6), abs=1e-6)
    assert c0["covariates"]["launch_half_year"] == "2025H2"
    assert c0["values"]["att.hn_points"]["value"] == 10
    # outcome sort (median floor, 32 observed values): 16 or so qualify, the top 20 are winners
    counts = sel["summary"]["counts"]
    assert counts["winners"] >= 15 and counts["matched_losers"] >= 1
    winners = [c for c in cases if c["role"] == "winner"]
    assert sorted(c["rank"] for c in winners) == list(range(1, len(winners) + 1))
    losers = [c for c in cases if c["role"] == "matched_loser"]
    for lo in losers:
        (wn,) = [c for c in winners if c["pair_id"] == lo["pair_id"]]
        assert (
            wn["detail"]["covariates"]["launch_half_year"]
            == lo["detail"]["covariates"]["launch_half_year"]
        )
        assert lo["headline"] is (not lo["detail"]["pair"]["excluded_on"])
    bal = sel["balance"]
    assert bal["exact_match"]["ok"] is True and "lsm" in bal["after_matching"]
    assert bal["pairs"] == len(losers)
    assert {a["key"] for a in sel["sensitivity"]["alternatives"]} >= {
        "band_shift:attention:looser",
        "band_shift:attention:tighter",
        "exclude_anomaly_flagged",
    }
    # determinism: recomputing from the same stored data gives the same result hash
    b2 = b
    window = window_bounds(b2, NOW.date())
    again = select(
        load_inputs(w.conn, b2, shortlisted(w.conn, b2), window=window, as_of=NOW.date()),
        Context.from_brief(b2),
        Definition.from_brief(b2),
    )
    assert again.result_hash == sel["result_hash"] and again.inputs_hash == sel["inputs_hash"]
    res2 = stage(w, b)
    assert res2.result_hash == res.result_hash and res2.selection_id != res.selection_id
    # a later as-of date with the same data is still the same selection (horizons all passed)
    later = select(
        load_inputs(w.conn, b2, shortlisted(w.conn, b2), window=window,
                    as_of=NOW.date() + timedelta(days=3)),
        Context.from_brief(b2),
        Definition.from_brief(b2),
    )  # fmt: skip
    assert later.result_hash == sel["result_hash"]
    # a horizon not reached yet is pending, never imputed
    early = load_inputs(
        w.conn, b2, shortlisted(w.conn, b2), window=window, as_of=date(2025, 10, 20)
    )
    assert {c.values["att.stars@30"].status for c in early if c.anchor} <= {"pending"}

    # CB-13c: a repo opt-out removes its selection rows; the other rows name no other repo
    gone = names[0]
    w.conn.execute(
        "INSERT INTO repos (id, host, host_id, full_name, first_seen_at)"
        " VALUES ('github:8800000', 'github', 8800000, %s, %s)",
        (gone, NOW),
    )
    requests.purge_repo(
        w.db, LocalSnapshotStore(tmp_path / "snap"), "github:8800000",
        DeletionLog(w.db, "objection"),
    )  # fmt: skip
    left = w.conn.execute(
        "SELECT count(*) FROM brief_selection_case WHERE repo_full_name = %s", (gone,)
    ).fetchone()[0]
    assert left == 0
    dump = " ".join(
        str(r)
        for r in w.conn.execute(
            "SELECT params, summary, balance, sensitivity FROM brief_selection"
        ).fetchall()
    )
    assert "org-q/" not in dump
    details = " ".join(
        str(r[0]) for r in w.conn.execute("SELECT detail FROM brief_selection_case").fetchall()
    )
    assert gone not in details


def test_selection_refuses_a_shortlist_in_review(w):
    fb = RelevanceBatchBackend()
    # before any run, `--stage selection` alone starts nothing
    out = run(w, fb, stages=("selection",))
    assert out.exit_code == 2 and out.brief_run_id is None and "nothing was started" in out.message
    assert runs(w) == []
    assert run(w, fb).status == "awaiting_review"
    from pigtail.briefs.selection import SelectionError

    with pytest.raises(SelectionError, match="not final"):
        stage(w, example())
