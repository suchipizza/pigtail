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
    monkeypatch.setenv("PIGTAIL_BRIEFS_DIR", str(d / "briefs"))
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
    # M12 follow-up (M21b): a file without `version:` needs --force-latest
    assert main([*edit, write(tmp_path, "e.yaml", text)]) == 1
    assert "--force-latest" in capsys.readouterr().err
    assert main([*edit, write(tmp_path, "e.yaml", text), "--force-latest"]) == 0
    assert "v2" in capsys.readouterr().out
    assert main([*edit, write(tmp_path, "e.yaml", text), "--force-latest"]) == 0
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


def test_r18_5_estimate_free_brief_exits_zero(capsys, tmp_path):
    free = EXAMPLE.read_text().replace("llm_backend: api", "llm_backend: subscription")
    main(["brief", "new", "--from", write(tmp_path, "free.yaml", free)])
    capsys.readouterr()
    assert main(["brief", "estimate", "example-config-linter", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["label"] == "estimate"
    assert out["money"]["usd"] == 0.0 and out["money"]["requires_approval"] is False
    assert "agents" in out["llm"]["subscription_note"]


def test_adr_053_estimate_with_paid_step_requires_approval_flag(capsys, tmp_path):
    paid = EXAMPLE.read_text().replace("x: false", "x: true")
    main(["brief", "new", "--from", write(tmp_path, "p.yaml", paid)])
    assert main(["brief", "estimate", "example-config-linter"]) == 3
    captured = capsys.readouterr()
    assert "--approve-paid" in captured.err and "Nothing was started" in captured.err
    assert main(["brief", "estimate", "example-config-linter", "--approve-paid"]) == 0


def test_r15_11_cli_estimate_per_stage_usd_against_the_caps(capsys, monkeypatch):
    monkeypatch.setenv("BUDGET_USD_MONTH", "200")
    main(["brief", "new", "--example", "--id", "synthetic-demo"])
    capsys.readouterr()
    assert main(["brief", "estimate", "synthetic-demo", "--json"]) == 3  # api: needs approval
    out = json.loads(capsys.readouterr().out)
    assert out["model"] == "estimate-v2"
    assert out["caps"]["brief"]["cap_usd"] == 150.0 and out["caps"]["month"]["cap_usd"] == 200.0
    models = {s["stage"]: s["model"] for s in out["llm"]["stages"] if s["llm_calls"]}
    assert models["relevance"] == "claude-haiku-4-5-20251001"
    assert models["extraction"] == "claude-sonnet-5" and models["plan"] == "claude-opus-5-5"


def test_r15_11_cli_estimate_over_the_cap_refuses_approval_h6(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("BUDGET_USD_MONTH", "0.01")  # H6: nothing above the monthly cap
    main(["brief", "new", "--example", "--id", "synthetic-demo"])
    capsys.readouterr()
    assert main(["brief", "estimate", "synthetic-demo", "--approve-paid"]) == 4
    err = capsys.readouterr().err
    assert "H6" in err and "approval not recorded" in err


def test_r15_8_llm_models_per_stage_from_env(capsys, monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "claude-opus-5")  # fallback only
    monkeypatch.setenv("LLM_MODEL_RELEVANCE", "claude-haiku-4-5-20251001")
    main(["brief", "new", "--example", "--id", "synthetic-demo"])
    capsys.readouterr()
    main(["brief", "estimate", "synthetic-demo", "--json"])
    out = json.loads(capsys.readouterr().out)
    per = out["llm"]["per_llm_stage"]
    assert per["relevance"]["model"] == "claude-haiku-4-5-20251001"
    assert per["extraction"]["model"] == per["synthesis"]["model"] == "claude-opus-5"


def test_schema_command_prints_the_committed_schema(capsys):
    assert main(["brief", "schema"]) == 0
    committed = json.loads((EXAMPLE.parents[2] / "schemas" / "brief" / "v1.2.json").read_text())
    assert json.loads(capsys.readouterr().out) == committed


def test_new_requires_one_source(capsys):
    assert main(["brief", "new"]) == 2
    assert main(["brief", "new", "--example"]) == 2


# --- M21b: briefs outside git (ADR-071.3, R18.9) ------------------------------------------------
def test_adr_071_3_default_briefs_dir_is_home_not_the_data_dir(tmp_path, monkeypatch):
    import pigtail.config as config
    from pigtail.config import Settings

    monkeypatch.setattr(config, "DEFAULT_BRIEFS_DIR", "~/.pigtail/briefs")  # the real default
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    s = Settings.from_env({"PIGTAIL_DATA_DIR": str(tmp_path / "data")})
    assert s.briefs_dir == tmp_path / "home" / ".pigtail" / "briefs"
    assert s.briefs_dir_source == "default"
    s = Settings.from_env({"PIGTAIL_BRIEFS_DIR": "~/elsewhere/briefs"})
    assert s.briefs_dir == tmp_path / "home" / "elsewhere" / "briefs"  # ~ is expanded
    assert s.briefs_dir_source == "env"
    # the old location only when explicitly configured
    s = Settings.from_env({"PIGTAIL_DATA_DIR": "d", "PIGTAIL_BRIEFS_IN_DATA_DIR": "1"})
    assert s.briefs_dir == Path("d") / "briefs" and s.briefs_dir_source == "data_dir"


def test_adr_071_3_migrate_store_moves_briefs_and_prints_counts_only(capsys, tmp_path, data_dir):
    old = tmp_path / "old-store"
    # a brief created in the old location (explicit opt-in)
    import os

    os.environ["PIGTAIL_BRIEFS_DIR"] = str(old)
    try:
        assert main(["brief", "new", "--example", "--id", "synthetic-moved"]) == 0
        edited = EXAMPLE.read_text().replace("months: 18", "months: 12")
        edited = edited.replace("brief_id: example-config-linter", "brief_id: synthetic-moved")
        e = write(tmp_path, "e.yaml", edited)
        assert main(["brief", "edit", "synthetic-moved", "--from", e, "--force-latest"]) == 0
    finally:
        os.environ["PIGTAIL_BRIEFS_DIR"] = str(data_dir / "briefs")
    (old / "notes.txt").write_text("synthetic note, not a brief")
    (old / ".tmp-draft.yaml").write_text("temp file")
    capsys.readouterr()
    assert main(["brief", "list"]) == 0
    captured = capsys.readouterr()
    assert "no briefs yet" in captured.out
    assert main(["brief", "migrate-store", "--from", str(old), "--dry-run"]) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["moved"] == 2 and rep["dry_run"] and (old / "synthetic-moved").is_dir()
    assert main(["brief", "migrate-store", "--from", str(old)]) == 0
    out = capsys.readouterr().out
    rep = json.loads(out)
    counts = ("briefs", "moved", "other_files_moved", "conflicts", "left_behind")
    assert tuple(rep[k] for k in counts) == (1, 2, 1, 0, 1)
    assert "synthetic-moved" not in out and "Example Config Linter" not in out  # counts only
    new = data_dir / "briefs" / "synthetic-moved"
    assert sorted(p.name for p in new.iterdir()) == ["v0001.yaml", "v0002.yaml"]
    assert oct(new.stat().st_mode & 0o777) == "0o700"
    assert oct((new / "v0001.yaml").stat().st_mode & 0o777) == "0o600"
    assert not (old / "synthetic-moved").exists() and not (old / "notes.txt").exists()
    assert (data_dir / "briefs" / "notes.txt").read_text() == "synthetic note, not a brief"
    assert (old / ".tmp-draft.yaml").exists()  # hidden temp files are left in place
    assert main(["brief", "versions", "synthetic-moved", "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 2
    # idempotent: nothing left to move
    assert main(["brief", "migrate-store", "--from", str(old)]) == 0
    assert json.loads(capsys.readouterr().out)["moved"] == 0


def test_adr_071_3_migrate_store_keeps_both_copies_on_conflict(capsys, tmp_path, data_dir):
    from pigtail.briefs.store import migrate_store

    old, new = tmp_path / "old", tmp_path / "new"
    for root, text in ((old, "a"), (new, "b")):
        (root / "synthetic-x").mkdir(parents=True)
        (root / "synthetic-x" / "v0001.yaml").write_text(text)
    (old / "synthetic-x" / "v0002.yaml").write_text("c")
    rep = migrate_store(old, new)
    assert (rep.moved, rep.conflicts) == (1, 1)
    assert (old / "synthetic-x" / "v0001.yaml").read_text() == "a"  # kept
    assert (new / "synthetic-x" / "v0001.yaml").read_text() == "b"  # never overwritten
    assert main(["brief", "migrate-store", "--from", str(old), "--to", str(new)]) == 1
    assert "differ" in capsys.readouterr().err
    with pytest.raises(ValueError):
        migrate_store(old, old)
