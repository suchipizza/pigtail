"""M1-T24 privacy and gating invariants (TM-33; CB-22, CB-23; ADR-022/ADR-036; schema v0).

- No function, CLI command or API path lists the stargazers of a repo: since M21a (Directive
  §8.1, migration 0017) nothing about individual actors is stored at all (`repo_event_actor` is
  gone; counts only), and no SQL in the code base names an actor column.
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


def _sql_constants(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and re.search(
            r"\bSELECT\b[\s\S]*\bFROM\b|\bINSERT INTO\b|\bUPDATE \w+ SET\b|\bDELETE FROM\b", n.value
        )
    ]


def test_m21a_cb23_no_sql_touches_actor_rows():
    """Directive §8.1 / ADR-071.2: no SQL anywhere reads or writes per-actor event rows."""
    for path in SRC.rglob("*.py"):
        for q in _sql_constants(path):
            assert "repo_event_actor" not in q, path
            assert not re.search(r"actor_pseudonym|author_pseudonym|_actor_token", q), path
    from pigtail.export.jsonl import TABLE_LEVELS

    assert "repo_event_actor" not in TABLE_LEVELS
    api = "\n".join(p.read_text() for p in (SRC / "api").rglob("*.py"))
    assert "repo_event_actor" not in api and "stargazers" not in api


def test_m21a_cb23_repo_events_poller_writes_counts_only():
    writes = [
        q for q in _sql_constants(SRC / "capture" / "repo_events.py") if "INSERT" in q.upper()
    ]
    assert writes
    for q in writes:
        cols = re.search(r"INSERT INTO (\w+) \((.*?)\)", q, re.DOTALL)
        assert cols is not None, q
        assert cols.group(1) in {"repo_event_poll", "repo_event_hourly_agg", "repo_event_daily_agg"}
        assert not re.search(r"actor|login|token|author", cols.group(2)), q


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
