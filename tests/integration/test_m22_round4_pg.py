"""M22 verifier round 4 on Postgres (ADR-082; synthetic data, fakes only, no network): the
selection is refused without the Show HN connector (stage and runner: exit 8, nothing fetched,
computed or stored, run row resumable), a repo's launch-lookup records of an earlier rule are
replaced, title-only candidates are rejected by the conservative rule and counted, and the
estimate's selection state comes from the run checkpoint. Every name is made up.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from pigtail.briefs.candidates import CandidateStore
from pigtail.briefs.estimate import SelectionState, selection_state
from pigtail.briefs.outcomes import LAUNCH_LOOKUP_SOURCE, LaunchLookupUnavailable
from pigtail.briefs.runner import EXIT_NO_LAUNCH_LOOKUP
from pigtail.briefs.selection_store import view
from pigtail.capture.snapshots import LocalSnapshotStore
from pigtail.connectors.hn import HNShowDiscoveryConnector
from tests.discovery_fake import FakeShowHN
from tests.integration.test_brief_run_m22 import example, make_world, run, runs
from tests.integration.test_launch_lookup_m22 import KF, MK, NL, hit, hn_connector, prereg, seed
from tests.integration.test_launch_lookup_m22 import stage as run_stage_with
from tests.integration.test_selection_m22 import finalize
from tests.integration.test_selection_m22 import prereg as prereg_world
from tests.relevance_fake import RelevanceBatchBackend

pytestmark = pytest.mark.db

T = datetime(2025, 10, 20, 17, tzinfo=UTC)


def count(conn: Any, table: str) -> int:
    return int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


def off_connector(tmp: Path) -> HNShowDiscoveryConnector:
    return HNShowDiscoveryConnector(
        store=LocalSnapshotStore(tmp / "snap-off"),
        pseudonymizer=None,
        env={"PIGTAIL_CONNECTOR_HN_SHOWHN_ENABLED": "false"},
    )


# --- 2. refusal without the Show HN connector ---------------------------------------------------
@pytest.mark.parametrize("which", ["none", "disabled"])
def test_stage_refuses_without_the_show_hn_connector(capture_db, tmp_path, which):
    b = seed(capture_db)
    prereg(capture_db.conn, b, tmp_path)
    hn = None if which == "none" else off_connector(tmp_path)
    cp: dict[str, Any] = {}
    with pytest.raises(LaunchLookupUnavailable, match="PIGTAIL_CONNECTOR_HN_SHOWHN_ENABLED"):
        run_stage_with(capture_db.conn, b, hn, cp)
    assert cp == {}  # not even the as-of date was fixed
    assert count(capture_db.conn, "brief_selection") == 0
    assert count(capture_db.conn, "evidence") == 0


def test_runner_refuses_with_exit_8_and_resumes_once_the_connector_is_on(capture_db, tmp_path):
    w = make_world(capture_db, tmp_path)
    fb = RelevanceBatchBackend()
    first = run(w, fb)
    assert first.status == "awaiting_review"
    finalize(w)
    prereg_world(w, example(), tmp_path)
    (before,) = runs(w)
    on = w.hnconn
    w.hnconn = None  # type: ignore[assignment]
    out = run(w, fb)
    assert out.exit_code == EXIT_NO_LAUNCH_LOOKUP == 8
    assert "PIGTAIL_CONNECTOR_HN_SHOWHN_ENABLED" in out.message
    (after,) = runs(w)
    assert after["status"] == before["status"] and after["checkpoint"] == before["checkpoint"]
    assert after["resumes"] == before["resumes"] and "selection" not in after["stages"]
    assert count(w.conn, "brief_selection") == 0
    w.hnconn = off_connector(tmp_path)  # a disabled connector is refused the same way
    assert run(w, fb).exit_code == EXIT_NO_LAUNCH_LOOKUP
    w.hnconn = on
    ok = run(w, fb)
    assert ok.exit_code == 0 and ok.brief_run_id == first.brief_run_id, ok.message
    assert count(w.conn, "brief_selection") == 1


# --- 1. the title rule on stored data -------------------------------------------------------------
def test_old_rule_records_are_replaced_and_title_only_candidates_counted(capture_db, tmp_path):
    b = seed(capture_db)
    prereg(capture_db.conn, b, tmp_path)
    store = CandidateStore(capture_db.conn, b.brief_id, b.version)
    # an anchor-v2 title match carried into this version (no `rule` field): not an anchor
    store.add_sources(
        f"gh:{NL}",
        [{"source": LAUNCH_LOOKUP_SOURCE, "hn_item_id": 8999, "time": T.isoformat(),
          "points": 5, "kind": "show_hn", "match": "title"}],
    )  # fmt: skip
    hits = [
        # the verifier's kind of false match: the name inside another product's title
        hit(8301, "Show HN: Visual Nolaunch Code, an editor", None, T, 30, "show_hn"),
        hit(8302, "Show HN: Nolaunch – a different tool", "https://github.com/org-x/other", T, 30,
            "show_hn"),  # links another repo
        hit(8303, "Launch HN: Nolaunch (YC S25) – before the repo existed", None,
            datetime(2024, 12, 1, tzinfo=UTC), 30, "launch_hn"),  # outside the window anyway
        hit(8304, "Launch HN: Nolaunch (YC S25) – launch week", None, T, 44, "launch_hn"),
    ]  # fmt: skip
    hn = hn_connector(capture_db, FakeShowHN(hits), tmp_path)
    res = run_stage_with(capture_db.conn, b, hn, {})
    lk = res.fetch["launch_lookup"]
    assert lk["title_rejected"] == {"links_other_repo": 1, "not_product_slot": 1}
    assert lk["title_rejected_total"] == 2
    nl = store.get(f"gh:{NL}")
    assert nl is not None
    assert [(s["hn_item_id"], s["match"], s["rule"]) for s in nl.sources] == [
        (8304, "title", "anchor-v3")
    ]  # the anchor-v2 record is gone
    v = view(capture_db.conn, b.brief_id, 1)
    a = {c["repo_full_name"]: c["detail"] for c in v["cases"]}[NL]["anchor"]
    assert a["type"] == "launch" and a["via"] == "lookup:title" and a["source"] == "launch_hn"
    warnings = v["selection"]["summary"]["warnings"]
    assert any("2 title-only candidates rejected" in x for x in warnings)
    assert {KF, MK} <= {c["repo_full_name"] for c in v["cases"]}


# --- 5. the estimate's selection state ------------------------------------------------------------
def test_selection_state_reads_the_checkpoint_and_the_final_shortlist(capture_db, tmp_path):
    w = make_world(capture_db, tmp_path)
    fb = RelevanceBatchBackend()
    run(w, fb)
    st = selection_state(w.conn, example())
    assert st.pending and st.shortlisted is None and st.lookup_done == 0  # not final yet
    finalize(w)
    (r,) = runs(w)
    cp = dict(r["checkpoint"])
    cp["selection"] = {"fetch": {"launch_lookup_done": ["gh:org-a/x", "gh:org-b/y"]}}
    from psycopg.types.json import Jsonb

    w.conn.execute("UPDATE brief_runs SET checkpoint = %s WHERE id = %s", (Jsonb(cp), r["id"]))
    st = selection_state(w.conn, example())
    assert st.pending and st.shortlisted is not None and st.shortlisted > 0
    assert st.lookup_done == 2
    prereg_world(w, example(), tmp_path)
    assert run(w, fb).exit_code == 0
    assert selection_state(w.conn, example()) == SelectionState(pending=False)
