"""Owner decision 2026-09-27 (ADR-083) on Postgres (synthetic data, fakes only, no network):
the launch lookup's title-only matches go through the E rule (excluded when the repo has a
URL-matched launch; confirmed by the homepage domain or by the Haiku check; unconfirmed ones
stored with the reason, counted, never an anchor; no title stored; the Haiku verdict cached),
the pre-registration gate refuses a pre-registration recorded before selection-v5 or before a
change to guarded code, and both views' rows are covered by export, inventory and the repo
opt-out. Every name here is made up (`org-…`, `hnuser…`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from pigtail.briefs import confirm as conf
from pigtail.briefs import selection as selmod
from pigtail.briefs.candidates import Candidate, CandidateStore
from pigtail.briefs.preregistration import PreregistrationMissing, require
from pigtail.briefs.selection_store import run_stage, view
from pigtail.briefs.shortlist import Shortlist
from pigtail.capture.inventory import inventory
from pigtail.capture.snapshots import LocalSnapshotStore
from pigtail.export.jsonl import export_jsonl
from pigtail.llm.store import LLMStore
from pigtail.privacy import requests
from pigtail.privacy.deletion import DeletionLog
from tests.discovery_fake import FakeShowHN
from tests.integration.test_launch_lookup_m22 import NOW, hit, hn_connector, prereg
from tests.integration.test_launch_lookup_m22 import seed as seed_three
from tests.selection_fake import brief as synthetic_brief
from tests.surface_fake import default_coder
from tests.unit.test_m22_views import VerdictBackend, confirmer, guard, llm

pytestmark = pytest.mark.db

T = datetime(2025, 10, 20, 17, tzinfo=UTC)
TALLY, WIDGET, QUILL, URLR = "org-t/tallyho", "org-w/widgetry", "org-q/quillpad", "org-u/urlrepo"
HITS = [
    hit(8401, "Show HN: Tallyho – counting made fun", "https://tallyho.dev/launch", T, 31,
        "show_hn"),  # rule 1: the repo's homepage domain
    hit(8402, "Show HN: Widgetry – TRUE-POST", "https://example.org/w", T, 32, "show_hn"),
    hit(8403, "Show HN: Quillpad – FALSE-POST", "https://example.org/q", T, 33, "show_hn"),
    hit(8404, "Show HN: Urlrepo – once more", "https://urlrepo.example", T, 34,
        "show_hn"),  # the repo has a URL-matched launch: not considered
]  # fmt: skip


def seed(capture_db: Any) -> Any:
    capture_db.conn.autocommit = True
    b = synthetic_brief(minimums={}, primary_threshold="at_least_median")
    store = CandidateStore(capture_db.conn, b.brief_id, b.version)
    for i, name in enumerate((TALLY, WIDGET, QUILL, URLR)):
        sources = []
        if name == URLR:  # discovery found a URL-matched launch
            t0 = (T - timedelta(days=9)).isoformat()
            sources = [{"source": "show_hn", "term": "t", "hn_item_id": 8499, "points": 9,
                        "title": "Show HN", "time": t0}]  # fmt: skip
        store.upsert(
            Candidate(
                ref=f"gh:{name}",
                repo_full_name=name,
                repo_host_id=8_950_001 + i,
                sources=sources,
                metadata={
                    "created_at": "2025-01-01T00:00:00+00:00",
                    "language": "Go",
                    "description": "a synthetic widget kit",
                    "homepage_domain": "tallyho.dev" if name == TALLY else "",
                },
            ),
            brief_run_id=None,
            now=NOW,
        )
    sl = Shortlist(capture_db.conn, b)
    sl.ensure(None)
    sl.decide([f"gh:{n}" for n in (TALLY, WIDGET, QUILL, URLR)], "accept", "x", reviewer="owner")
    sl.finalize(reviewer="owner")
    return b


def stage(conn: Any, b: Any, hn: Any, cp: dict[str, Any], c: Any = None) -> Any:
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
        confirmer=c,
        coder=default_coder(),  # ADR-084: the stage's first step
    )


def test_title_only_matches_are_confirmed_or_excluded_by_the_e_rule(capture_db, tmp_path):
    b = seed(capture_db)
    prereg(capture_db.conn, b, tmp_path)
    fake = FakeShowHN(HITS)
    hn = hn_connector(capture_db, fake, tmp_path)
    store = LLMStore(":memory:")
    be = VerdictBackend()
    res = stage(capture_db.conn, b, hn, {}, confirmer(llm(be, store), guard(store)))
    lk = res.fetch["launch_lookup"]
    assert lk["title_confirmed"] == {"haiku": 1, "homepage_domain": 1}
    assert lk["title_unconfirmed"] == {"haiku_false": 1, "has_url_launch": 1}
    assert lk["haiku_checks"] == 2 and be.calls == 2  # rule 1 and rule 0 need no model call

    cs = CandidateStore(capture_db.conn, b.brief_id, b.version)

    def rec(name: str) -> dict[str, Any]:
        c = cs.get(f"gh:{name}")
        assert c is not None
        (r,) = [s for s in c.sources if s.get("source") == "hn_launch_lookup"]
        return r

    assert (rec(TALLY)["confirmed"], rec(TALLY)["confirmation"]) == (True, "homepage_domain")
    w = rec(WIDGET)
    assert (w["confirmed"], w["confirmation"]) == (True, "haiku")
    prov = w["confirmation_provenance"]
    assert prov["prompt_id"] == "title_match_check" and prov["prompt_version"] == "1"
    assert prov["model"] == "claude-haiku-4-5-20251001" and prov["cached"] is False
    assert rec(QUILL)["confirmation"] == "unconfirmed:haiku_false"
    assert rec(URLR)["confirmation"] == "unconfirmed:has_url_launch"
    # no title, no model reason, no author is stored anywhere
    dump = " ".join(str(r) for r in capture_db.conn.execute("SELECT * FROM brief_candidate"))
    dump += " ".join(str(r) for r in capture_db.conn.execute("SELECT * FROM brief_selection"))
    dump += " ".join(str(r) for r in capture_db.conn.execute("SELECT * FROM brief_selection_case"))
    for text in (
        "TRUE-POST",
        "FALSE-POST",
        "counting made fun",
        "once more",
        "hnuser",
        "'synthetic'",
        "Show HN: ",
    ):  # fmt: skip  ('synthetic': the model's reason
        assert text not in dump, text

    # only confirmed title matches anchor; the URL-matched repo keeps its discovery launch
    v = view(capture_db.conn, b.brief_id, 1)
    for rows in v["cases_by_view"].values():
        a = {r["repo_full_name"]: r["detail"]["anchor"] for r in rows}
        assert a[TALLY]["via"] == "lookup:title" and a[WIDGET]["via"] == "lookup:title"
        assert a[QUILL] is None
        assert a[URLR]["via"] == "discovery" and a[URLR]["at"] != T.isoformat()
    w_ = v["selection"]["summary"]["warnings"]
    assert (
        "launch lookup: 2 title-only matches not confirmed and excluded (haiku_false 1, "
        "has_url_launch 1; ADR-083 E)"
    ) in w_
    assert (
        "launch lookup: title-only matches confirmed (haiku 1, homepage_domain 1; ADR-083 E)" in w_
    )
    hn_points = {r["repo_full_name"]: r["detail"]["values"]["att.hn_points"]
                 for r in v["cases_by_view"]["follow_through"]}  # fmt: skip
    assert hn_points[WIDGET]["value"] == 32 and hn_points[QUILL]["status"] == "unknown"

    # a later run without approval gets the same verdict from the LLM cache (no model call)
    again = stage(
        capture_db.conn, b, hn, {}, confirmer(llm(be, store), guard(store, approved=False))
    )
    assert be.calls == 2
    assert again.fetch["launch_lookup"]["title_confirmed"] == {"haiku": 1, "homepage_domain": 1}
    assert rec(WIDGET)["confirmation_provenance"]["cached"] is True
    assert again.result_hash == res.result_hash  # same stored inputs, same selection

    # without a confirmer (no API) the Haiku-dependent match fails closed
    closed = stage(capture_db.conn, b, hn, {})
    assert closed.fetch["launch_lookup"]["title_unconfirmed"] == {
        "haiku_unavailable": 2, "has_url_launch": 1,
    }  # fmt: skip
    assert rec(WIDGET)["confirmed"] is False


def test_the_gate_refuses_an_old_pre_registration_and_one_before_a_guarded_code_change(
    capture_db, tmp_path, monkeypatch
):
    b = seed_three(capture_db)
    new_params = selmod.Context.params

    def v4_params(self: Any) -> dict[str, Any]:  # what selection-v4 hashed: no views, no E
        p = new_params(self)
        for k in ("views", "context_view", "follow_through", "title_confirmation",
                  "anchor_rule_source_sha256"):  # fmt: skip
            p.pop(k)
        p["selection_version"], p["anchor_rule_version"] = "selection-v4", "anchor-v3"
        return p

    monkeypatch.setattr(selmod.Context, "params", v4_params)
    prereg(capture_db.conn, b, tmp_path)
    monkeypatch.setattr(selmod.Context, "params", new_params)
    with pytest.raises(PreregistrationMissing, match=r"selection rule changed.*selection-v8"):
        require(capture_db.conn, b)
    fake = FakeShowHN([])
    with pytest.raises(PreregistrationMissing):
        stage(capture_db.conn, b, hn_connector(capture_db, fake, tmp_path), {})
    assert fake.requests == [] and capture_db.conn.execute(
        "SELECT count(*) FROM brief_selection"
    ).fetchone() == (0,)
    # pre-registered under the current rule, then a guarded constant changes: refused by itself
    prereg(capture_db.conn, b, tmp_path)
    assert require(capture_db.conn, b)
    monkeypatch.setattr(conf, "KEYWORD_MIN_SHARED", 1)
    with pytest.raises(PreregistrationMissing):
        require(capture_db.conn, b)
    monkeypatch.undo()
    assert require(capture_db.conn, b)


def test_both_views_rows_are_exported_counted_and_purged(capture_db, pg_url, tmp_path):
    b = seed_three(capture_db)
    prereg(capture_db.conn, b, tmp_path)
    stage(capture_db.conn, b, hn_connector(capture_db, FakeShowHN([]), tmp_path), {})
    conn = capture_db.conn
    rows = conn.execute(
        "SELECT view, count(*) FROM brief_selection_case GROUP BY view ORDER BY view"
    ).fetchall()
    assert rows == [("follow_through", 3), ("launch", 3), ("launch_undeclared", 3)]
    sel = conn.execute("SELECT views, context, balance FROM brief_selection").fetchone()
    assert set(sel[0]) == set(sel[2]) == {"follow_through", "launch", "launch_undeclared"}
    assert sel[1]["label"] == "context, not a headline"
    # export (project level): both views' rows with their view
    res = export_jsonl(pg_url, tmp_path / "export", tables=["brief_selection_case"])
    assert res.tables["brief_selection_case"]["rows"] == 9
    text = (tmp_path / "export" / "brief_selection_case.jsonl").read_text()
    assert text.count('"view":"follow_through"') + text.count('"view": "follow_through"') == 3
    undeclared = text.count('"view":"launch_undeclared"') + text.count(
        '"view": "launch_undeclared"'
    )
    assert undeclared == 3  # view B's undeclared-launch sub-population (ADR-084)
    # inventory: rows per view, repos once
    inv = {t.table: t for t in inventory(conn).tables}
    assert inv["brief_selection_case"].rows == 9
    assert inv["brief_selection_case"].distinct_repos == 3
    # a repo opt-out removes its rows of every view
    kf = "org-k/kubeforge"
    conn.execute(
        "INSERT INTO repos (id, host, host_id, full_name, first_seen_at)"
        " VALUES ('github:8900001', 'github', 8900001, %s, %s)",
        (kf, NOW),
    )
    requests.purge_repo(
        capture_db, LocalSnapshotStore(Path(tmp_path) / "snap"), "github:8900001",
        DeletionLog(capture_db, "objection"),
    )  # fmt: skip
    left = conn.execute(
        "SELECT count(*) FROM brief_selection_case WHERE repo_full_name = %s", (kf,)
    ).fetchone()
    assert left == (0,)
    assert conn.execute("SELECT count(*) FROM brief_selection_case").fetchone() == (6,)
