"""M22 CLI: `pigtail run --brief` shows the cost estimate first, `--dry-run` does nothing, paid
steps need `--approve-paid` (R18.5, R19.1); `pigtail brief shortlist show|accept|reject|add|
finalize` (R4.7). Synthetic example brief in a tmp briefs dir; no network."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from pigtail.briefs.candidates import Candidate, CandidateStore
from pigtail.briefs.shortlist import Shortlist
from pigtail.briefs.store import BriefStore
from pigtail.cli import main

pytestmark = pytest.mark.db

NOW = datetime(2026, 9, 25, 18, 0, tzinfo=UTC)
BID = "example-config-linter"


@pytest.fixture
def cli_env(capture_db: Any, pg_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    capture_db.conn.autocommit = True
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("PIGTAIL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LLM_BACKEND", "api")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert main(["brief", "new", "--example", "--id", BID]) == 0
    return capture_db


def n_runs(db: Any) -> int:
    return int(db.conn.execute("SELECT count(*) FROM brief_runs").fetchone()[0])


def test_r19_1_r18_5_run_shows_estimate_first_dry_run_and_approval(cli_env, capsys):
    db = cli_env
    capsys.readouterr()
    assert main(["run", "--brief", BID, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Cost ESTIMATE" in out and "This run (discovery, relevance, shortlist" in out
    assert "relevance filter:" in out and "claude-haiku-4-5" in out and "DRY RUN" in out
    assert main(["run", "--brief", BID, "--dry-run", "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["dry_run"] == {
        "would": "new",
        "run": None,
        "stages": ["discovery", "relevance", "shortlist", "selection"],
        "incremental": False,
        "network_calls": 0,
        "writes": 0,
    }
    assert d["run_scope"]["requires_approval"] is True and d["run_scope"]["api_usd"] > 0
    assert d["run_scope"]["llm"]["mode"] == "batch"
    # the selection stage (ADR-077) makes no model call and runs only on a final shortlist
    assert d["run_scope"]["selection"]["llm_calls"] == 0
    assert d["run_scope"]["selection"]["runs_only_when"] == (
        "the shortlist is final and the brief version is pre-registered"
    )
    assert n_runs(db) == 0
    # paid steps (api backend) without approval: nothing starts
    assert main(["run", "--brief", BID]) == 3
    assert "--approve-paid" in capsys.readouterr().err and n_runs(db) == 0
    # approved, but no GitHub token for discovery: a usage error, recorded as failed
    assert main(["run", "--brief", BID, "--approve-paid", "--stage", "discovery"]) == 2


def test_r4_7_shortlist_cli(cli_env, capsys):
    db = cli_env
    from pigtail.config import Settings

    brief = BriefStore.from_settings(Settings.from_env()).get(BID).brief
    st = CandidateStore(db.conn, BID, 1)
    for name, verdict in (
        ("org-s/lint-a", "relevant"),
        ("org-s/lint-b", "relevant"),
        ("org-s/maybe-c", "uncertain"),
    ):
        st.upsert(
            Candidate(
                ref=f"gh:{name}",
                repo_full_name=name,
                sources=[{"source": "github_topic", "term": "yaml"}],
            ),
            brief_run_id=None,
            now=NOW,
        )
        st.set_verdict(
            f"gh:{name}",
            verdict=verdict,
            reason="synthetic",
            distance=0,  # type: ignore[arg-type]
            model_panel="field",
            rubric_version="rubric-v1-t",
            provenance={},
            judged_at=NOW,
            brief_run_id=None,
        )
    capsys.readouterr()
    assert main(["brief", "shortlist", "show", BID]) == 0
    assert "not started" in capsys.readouterr().out
    Shortlist(db.conn, brief).ensure(None)
    capsys.readouterr()
    assert main(["brief", "shortlist", "show", BID, "--json", "--verdict", "relevant"]) == 0
    v = json.loads(capsys.readouterr().out)
    assert [c["candidate_ref"] for c in v["candidates"]] == ["gh:org-s/lint-a", "gh:org-s/lint-b"]
    assert (
        main(
            [
                "brief",
                "shortlist",
                "accept",
                BID,
                "org-s/lint-a",
                "--reason",
                "fits",
                "--as",
                "owner",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "brief",
                "shortlist",
                "reject",
                BID,
                "--verdict",
                "uncertain",
                "--reason",
                "unclear",
                "--as",
                "owner",
            ]
        )
        == 0
    )
    assert main(["brief", "shortlist", "finalize", BID]) == 1  # lint-b undecided
    assert "undecided" in capsys.readouterr().err
    assert (
        main(
            [
                "brief",
                "shortlist",
                "reject",
                BID,
                "gh:org-s/lint-b",
                "--reason",
                "no",
                "--as",
                "owner",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "brief",
                "shortlist",
                "add",
                BID,
                "https://github.com/org-y/extra",
                "--reason",
                "missed",
                "--as",
                "owner",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["brief", "shortlist", "finalize", BID, "--as", "owner"]) == 0
    out = capsys.readouterr().out
    assert "final: 2 repos" in out and "50 %" in out and "BELOW TARGET" in out
    assert main(["brief", "shortlist", "accept", BID, "org-s/lint-b", "--reason", "x"]) == 1
    roles = {r[0] for r in db.conn.execute("SELECT DISTINCT reviewer_role FROM shortlist_decision")}
    assert roles == {"owner"}


def test_r4_8_selection_show_cli(cli_env, capsys, tmp_path):
    """`pigtail brief selection show` (ADR-077): nothing before the selection stage ran; then the
    stored summary, steps, balance and sensitivity. Two synthetic repos without star history:
    every case is `no_anchor` and the report says there are fewer winners than the minimum."""
    db = cli_env
    from pigtail.briefs.selection_store import run_stage
    from pigtail.config import Settings

    brief = BriefStore.from_settings(Settings.from_env()).get(BID).brief
    capsys.readouterr()
    assert main(["brief", "selection", "show", BID]) == 1
    assert "no selection" in capsys.readouterr().out
    st = CandidateStore(db.conn, BID, 1)
    for name in ("org-s/lint-a", "org-s/lint-b"):
        st.upsert(Candidate(ref=f"gh:{name}", repo_full_name=name), brief_run_id=None, now=NOW)
    sl = Shortlist(db.conn, brief)
    sl.ensure(None)
    sl.decide(["org-s/lint-a", "org-s/lint-b"], "accept", "synthetic", reviewer="owner")
    sl.finalize(reviewer="owner")
    f = tmp_path / "prereg.md"
    f.write_text("# Pre-registration (synthetic test)\nNo brief content.\n")
    assert main(["brief", "preregister", BID, "--file", str(f)]) == 0
    from tests.discovery_fake import FakeShowHN
    from tests.integration.test_launch_lookup_m22 import hn_connector

    run_stage(
        db.conn,
        brief,
        brief_run_id=None,
        github=None,
        checkpoint={},
        save_checkpoint=lambda _c: None,
        run_date=NOW.date(),
        clock=lambda: NOW,
        hn=hn_connector(db, FakeShowHN([]), tmp_path),  # the launch lookup finds nothing
    )
    capsys.readouterr()
    assert main(["brief", "selection", "show", BID]) == 0
    out = capsys.readouterr().out
    assert "Selection sel_" in out and "no_anchor 2" in out and "baseline" in out
    assert "fewer winners than the minimum (15)" in out and "Sensitivity (R4.9)" in out
    assert "org-s/lint-a" in out and "unfiltered, anomaly-checked" in out
    assert main(["brief", "selection", "show", BID, "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["selection"]["summary"]["roles"] == {"no_anchor": 2} and len(d["cases"]) == 2


def test_adr_082_cli_warns_when_the_show_hn_connector_is_off(cli_env, capsys, monkeypatch):
    """`brief preregister --print-hashes`, `brief estimate` and `run --dry-run` warn that the
    selection will be refused without the Show HN connector (its launch lookup)."""
    capsys.readouterr()
    monkeypatch.delenv("PIGTAIL_CONNECTOR_HN_SHOWHN_ENABLED", raising=False)
    assert main(["brief", "preregister", BID, "--print-hashes"]) == 0
    assert "PIGTAIL_CONNECTOR_HN_SHOWHN_ENABLED" not in capsys.readouterr().err
    monkeypatch.setenv("PIGTAIL_CONNECTOR_HN_SHOWHN_ENABLED", "false")
    assert main(["brief", "preregister", BID, "--print-hashes"]) == 0
    assert "PIGTAIL_CONNECTOR_HN_SHOWHN_ENABLED=false" in capsys.readouterr().err
    main(["brief", "estimate", BID])
    assert "refuse the selection (exit 8)" in capsys.readouterr().out
    assert main(["run", "--brief", BID, "--dry-run", "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["estimate"]["warnings"] and d["run_scope"]["selection"]["warnings"]
    assert d["estimate"]["other_requests"]["hn_launch_lookup"] > 0  # the selection is pending
