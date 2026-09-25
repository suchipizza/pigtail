"""Tests for the subscription backend (R15.2, R15.5). CLI outputs below are synthetic fixtures
shaped like `claude -p --output-format json`."""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from pigtail.llm import UsageLimitReached
from pigtail.llm.errors import BackendError
from pigtail.llm.subscription import SubscriptionBackend, parse_reset, subprocess_env

OK = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": '{"n": 2}',
    "structured_output": {"n": 2},
    "total_cost_usd": 0.01,
    "session_id": "s-1",
    "num_turns": 1,
    "usage": {"input_tokens": 10, "output_tokens": 4},
    "modelUsage": {"claude-opus-5": {"inputTokens": 10}},
}


def test_r15_2_api_key_stripped_from_subprocess_env():
    env = subprocess_env(
        {
            "ANTHROPIC_API_KEY": "x",
            "ANTHROPIC_AUTH_TOKEN": "y",
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "PATH": "/bin",
        }
    )
    assert env == {"PATH": "/bin"}


def test_r15_2_command_uses_official_cli_without_bare():
    cmd = SubscriptionBackend().command(system="s", json_schema={"type": "object"}, model="m")
    assert cmd[:2] == ["claude", "-p"]
    assert "--bare" not in cmd and "--json-schema" in cmd and "--output-format" in cmd


def test_parse_structured_output():
    r = SubscriptionBackend.parse(json.dumps(OK), "", 0, "claude-opus-5")
    assert r.data == {"n": 2} and r.input_tokens == 10 and r.model == "claude-opus-5"


def test_parse_falls_back_to_result_text():
    payload = {**OK, "structured_output": None}
    assert SubscriptionBackend.parse(json.dumps(payload), "", 0, "m").data == {"n": 2}


@pytest.mark.parametrize(
    "result",
    ["Claude AI usage limit reached|1893456000", "You've hit your limit · resets at 3pm"],
)
def test_r15_5_limit_detected(result):
    payload = {"type": "result", "is_error": True, "result": result}
    with pytest.raises(UsageLimitReached):
        SubscriptionBackend.parse(json.dumps(payload), "", 1, "m")


def test_parse_reset_epoch():
    reset = parse_reset("usage limit reached|1893456000")
    assert reset is not None and reset.year == 2030


def test_other_errors_are_backend_errors():
    with pytest.raises(BackendError):
        SubscriptionBackend.parse("not json", "boom", 1, "m")


def test_complete_passes_prompt_on_stdin_with_clean_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-not-leak")
    seen: dict[str, Any] = {}

    def runner(cmd, **kw):
        seen.update(kw, cmd=cmd)
        return subprocess.CompletedProcess(cmd, 0, json.dumps(OK), "")

    SubscriptionBackend(runner=runner).complete(
        system="s", prompt="hello", json_schema={}, model="m"
    )
    assert seen["input"] == "hello"
    assert "ANTHROPIC_API_KEY" not in seen["env"]
    assert "hello" not in seen["cmd"]


def test_missing_cli_is_backend_error():
    def runner(cmd, **kw):
        raise FileNotFoundError

    with pytest.raises(BackendError):
        SubscriptionBackend(runner=runner).complete(system="", prompt="", json_schema={}, model="m")
