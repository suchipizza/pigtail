"""Tests for the api backend (R15.3) with a stubbed Anthropic client."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import pytest

from pigtail.llm import UsageLimitReached
from pigtail.llm.api import ApiBackend, estimate_cost
from pigtail.llm.errors import BackendError


class StubMessages:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.kwargs: dict[str, Any] = {}

    def create(self, **kw: Any) -> Any:
        self.kwargs = kw
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def backend(result: Any) -> tuple[ApiBackend, StubMessages]:
    msgs = StubMessages(result)
    return ApiBackend(client=SimpleNamespace(messages=msgs)), msgs  # type: ignore[arg-type]


def message(text: str, stop: str = "end_turn") -> Any:
    return SimpleNamespace(
        id="msg_1",
        model="claude-opus-5",
        stop_reason=stop,
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=1000, output_tokens=100),
    )


def test_r15_3_structured_output_request_and_parse():
    b, msgs = backend(message('{"n": 1}'))
    r = b.complete(system="s", prompt="p", json_schema={"type": "object"}, model="claude-opus-5")
    assert r.data == {"n": 1}
    assert msgs.kwargs["output_config"]["format"]["type"] == "json_schema"
    assert r.cost_usd == pytest.approx(estimate_cost("claude-opus-5", 1000, 100))


def test_refusal_is_backend_error():
    b, _ = backend(message("", stop="refusal"))
    with pytest.raises(BackendError):
        b.complete(system="s", prompt="p", json_schema={}, model="claude-opus-5")


def test_r15_5_rate_limit_maps_to_usage_limit():
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(429, headers={"retry-after": "30"}, request=req)
    err = anthropic.RateLimitError("rate limited", response=resp, body=None)
    b, _ = backend(err)
    with pytest.raises(UsageLimitReached) as ei:
        b.complete(system="s", prompt="p", json_schema={}, model="claude-opus-5")
    assert ei.value.reset_at is not None
