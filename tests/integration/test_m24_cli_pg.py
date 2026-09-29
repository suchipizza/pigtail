"""M24 CLI: `pigtail brief code` shows the full-coding estimate first (with the contingency),
`--dry-run` does nothing, paid steps need `--approve-paid`, `--max-usd` stops before anything
(ADR-089). Synthetic example brief, fake LLM backend, no GitHub token, no network."""

from __future__ import annotations

import json
from typing import Any

import pytest

from pigtail.cli import main
from tests.integration.test_m23_cli_pg import BID, cli_env, n  # noqa: F401 (fixture)

pytestmark = pytest.mark.db


def test_m24_t1_cli_code_dry_run_approval_cap_and_run(
    cli_env: Any,  # noqa: F811
    capsys: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PIGTAIL_CONNECTOR_HN_SHOWHN_ENABLED", "false")  # no network
    db, backend, _tmp = cli_env
    capsys.readouterr()
    assert main(["brief", "code", BID, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Full coding estimate" in out and "11 in the full brief" in out and "DRY RUN" in out
    assert "Cases (full-cases-v1): 11" in out
    assert main(["brief", "code", BID, "--dry-run", "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert len(d["cases"]) == 11 and d["estimate"]["rule"] == "full-cases-v1"
    assert d["estimate"]["total_with_contingency_usd"] > d["estimate"]["total_usd"] > 0
    assert n(db, "brief_pilot") == 0 and not backend.submitted
    assert main(["brief", "code", BID]) == 3
    assert "--approve-paid" in capsys.readouterr().err
    assert main(["brief", "code", BID, "--approve-paid", "--max-usd", "0.001"]) == 4
    assert "--max-usd" in capsys.readouterr().err
    assert n(db, "brief_pilot") == 0 and not backend.submitted
    assert main(["brief", "code", BID, "--approve-paid", "--json"]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["status"] == "succeeded" and res["summary"]["cases"] == 11
    kind = db.conn.execute("SELECT kind FROM brief_runs").fetchall()
    assert kind == [("coding",)]
    gaps = db.conn.execute(
        "SELECT DISTINCT reason FROM brief_case_gap WHERE source = 'hn_stories'"
    ).fetchall()
    assert gaps == [("connector_off",)]
