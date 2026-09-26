"""M12 `pigtail brief new|edit|show|list|versions|diff|validate|estimate|schema` (R18.1–R18.5).

Runs against a throwaway PIGTAIL_DATA_DIR with synthetic briefs; no database.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pigtail.cli import main

EXAMPLE = Path(__file__).resolve().parents[2] / "docs" / "examples" / "brief-example.yaml"


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "data"
    monkeypatch.setenv("PIGTAIL_DATA_DIR", str(d))
    monkeypatch.setenv("PIGTAIL_SUBSCRIPTION_WEEKLY_TOKENS", "20000000")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    return d


def write(tmp_path: Path, name: str, text: str) -> str:
    p = tmp_path / name
    p.write_text(text)
    return str(p)


def test_r18_1_new_from_example_list_show(capsys, data_dir):
    assert main(["brief", "new", "--example", "--id", "synthetic-demo"]) == 0
    assert (data_dir / "briefs" / "synthetic-demo" / "v0001.yaml").is_file()
    assert main(["brief", "list"]) == 0
    assert "synthetic-demo" in capsys.readouterr().out
    assert main(["brief", "show", "synthetic-demo", "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["version"] == 1 and shown["brief_id"] == "synthetic-demo"
    assert main(["brief", "new", "--example", "--id", "synthetic-demo"]) == 1  # exists


def test_r18_4_edit_versions_diff(capsys, tmp_path):
    main(["brief", "new", "--from", str(EXAMPLE)])
    text = EXAMPLE.read_text().replace("months: 18", "months: 15")
    edit = ["brief", "edit", "example-config-linter", "--from"]
    assert main([*edit, write(tmp_path, "e.yaml", text)]) == 0
    assert "v2" in capsys.readouterr().out
    assert main([*edit, write(tmp_path, "e.yaml", text)]) == 0
    assert "no changes" in capsys.readouterr().out
    assert main(["brief", "versions", "example-config-linter", "--json"]) == 0
    assert [v["version"] for v in json.loads(capsys.readouterr().out)] == [1, 2]
    assert main(["brief", "diff", "example-config-linter"]) == 0
    assert "~ window.months: 18 -> 15" in capsys.readouterr().out
    assert main(["brief", "diff", "example-config-linter", "1", "2", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["changes"][0]["path"] == "window.months"
    # a stale base version is refused
    text3 = text.replace("months: 15", "months: 12")
    assert main([*edit, write(tmp_path, "f.yaml", text3), "--base-version", "1"]) == 1


def test_d7_validate_names_the_failing_field(capsys, tmp_path):
    bad = EXAMPLE.read_text().replace("winners: 20", "winners: 30")
    assert main(["brief", "validate", write(tmp_path, "bad.yaml", bad)]) == 1
    assert "panel.winners" in capsys.readouterr().err
    assert main(["brief", "validate", write(tmp_path, "bad.yaml", bad), "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["errors"][0]["path"] == "panel.winners"
    assert main(["brief", "validate", str(EXAMPLE)]) == 0
    assert main(["brief", "new", "--from", write(tmp_path, "bad.yaml", bad)]) == 1


def test_r18_5_estimate_free_brief_exits_zero(capsys):
    main(["brief", "new", "--example", "--id", "synthetic-demo"])
    capsys.readouterr()
    assert main(["brief", "estimate", "synthetic-demo", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["label"] == "estimate"
    assert out["money"]["usd"] == 0.0 and out["money"]["requires_approval"] is False
    assert out["llm"]["subscription"]["allowance"]["basis"] == "configured"


def test_adr_053_estimate_with_paid_step_requires_approval_flag(capsys, tmp_path):
    paid = EXAMPLE.read_text().replace("x: false", "x: true")
    paid = paid.replace("money_usd: 0 ", "money_usd: 5 ")
    main(["brief", "new", "--from", write(tmp_path, "p.yaml", paid)])
    assert main(["brief", "estimate", "example-config-linter"]) == 3
    captured = capsys.readouterr()
    assert "--approve-paid" in captured.err and "Nothing was started" in captured.err
    assert main(["brief", "estimate", "example-config-linter", "--approve-paid"]) == 0


def test_schema_command_prints_the_committed_schema(capsys):
    assert main(["brief", "schema"]) == 0
    committed = json.loads((EXAMPLE.parents[2] / "schemas" / "brief" / "v1.json").read_text())
    assert json.loads(capsys.readouterr().out) == committed


def test_new_requires_one_source(capsys):
    assert main(["brief", "new"]) == 2
    assert main(["brief", "new", "--example"]) == 2
