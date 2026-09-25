"""M1-T2 / PRD §7: RunRecorder writes a `run` record at start and end."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from pigtail.capture.models import Run
from pigtail.capture.runs import RunRecorder, git_commit, jsonl_sink

RUN_SCHEMA = json.loads(
    (Path(__file__).resolve().parents[2] / "schemas/v0/run.schema.json").read_text()
)


def test_prd7_run_recorded_start_and_success():
    seen: list[Run] = []
    with RunRecorder("capture.scan", {"a": 1}, sink=seen.append, code_commit="abcdef1") as r:
        r.incr("events", 3)
        r.incr("events")
        r.incr("cost_usd", 0.5)
    assert [x.status for x in seen] == ["running", "succeeded"]
    final = seen[-1]
    assert final.counts == {"events": 4, "cost_usd": 0.5}
    assert final.code_commit == "abcdef1"
    assert final.finished_at is not None and final.config == {"a": 1}
    assert final.prompt_versions is None and final.model_versions is None
    for run in seen:
        Draft202012Validator(RUN_SCHEMA).validate(run.to_json_dict())


def test_prd7_run_recorded_on_failure_and_reraises():
    seen: list[Run] = []
    with pytest.raises(RuntimeError), RunRecorder("job", sink=seen.append, detect_commit=False):
        raise RuntimeError("boom")
    assert seen[-1].status == "failed"
    assert seen[-1].error == "RuntimeError: boom"


def test_code_commit_from_env_or_git(monkeypatch):
    monkeypatch.setenv("PIGTAIL_CODE_COMMIT", "0123abcd")
    assert git_commit() == "0123abcd"
    monkeypatch.setenv("PIGTAIL_CODE_COMMIT", "not-a-sha")
    assert git_commit() is None
    monkeypatch.delenv("PIGTAIL_CODE_COMMIT")
    c = git_commit()
    assert c is None or len(c) == 40


def test_jsonl_sink(tmp_path):
    path = tmp_path / "runs.jsonl"
    with RunRecorder("job", sink=jsonl_sink(path), detect_commit=False):
        pass
    lines = [json.loads(x) for x in path.read_text().splitlines()]
    assert [x["status"] for x in lines] == ["running", "succeeded"]
