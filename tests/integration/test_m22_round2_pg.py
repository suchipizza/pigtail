"""M22 verifier round 2 fixes on Postgres (synthetic example brief, fakes only; no network):

- R8.2 / ADR-065: the selection refuses to run before the brief version's pre-registration is
  recorded (exit code 7; nothing fetched, computed or stored; the run row untouched), then runs;
  `pigtail brief preregister` prints hashes, records a file, refuses a file quoting the brief
  and refuses a pre-registration after the outcome sort;
- CB-13: a repo refused by GitHub id only, added by URL before its metadata was known, leaves
  the brief version when its id arrives: no star history, no final scope entry, no case row;
  a refusal whose id pigtail already holds is honoured at finalize;
- R4.7 / M22-P: decisions from one bulk action on the filter's own verdict are "not
  item-reviewed"; `shortlist show` prints the defaulted brief fields and warnings.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pigtail.briefs.preregistration import (
    PreregistrationError,
    PreregistrationMissing,
    hashes,
    require,
)
from pigtail.briefs.preregistration import record as preregister
from pigtail.briefs.shortlist import Shortlist
from pigtail.briefs.store import BriefStore
from pigtail.cli import main
from pigtail.privacy import suppression
from tests.github_fake import series
from tests.integration.test_brief_run_m22 import World, example, make_world, run, runs
from tests.integration.test_cli_run_m22 import BID
from tests.integration.test_selection_m22 import finalize
from tests.relevance_fake import RelevanceBatchBackend

pytestmark = pytest.mark.db


@pytest.fixture
def w(capture_db: Any, tmp_path: Any) -> World:
    return make_world(capture_db, tmp_path)


@pytest.fixture
def cli(capture_db: Any, pg_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """The CLI on a throwaway database and briefs dir, with the synthetic example brief."""
    capture_db.conn.autocommit = True
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("PIGTAIL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LLM_BACKEND", "api")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert main(["brief", "new", "--example", "--id", BID]) == 0
    return capture_db


def prereg_file(tmp_path: Path, text: str = "") -> Path:
    f = tmp_path / "2026-09-26-brief-1.md"
    f.write_text(text or "# Pre-registration (synthetic)\nHypotheses: MC-01, MC-04.\n")
    return f


def count(w: World, sql: str, *args: Any) -> int:
    return int(w.conn.execute(sql, args).fetchone()[0])


# --- R8.2 gate -----------------------------------------------------------------------------------
def test_r8_2_selection_refused_until_preregistered_then_runs(w, tmp_path):
    fb = RelevanceBatchBackend()
    assert run(w, fb).status == "awaiting_review"
    finalize(w)
    for c in Shortlist(w.conn, example()).candidates.all():
        r = w.gh.by_name(c.repo_full_name or "")
        if r is not None:
            w.gh.daily[r["id"]] = series("burst", days=700)
    (before,) = runs(w)
    requests_before = len(w.gh.requests)

    out = run(w, fb)
    assert out.exit_code == 7 and "pre-registration" in out.message
    assert "TEMPLATE-brief.md" in out.message and "Nothing was computed or fetched" in out.message
    (after,) = runs(w)
    assert after == before  # the run row is untouched
    assert count(w, "SELECT count(*) FROM brief_selection") == 0
    assert count(w, "SELECT count(*) FROM star_history_fetch") == 0
    assert len(w.gh.requests) == requests_before  # no GitHub request at all
    # `--stage selection` alone: the same refusal
    assert run(w, fb, stages=("selection",)).exit_code == 7
    with pytest.raises(PreregistrationMissing):
        require(w.conn, example())

    rec = preregister(w.conn, example(), prereg_file(tmp_path), commit="0123abc")
    assert rec.brief_hash == example().content_hash() and rec.git_commit == "0123abc"
    assert rec.selection_params_sha256 == hashes(example())["selection_params_sha256"]
    ok = run(w, fb)
    assert ok.exit_code == 0 and ok.status == "succeeded", ok.message
    assert count(w, "SELECT count(*) FROM brief_selection") == 1
    # after the outcome sort, a pre-registration can't be recorded for this version
    with pytest.raises(PreregistrationError, match="already has an outcome sort"):
        preregister(w.conn, example(), prereg_file(tmp_path))


def test_r8_2_preregistration_is_bound_to_version_content_and_selection_params(w, tmp_path):
    b = example()
    preregister(w.conn, b, prereg_file(tmp_path))
    assert require(w.conn, b).brief_version == 1
    # another brief content (an edit is a new version with another hash): not pre-registered
    edited = b.model_copy(update={"panel": b.panel.model_copy(update={"winners": 16})})
    with pytest.raises(PreregistrationMissing):
        require(w.conn, edited)
    # a recorded row whose selection parameters differ (e.g. the selection rule changed since)
    w.conn.execute("UPDATE brief_preregistration SET selection_params_sha256 = repeat('0', 64)")
    with pytest.raises(PreregistrationMissing, match="other selection parameters"):
        require(w.conn, b)
    # a file quoting brief content is refused (R8.2: hashes only)
    with pytest.raises(PreregistrationError, match="quotes brief content"):
        preregister(w.conn, b, prereg_file(tmp_path, f"Field: {b.field.core_field}\n"))
    with pytest.raises(PreregistrationError, match="commit"):
        preregister(w.conn, b, prereg_file(tmp_path), commit="not-a-sha")
    with pytest.raises(PreregistrationError, match="no such file"):
        preregister(w.conn, b, tmp_path / "missing.md")
    # the stored row holds ids, hashes, the path and the commit: no brief text
    row = w.conn.execute("SELECT * FROM brief_preregistration LIMIT 1").fetchone()
    assert b.project.description not in repr(row) and b.field.core_field not in repr(row)


def test_r8_2_preregister_cli_prints_hashes_and_records(cli, capsys, tmp_path):
    from pigtail.config import Settings

    brief = BriefStore.from_settings(Settings.from_env()).get(BID).brief
    capsys.readouterr()
    assert main(["brief", "preregister", BID, "--print-hashes", "--json"]) == 0
    h = json.loads(capsys.readouterr().out)
    assert h == hashes(brief)
    assert main(["brief", "preregister", BID, "--print-hashes"]) == 0
    out = capsys.readouterr().out
    assert h["brief_sha256"] in out and "reveal no brief text" in out
    assert brief.project.description not in out
    assert main(["brief", "preregister", BID]) == 2  # neither --file nor --print-hashes
    bad = prereg_file(tmp_path, f"Target: {brief.project.target_users.primary}\n")
    assert main(["brief", "preregister", BID, "--file", str(bad)]) == 1
    assert "quotes brief content" in capsys.readouterr().err
    good = prereg_file(tmp_path)
    assert main(["brief", "preregister", BID, "--file", str(good), "--commit", "abcdef1"]) == 0
    assert "pre-registration recorded" in capsys.readouterr().out
    assert cli.conn.execute("SELECT git_commit FROM brief_preregistration").fetchone() == (
        "abcdef1",
    )


# --- CB-13: refused by id only, added by URL -----------------------------------------------------
def test_cb13_repo_refused_by_id_added_by_url_never_fetched_or_in_final_scope(w, tmp_path):
    fb = RelevanceBatchBackend()
    assert run(w, fb).status == "awaiting_review"
    engine = w.gh.by_name("org-z/showcase-engine")
    assert engine is not None
    hid = int(engine["id"])
    # the owner objected by GitHub id only; pigtail holds no id for the name yet
    suppression.add(w.db, "repo", f"github:{hid}", platform="github", reason="objection")
    assert Shortlist(w.conn, example()).known_host_ids("org-z/showcase-engine") == set()
    finalize(w)  # adds org-z/showcase-engine by URL (metadata unknown), then finalizes
    for c in Shortlist(w.conn, example()).candidates.all():
        r = w.gh.by_name(c.repo_full_name or "")
        if r is not None:
            w.gh.daily[r["id"]] = series("burst", days=700)
    preregister(w.conn, example(), prereg_file(tmp_path))
    out = run(w, fb)
    assert out.exit_code == 0, out.message
    (r,) = runs(w)
    assert r["stages"]["selection"]["result"]["fetch"]["failed"].get("refused") == 1
    name = "org-z/showcase-engine"
    assert count(w, "SELECT count(*) FROM star_history_fetch WHERE repo_host_id = %s", hid) == 0
    assert count(w, "SELECT count(*) FROM repo_star_daily WHERE repo_host_id = %s", hid) == 0
    assert count(w, "SELECT count(*) FROM brief_candidate WHERE repo_full_name = %s", name) == 0
    assert (
        count(w, "SELECT count(*) FROM brief_shortlist_entry WHERE repo_full_name = %s", name) == 0
    )
    assert (
        count(w, "SELECT count(*) FROM brief_selection_case WHERE repo_full_name = %s", name) == 0
    )


def test_cb13_refusal_by_a_known_id_is_honoured_at_finalize(w):
    fb = RelevanceBatchBackend()
    assert run(w, fb).status == "awaiting_review"
    sl = Shortlist(w.conn, example())
    on, _ = sl.members()
    target = sorted(on or [c.repo_full_name for c in sl.candidates.all() if c.repo_full_name])[0]
    got = sl.candidates.get(f"gh:{target}")
    assert got is not None and got.repo_host_id is not None
    sl.decide_where("accept", "fits", verdict="relevant", reviewer="owner")
    sl.decide_where("reject", "no", verdict="uncertain", reviewer="owner")
    suppression.add(
        w.db, "repo", f"github:{got.repo_host_id}", platform="github", reason="objection"
    )
    Shortlist(w.conn, example()).finalize(reviewer="owner")
    final = {
        n
        for (n,) in w.conn.execute(
            "SELECT repo_full_name FROM brief_shortlist_entry WHERE status = 'final'"
        )
    }
    assert target not in final
    assert count(w, "SELECT count(*) FROM brief_candidate WHERE repo_full_name = %s", target) == 0


# --- R4.7 / M22-P: bulk decisions on the filter's verdict ----------------------------------------
def test_m22_p_bulk_on_the_filter_verdict_is_not_item_reviewed(w):
    fb = RelevanceBatchBackend()
    assert run(w, fb).status == "awaiting_review"
    sl = Shortlist(w.conn, example())
    n = sl.decide_where("accept", "bulk: relevant", verdict="relevant", reviewer="owner")
    assert n > 0
    p = sl.precision()
    assert p["label"] == "not item-reviewed (bulk action on the filter's verdict)"
    assert p["bulk_on_filter_verdict"] == p["decided"] and p["item_reviewed"] == 0
    rows = w.conn.execute(
        "SELECT DISTINCT bulk_id, bulk_verdict FROM shortlist_decision WHERE bulk_id IS NOT NULL"
    ).fetchall()
    assert len(rows) == 1 and rows[0][1] == "relevant" and rows[0][0].startswith("bulk_")
    # one item reviewed on its own afterwards: a mixed label that says how many were bulk
    one = next(c.ref for c in sl.candidates.all() if c.verdict == "relevant" and not c.is_named)
    sl.decide([one], "accept", "read the README", reviewer="owner")
    p2 = sl.precision()
    assert p2["item_reviewed"] == 1 and p2["label"].startswith("owner-checked; ")
    assert "by bulk action, not item-reviewed" in p2["label"]


def test_r4_7_shortlist_show_text_prints_defaulted_fields_and_warnings(cli, capsys):
    from pigtail.config import Settings

    brief = BriefStore.from_settings(Settings.from_env()).get(BID).brief
    Shortlist(cli.conn, brief).ensure(None)
    capsys.readouterr()
    assert main(["brief", "shortlist", "show", BID]) == 0
    out = capsys.readouterr().out
    fields = brief.defaulted_fields()
    assert fields and f"Brief fields to confirm ({len(fields)} still at their default)" in out
    assert all(f"  {f['field']}: " in out for f in fields)
    for warning in brief.warnings():
        assert f"brief warning: {warning}" in out
