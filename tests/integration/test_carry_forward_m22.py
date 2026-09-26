"""ADR-079: carry a final shortlist forward to a later brief version (PRD R4.7, R4.8, R8.2) on
Postgres, with the synthetic example brief and fakes only (no network, no key):

- the allow-list: a change to `field.*` or `window.*` is refused with the differing fields and
  nothing is written;
- a carry-forward copies candidates (verdicts, reasons, distances, provenance), each latest
  decision (original role, channel, reason, bulk marker and time, plus the carry's own role,
  reason, time and source version), the named-project confirmation, and finalizes the target
  with the same members, its mention scope and a `carried_forward` run row; the precision keeps
  the source's label and says "carried from v1";
- a second call is refused (the target is not empty);
- a repo refused since the source was finalized is dropped and counted (CB-13);
- the target version then runs only the selection, behind its own pre-registration gate;
- no brief text is stored;
- the CLI command, its defaults and its refusal.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from pigtail.briefs.candidates import CandidateStore
from pigtail.briefs.carry import CarryError, CarryRefused, blocking_changes, carry_forward
from pigtail.briefs.model import Brief, Success, Window
from pigtail.briefs.shortlist import Shortlist, ShortlistFinal
from pigtail.briefs.store import BriefStore
from pigtail.cli import main
from pigtail.privacy.suppression import Suppressions
from tests.github_fake import series
from tests.integration.test_brief_run_m22 import World, example, make_world, run, runs
from tests.integration.test_cli_run_m22 import BID
from tests.integration.test_github_per_repo_m1t24 import dump_all_tables
from tests.integration.test_selection_m22 import finalize, prereg
from tests.relevance_fake import RelevanceBatchBackend

pytestmark = pytest.mark.db

REASON = "only the success definition changed (synthetic test)"
NOTE = "synthetic carry-forward marker note, never stored in the database"


def v2(**update: Any) -> Brief:
    """Version 2 of the example: ranks on adoption instead of attention (success.* only)."""
    b = example()
    success = Success(
        primary="adoption",
        primary_threshold="top_quartile",
        fallbacks={"too_few_winners": {"min_winners": 10, "steps": ["relax_primary_to_top_third"]}},
    )
    return b.model_copy(update={"version": 2, "success": success, "notes": NOTE, **update})


def count(w: World, sql: str, *args: Any) -> int:
    return int(w.conn.execute(sql, args).fetchone()[0])


def decisions(w: World, version: int) -> dict[str, dict[str, Any]]:
    cur = w.conn.execute(
        "SELECT * FROM shortlist_decision WHERE brief_version = %s ORDER BY id", (version,)
    )
    cols = [d.name for d in cur.description or []]
    out: dict[str, dict[str, Any]] = {}
    for r in cur.fetchall():
        out[r[cols.index("candidate_ref")]] = dict(zip(cols, r, strict=True))
    return out


def scope(w: World, version: int) -> set[str]:
    rows = w.conn.execute(
        "SELECT repo_full_name FROM brief_shortlist_entry WHERE brief_version = %s"
        " AND status = 'final'",
        (version,),
    ).fetchall()
    return {r[0] for r in rows}


@pytest.fixture
def w(capture_db: Any, tmp_path: Any) -> World:
    world = make_world(capture_db, tmp_path)
    assert run(world, RelevanceBatchBackend()).status == "awaiting_review"
    finalize(world)  # owner: bulk accept on `relevant`, reject `uncertain`, confirm the exemplar
    return world


def test_allow_list_refuses_field_and_window_changes_and_writes_nothing(w):
    b1 = example()
    changed_field = b1.field.model_copy(update={"include": [*b1.field.include, "config diffs"]})
    for target, path in (
        (v2(field=changed_field), "field.include"),
        (v2(window=Window(months=12)), "window.months"),
    ):
        assert blocking_changes(b1, target) == [path]
        with pytest.raises(CarryRefused) as ei:
            carry_forward(w.conn, b1, target, reason=REASON, reviewer="owner")
        assert ei.value.paths == [path] and path in str(ei.value)
    # success.*, panel.*, report.*, notes and store metadata are allowed
    assert blocking_changes(b1, v2()) == []
    assert count(w, "SELECT count(*) FROM brief_candidate WHERE brief_version = 2") == 0
    assert count(w, "SELECT count(*) FROM brief_runs WHERE brief_version = 2") == 0
    assert count(w, "SELECT count(*) FROM brief_shortlist WHERE brief_version = 2") == 0


def test_carry_forward_keeps_candidates_decisions_confirmations_and_precision_label(w):
    b1, b2 = example(), v2()
    src = Shortlist(w.conn, b1)
    src_prec = src.status()["precision"]
    assert src_prec["label"].startswith("not item-reviewed")  # bulk accept on `relevant`
    (r1,) = runs(w)

    res = carry_forward(w.conn, b1, b2, reason=REASON, reviewer="owner")
    assert (res.from_version, res.to_version, res.dropped_refused) == (1, 2, 0)
    assert "success.primary" in res.changed_fields and "notes" in res.changed_fields

    # candidates: verdicts, reasons, distances and provenance, plus carried_from_version
    c1 = {c.ref: c for c in CandidateStore(w.conn, BID, 1).all()}
    c2 = {c.ref: c for c in CandidateStore(w.conn, BID, 2).all()}
    assert set(c2) == set(c1) and res.candidates == len(c1)
    for ref, c in c2.items():
        o = c1[ref]
        assert c.carried_from_version == 1 and o.carried_from_version is None
        assert (c.verdict, c.reason, c.distance, c.rubric_version, c.relevance) == (
            o.verdict, o.reason, o.distance, o.rubric_version, o.relevance,
        )  # fmt: skip
        assert (c.sources, c.panel, c.named_index, c.resolution) == (
            o.sources, o.panel, o.named_index, o.resolution,
        )  # fmt: skip
    # the exemplar's confirmation is carried (ADR-054.3)
    assert c2["named:exemplar:0"].resolution == "confirmed"
    assert c2["gh:org-z/showcase-engine"].resolution_rule == "user_confirmed"

    # decisions: the original ones, plus the carry's role, reason, time and source version
    d1, d2 = decisions(w, 1), decisions(w, 2)
    assert set(d2) == set(d1) and res.decisions == len(d1)
    (r2,) = [r for r in runs(w) if r["brief_version"] == 2]
    for ref, d in d2.items():
        o = d1[ref]
        for k in ("decision", "reason", "reviewer_role", "via", "decided_at", "bulk_id"):
            assert d[k] == o[k], (ref, k)
        assert d["bulk_verdict"] == o["bulk_verdict"]
        assert (d["carried_from_version"], d["carried_role"], d["carried_reason"]) == (
            1, "owner", REASON,
        )  # fmt: skip
        assert d["carried_at"] is not None and d["brief_run_id"] == r2["id"]
        assert o["carried_from_version"] is None

    # the target is final with the same members and its own final mention scope
    tgt = Shortlist(w.conn, b2)
    st = tgt.status()
    assert st["status"] == "final" and st["finalized_role"] == "owner"
    assert (st["carried_from_version"], st["carried_reason"]) == (1, REASON)
    assert scope(w, 2) == scope(w, 1) and len(scope(w, 2)) == res.shortlisted > 0
    assert tgt.members() == src.members()
    # precision: the source's label (not item-reviewed), then "carried from v1"
    assert st["precision"]["label"] == src_prec["label"] + "; carried from v1"
    assert st["precision"]["value"] == src_prec["value"]
    assert tgt.view()["precision"]["label"].endswith("carried from v1")
    assert res.precision["carried_from_version"] == 1
    with pytest.raises(ShortlistFinal):
        tgt.decide(["gh:org-s/yaml-guard"], "reject", "late change")

    # the run row: carried_forward, linked to the source run, the upstream stages done
    assert r2["status"] == "carried_forward" and r2["carried_from"] == r1["id"]
    assert r2["brief_hash"] == b2.content_hash()
    assert {k: v["status"] for k, v in r2["stages"].items()} == {
        "discovery": "done", "relevance": "done", "shortlist": "done",
    }  # fmt: skip
    assert r2["checkpoint"]["run_date"] == r1["checkpoint"]["run_date"]
    assert r2["prompt_versions"] == r1["prompt_versions"]
    # the source is left as it was
    assert Shortlist(w.conn, b1).status()["precision"] == src_prec


def test_second_carry_forward_is_refused_because_the_target_is_not_empty(w):
    b1, b2 = example(), v2()
    carry_forward(w.conn, b1, b2, reason=REASON, reviewer="owner")
    before = (
        count(w, "SELECT count(*) FROM shortlist_decision"),
        count(w, "SELECT count(*) FROM brief_runs"),
    )
    with pytest.raises(CarryError, match="already has a shortlist"):
        carry_forward(w.conn, b1, b2, reason=REASON, reviewer="owner")
    after = (
        count(w, "SELECT count(*) FROM shortlist_decision"),
        count(w, "SELECT count(*) FROM brief_runs"),
    )
    assert after == before
    # other refusals: not later, not final, another brief
    with pytest.raises(CarryError, match="later version"):
        carry_forward(w.conn, b2, b1, reason=REASON)
    b3 = v2(version=3)
    with pytest.raises(CarryError, match="reason"):
        carry_forward(w.conn, b1, b3, reason="  ")


def test_source_not_final_is_refused(capture_db, tmp_path):
    world = make_world(capture_db, tmp_path)
    assert run(world, RelevanceBatchBackend()).status == "awaiting_review"
    with pytest.raises(CarryError, match="not final"):
        carry_forward(world.conn, example(), v2(), reason=REASON)
    assert count(world, "SELECT count(*) FROM brief_runs WHERE brief_version = 2") == 0


def test_repo_refused_since_is_dropped_and_reported(w):
    b1, b2 = example(), v2()
    host_id = CandidateStore(w.conn, BID, 1).get("gh:org-s/yaml-guard").repo_host_id
    assert host_id is not None and "org-s/yaml-guard" in scope(w, 1)
    sup = Suppressions(repos=frozenset({f"github:{host_id}"}))
    res = carry_forward(w.conn, b1, b2, reason=REASON, reviewer="owner", suppressions=sup)
    assert res.dropped_refused == 1
    assert CandidateStore(w.conn, BID, 2).get("gh:org-s/yaml-guard") is None
    assert "gh:org-s/yaml-guard" not in decisions(w, 2)
    assert "org-s/yaml-guard" not in scope(w, 2)
    assert scope(w, 2) == scope(w, 1) - {"org-s/yaml-guard"}
    assert res.candidates == CandidateStore(w.conn, BID, 1).count() - 1


def test_target_runs_only_the_selection_behind_its_preregistration_gate(w, tmp_path):
    b1, b2 = example(), v2()
    prereg(w, b1, tmp_path)  # v1's pre-registration doesn't cover v2
    res = carry_forward(w.conn, b1, b2, reason=REASON, reviewer="owner")
    for c in Shortlist(w.conn, b2).candidates.all():
        r = w.gh.by_name(c.repo_full_name or "")
        if r is not None:
            w.gh.daily[r["id"]] = series("burst", days=700)
    fb = RelevanceBatchBackend()
    run2 = [r for r in runs(w) if r["brief_version"] == 2]
    n_cands = count(w, "SELECT count(*) FROM brief_candidate WHERE brief_version = 2")

    refused = run(w, fb, b2)
    assert refused.exit_code == 7 and "pre-registration" in refused.message
    assert [r for r in runs(w) if r["brief_version"] == 2] == run2  # untouched
    assert count(w, "SELECT count(*) FROM brief_selection") == 0

    prereg(w, b2, tmp_path)
    ok = run(w, fb, b2)
    assert ok.exit_code == 0 and ok.status == "succeeded", ok.message
    assert ok.brief_run_id == res.brief_run_id  # the carried run row, resumed for the selection
    assert fb.submitted == []  # no discovery, no relevance: no model call at all
    assert count(w, "SELECT count(*) FROM brief_candidate WHERE brief_version = 2") == n_cands
    sel = w.conn.execute(
        "SELECT brief_version, brief_run_id, summary FROM brief_selection"
    ).fetchall()
    assert len(sel) == 1 and sel[0][0] == 2 and sel[0][1] == res.brief_run_id
    # v2 ranks on adoption, which has no connector until M23b: no winners, and it says so
    summary = sel[0][2]
    assert summary["final_definition"]["primary"] == "adoption"
    assert summary["counts"]["winners"] == 0
    assert summary["undetermined_by_dimension"]["adoption"] >= 1


def rows_of(w: World, version: int) -> str:
    """Every row of one brief version in the tables a carry-forward writes, as JSON text."""
    return "\n".join(
        w.conn.execute(
            f"SELECT coalesce(json_agg(t)::text, '') FROM {table} t WHERE brief_version = %s",
            (version,),
        ).fetchone()[0]
        for table in (
            "brief_candidate",
            "shortlist_decision",
            "brief_shortlist",
            "brief_shortlist_entry",
            "brief_runs",
        )
    )


def test_no_brief_text_is_stored(w):
    """The carry-forward writes no brief text: the project, the named projects and the notes
    appear nowhere, and no other brief value appears in the target's rows unless the source's
    rows already held it (discovery signals keep the search term that found a repo, ADR-076)."""
    b1, b2 = example(), v2()
    carry_forward(w.conn, b1, b2, reason=REASON, reviewer="owner")
    source, written = rows_of(w, 1), rows_of(w, 2)
    assert "carried_from_version" in written and REASON in written
    never = [NOTE, b2.project.description, b2.project.name]
    never += [b2.field.reference_cases[0].name, b2.distribution_exemplars.projects[0].name]
    dump = dump_all_tables(w.db)
    for text in never:
        assert text not in dump, text
    assert b2.expansion is not None
    others = [b2.field.core_field, *b2.field.include, *b2.field.exclude, *b2.field.widening_steps]
    others += [*b2.expansion.keywords, *b2.expansion.search_queries, *b2.expansion.users]
    for text in others:
        assert written.count(text) <= source.count(text), text


# --- CLI ------------------------------------------------------------------------------------------
def test_cli_carry_forward_defaults_refusal_and_json(
    capture_db, pg_url, tmp_path, monkeypatch, capsys
):
    capture_db.conn.autocommit = True
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("PIGTAIL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    from pigtail.config import Settings

    assert main(["brief", "new", "--example", "--id", BID]) == 0
    store = BriefStore.from_settings(Settings.from_env())
    b1 = store.get(BID).brief
    sl = Shortlist(capture_db.conn, b1)
    sl.ensure(None)
    sl.add("https://github.com/org-s/yaml-guard", "fits the core field", reviewer="owner")
    sl.finalize(reviewer="owner")
    # v2 changes only the success definition; v3 also the window
    text = store.get(BID).yaml_text
    f2 = tmp_path / "v2.yaml"
    f2.write_text(text.replace("primary_threshold: top_quartile", "primary_threshold: top_decile"))
    assert main(["brief", "edit", BID, "--from", str(f2)]) == 0
    capsys.readouterr()

    assert (
        main(["brief", "shortlist", "carry-forward", BID, "--as", "owner", "--reason", REASON]) == 0
    )
    out = capsys.readouterr().out
    assert "v1 to v2" in out and "carried from v1" in out and "success.primary_threshold" in out
    assert "pigtail brief preregister" in out
    assert scope_names(capture_db, 2) == {"org-s/yaml-guard"}
    # again: refused, the target is not empty
    assert (
        main(["brief", "shortlist", "carry-forward", BID, "--as", "owner", "--reason", REASON]) == 1
    )
    assert "already has a shortlist" in capsys.readouterr().err

    f3 = tmp_path / "v3.yaml"
    f3.write_text(store.get(BID).yaml_text.replace("months: 18", "months: 12"))
    assert main(["brief", "edit", BID, "--from", str(f3)]) == 0
    capsys.readouterr()
    args = ["brief", "shortlist", "carry-forward", BID, "--from", "2", "--as", "owner"]
    assert main([*args, "--reason", REASON, "--json"]) == 1
    assert "window.months" in capsys.readouterr().err
    # --as and --reason are required
    with pytest.raises(SystemExit):
        main(["brief", "shortlist", "carry-forward", BID, "--reason", REASON])
    # v4 goes back to v2's window: carried from v2 (the newest final one), JSON output
    f4 = tmp_path / "v4.yaml"
    f4.write_text(store.get(BID).yaml_text.replace("months: 12", "months: 18"))
    assert main(["brief", "edit", BID, "--from", str(f4)]) == 0
    capsys.readouterr()
    assert main(["brief", "shortlist", "carry-forward", BID, "--as", "user", "--reason", REASON,
                 "--json"]) == 0  # fmt: skip
    got = json.loads(capsys.readouterr().out)
    assert (got["from_version"], got["to_version"], got["shortlisted"]) == (2, 4, 1)


def scope_names(db: Any, version: int) -> set[str]:
    rows = db.conn.execute(
        "SELECT repo_full_name FROM brief_shortlist_entry WHERE brief_version = %s"
        " AND status = 'final'",
        (version,),
    ).fetchall()
    return {r[0] for r in rows}
