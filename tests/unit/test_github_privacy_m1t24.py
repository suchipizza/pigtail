"""M1-T24 privacy and gating invariants (TM-33; CB-22, CB-23; ADR-022/ADR-036; schema v0).

- No function, CLI command or API path lists the stargazers of a repo: `repo_event_actor` is
  mentioned only by the events poller and the person-table registry, and every query on it is an
  aggregate.
- Scheduler jobs skip with a logged reason until GITHUB_TOKEN is set; the events job is off.
- The detection-v1 block and its bot_filter object match the JSON Schema field for field.
"""

from __future__ import annotations

import argparse
import ast
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from pigtail.capture.models import BotFilterStatus, Case, DetectionV1
from pigtail.cli import build_parser
from pigtail.scheduler.config import load
from pigtail.scheduler.jobs import Planner, connector_gate

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "pigtail"


def _commands(p: argparse.ArgumentParser, prefix: str = "") -> set[str]:
    out: set[str] = set()
    for a in p._actions:
        if isinstance(a, argparse._SubParsersAction):
            for name, sp in a.choices.items():
                full = f"{prefix} {name}".strip()
                out.add(full)
                out |= _commands(sp, full)
    return out


def test_m1_t24_cb23_no_cli_command_lists_stargazers():
    cmds = _commands(build_parser())
    gh = {c for c in cmds if c.startswith("capture github")}
    assert gh == {
        "capture github",
        "capture github star-history",
        "capture github repo-events",
        "capture github budget",
    }  # M11: watch list, sweeps, screens, detect-v1 and settle-lag removed (ADR-047.6)
    assert not [c for c in cmds if re.search(r"stargazer|actor|who", c)]


def test_m1_t24_cb23_repo_event_actor_only_in_poller_and_registry():
    users = {
        str(p.relative_to(SRC))
        for p in SRC.rglob("*.py")
        if "repo_event_actor" in p.read_text(encoding="utf-8")
    }
    # export/jsonl.py only names it to classify it as never exported (M1-T20); the M11 data-cache
    # inventory only counts its rows, repos and days (never the actor column)
    assert users == {
        "capture/repo_events.py",
        "privacy/deletion.py",
        "export/jsonl.py",
        "capture/inventory.py",
    }
    assert "actor_pseudonym" not in (SRC / "capture" / "inventory.py").read_text()
    from pigtail.export.jsonl import TABLE_LEVELS

    assert TABLE_LEVELS["repo_event_actor"] == "never"
    assert (SRC / "export" / "jsonl.py").read_text().count("repo_event_actor") == 2
    api = "\n".join(p.read_text() for p in (SRC / "api").rglob("*.py"))
    assert "repo_event_actor" not in api and "stargazers" not in api


def test_m1_t24_cb23_repo_event_actor_is_read_only_in_aggregate():
    tree = ast.parse((SRC / "capture" / "repo_events.py").read_text())
    sqls = [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and "repo_event_actor" in n.value
    ]
    reads = [s for s in sqls if re.search(r"\bSELECT\b", s, re.IGNORECASE)]
    assert reads, "expected aggregate reads"
    for s in reads:
        # every SELECT touching the table returns counts / min only, never actor_pseudonym rows
        selected = re.findall(r"SELECT(.*?)FROM repo_event_actor", s, re.IGNORECASE | re.DOTALL)
        for cols in selected:
            stripped = re.sub(r"count\(DISTINCT actor_pseudonym\)", "", cols)
            assert "actor_pseudonym" not in stripped, s
            assert re.search(r"count\(|min\(", cols, re.IGNORECASE), s


def test_m1_t24_schedule_jobs_skip_without_token(caplog: pytest.LogCaptureFixture):
    cfg = load()
    gh = [j for j in cfg.jobs if j.name.startswith("gh_")]
    assert {j.name for j in gh} == {"gh_star_history", "gh_repo_events"}
    ev = cfg.job("gh_repo_events")
    assert ev.enabled is False  # person-level: off by default
    now = datetime(2026, 9, 25, tzinfo=UTC)
    with caplog.at_level(logging.INFO, logger="pigtail.scheduler"):
        for j in gh:
            assert Planner({}).plan(j, now).skip == "missing_env:GITHUB_TOKEN"
    assert "GITHUB_TOKEN is not set" in caplog.text
    env = {"GITHUB_TOKEN": "x"}
    assert Planner(env).plan(cfg.job("gh_star_history"), now).commands
    assert connector_gate(["github", "github_events"], env) == "connector_disabled"
    env_on = {**env, "PIGTAIL_ENABLE_GITHUB_EVENTS": "1"}
    assert connector_gate(["github", "github_events"], env_on) == "person_source_hold"
    ok = {**env_on, "PIGTAIL_ADR022_PERSON_SOURCES_OK": "1"}
    assert connector_gate(["github", "github_events"], ok) is None


def test_m1_t24_detection_v1_schema_matches_model():
    schema = json.loads((ROOT / "schemas" / "v0" / "case.schema.json").read_text())
    defs = schema["$defs"]
    for model, key in ((DetectionV1, "velocity_detection_v1"), (BotFilterStatus, "bot_filter")):
        assert set(model.model_fields) == set(defs[key]["properties"]), key
        assert set(defs[key]["required"]) == set(model.model_fields), key
    doc = json.loads((ROOT / "schemas" / "v0" / "examples" / "case.example.json").read_text())
    assert doc["detection"]["rule_version"] == "detection-v1"
    assert doc["detection"]["bot_filter"]["basis"] == "repo_events"
    case = Case.model_validate(doc)
    assert isinstance(case.detection, DetectionV1)
    v = Draft202012Validator(schema, format_checker=FormatChecker())
    assert not list(v.iter_errors(case.to_json_dict()))
    bad = json.loads(json.dumps(doc))
    bad["detection"]["bot_filter"]["status"] = "maybe"
    assert list(v.iter_errors(bad))
