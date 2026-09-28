"""M23 CLI: `pigtail brief pilot` shows the estimate first, `--dry-run` does nothing, paid steps
need `--approve-paid`; `pilot-summary` prints counts only; `decay` aggregates; the next
`brief estimate` prices the coding stages with the pilot's measured cost model. Synthetic example
brief, fake LLM backend, no GitHub token, no network."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import psycopg
import pytest

from pigtail.briefs.store import BriefStore
from pigtail.cli import main
from pigtail.config import Settings
from pigtail.llm.batch import MemoryBatchStore, PgCostLedger
from pigtail.llm.client import LLMClient
from pigtail.llm.redact import alias_redact
from pigtail.llm.store import LLMStore
from tests.forensics_fake import CodingBatchBackend, seed_selection

pytestmark = pytest.mark.db
BID = "example-config-linter"


@pytest.fixture
def cli_env(capture_db: Any, pg_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("PIGTAIL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LLM_BACKEND", "api")
    monkeypatch.setenv("PIGTAIL_CONNECTOR_PROJECT_PAGE_ENABLED", "false")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert main(["brief", "new", "--example", "--id", BID]) == 0
    brief = BriefStore.from_settings(Settings.from_env()).get(BID).brief
    seed_selection(capture_db.conn, brief)
    backend = CodingBatchBackend()
    store = LLMStore(tmp_path / "llm.sqlite3")
    batches = MemoryBatchStore()
    ledger_conn = psycopg.connect(pg_url, autocommit=True)

    def client() -> LLMClient:
        return LLMClient(
            backends={"api": backend}, default_backend="api", store=store,
            models={"extraction": "claude-sonnet-5", "synthesis": "claude-opus-5-5",
                    "relevance": "claude-haiku-4-5-20251001"},
            redactor=alias_redact, batch_store=batches, cost_sink=PgCostLedger(ledger_conn),
        )  # fmt: skip

    monkeypatch.setattr("pigtail.briefs.cli._llm_client", client)
    yield capture_db, backend, tmp_path
    ledger_conn.close()


def n(db: Any, table: str) -> int:
    return int(db.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


def test_m23_cli_pilot_estimate_dry_run_approval_run_and_summary(cli_env, capsys):
    db, backend, tmp = cli_env
    capsys.readouterr()
    assert main(["brief", "pilot", BID, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Pilot estimate (planning cost model" in out and "DRY RUN" in out
    assert "Full brief (11 cases)" in out
    assert main(["brief", "pilot", BID, "--dry-run", "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["dry_run"] == {"network_calls": 0, "writes": 0} and len(d["cases"]) == 5
    assert d["estimate"]["requires_approval"] and d["estimate"]["total_usd"] > 0
    assert d["estimate"]["llm_calls"] == {"coder_a": 5, "coder_b": 5, "adjudication": 5}
    assert n(db, "brief_pilot") == 0 and not backend.submitted
    # paid steps without approval: the estimate is shown, nothing starts
    assert main(["brief", "pilot", BID]) == 3
    captured = capsys.readouterr()
    assert "Pilot estimate" in captured.out and "--approve-paid" in captured.err
    assert n(db, "brief_pilot") == 0
    # approved: runs (no GitHub token: those items are gaps)
    assert main(["brief", "pilot", BID, "--approve-paid", "--json"]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["status"] == "succeeded" and res["report_paths"]
    assert Path(res["report_paths"]["json"]).is_relative_to(tmp / "data")
    gaps = {
        r[0]
        for r in db.conn.execute(
            "SELECT reason FROM brief_case_gap WHERE source = 'readme_current'"
        ).fetchall()
    }
    assert gaps == {"no_github_token"}
    assert len(backend.submitted) == 3
    # counts-only lines for the ops files
    assert main(["brief", "pilot-summary", BID, "--label", "brief 1", "--json"]) == 0
    lines = json.loads(capsys.readouterr().out)
    text = " ".join(lines.values())
    assert set(lines) == {"COSTS.md", "STATUS.md"} and "pilot brief 1: 5 cases" in text
    for bad in ("org-p", "gh:", BID, "cod_", "sel_"):
        assert bad not in text
    # decay: nothing with a URL without GitHub; the command still reports
    assert main(["brief", "decay", BID, "--due", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["run"]["checked"] == 0
    assert main(["brief", "decay", "--all", "--json"]) == 0
    capsys.readouterr()
    assert main(["brief", "decay"]) == 2  # neither an id nor --all
    # the next estimate prices the coding stages with the measured per-case model
    assert main(["brief", "estimate", BID, "--json"]) == 3  # paid steps, not approved
    est = json.loads(capsys.readouterr().out)
    assert est["model"] == "estimate-v12"
    assert est["coding_cost_model"]["source"] == "measured"
    assert est["coding_cost_model"]["n_cases"] == 5


def test_m23_cli_pilot_cost_rebuild_and_annotate(cli_env, capsys):
    """ADR-086 addendum 3: `pilot-cost --rebuild` re-stores the model from the ledger (no call),
    writes a private cost report and prints what it used and left out; `pilot-annotate`
    appends a note to the pilot's provenance."""
    db, backend, tmp = cli_env
    assert main(["brief", "pilot", BID, "--approve-paid", "--json"]) == 0
    capsys.readouterr()
    submitted = len(backend.submitted)
    models = n(db, "brief_case_cost_model")
    assert main(["brief", "pilot-cost", BID, "--rebuild", "--dry-run", "--json"]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["dry_run"] and not dry["stored"] and n(db, "brief_case_cost_model") == models
    assert main(["brief", "pilot-cost", BID, "--rebuild", "--json"]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["stored"] and res["cost_model"]["model_version"] == "case-cost-v3"
    assert res["projection"]["projected_total_with_contingency_usd"] is not None
    assert Path(res["report_paths"]["json"]).is_relative_to(tmp / "data")
    assert n(db, "brief_case_cost_model") == models + 1
    assert len(backend.submitted) == submitted  # nothing was called
    assert main(["brief", "pilot-cost", BID, "--rebuild"]) == 0
    text = capsys.readouterr().out
    assert "ledger rows used" in text and "contingency" in text
    assert main(["brief", "pilot-cost", BID]) == 0
    assert "case-cost-v3" in capsys.readouterr().out
    assert main(["brief", "pilot-cost", BID, "--rebuild", "--superseded-before", "nope"]) == 2
    capsys.readouterr()
    assert main(["brief", "pilot-annotate", BID, "--note", "codings made at 8f441b3's code",
                 "--commit", "8f441b3", "--step", "double_coding", "--json"]) == 0  # fmt: skip
    ann = json.loads(capsys.readouterr().out)
    assert ann["annotation"]["commit"] == "8f441b3" and len(ann["annotations"]) == 1
    assert ann["invocations"] and ann["invocations"][0]["kind"] == "create"
    assert main(["brief", "pilot-annotate", BID, "--note", " "]) == 2
    assert main(["brief", "pilot-annotate", "no-such-brief", "--note", "x"]) == 1
