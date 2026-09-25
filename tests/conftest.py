from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from pigtail.llm import LLMClient, PromptSpec
from pigtail.llm.store import LLMStore
from pigtail.llm.types import BackendResponse
from pigtail.pseudonymize import Pseudonymizer

TEST_KEY = "test-key-not-secret-0123456789"


class Echo(BaseModel):
    value: int
    label: str


class FakeBackend:
    """Scripted backend: each call pops the next item (a dict to return or an exception)."""

    def __init__(self, name: str, script: list[Any]) -> None:
        self.name = name
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    def complete(
        self, *, system: str, prompt: str, json_schema: dict[str, Any], model: str
    ) -> BackendResponse:
        self.calls.append(
            {"system": system, "prompt": prompt, "schema": json_schema, "model": model}
        )
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return BackendResponse(
            data=item, model=model, input_tokens=10, output_tokens=5, cost_usd=0.001
        )


@pytest.fixture
def prompt() -> PromptSpec:
    return PromptSpec(id="echo", version="1", system="sys", template="Input: {input}")


@pytest.fixture
def pz() -> Pseudonymizer:
    return Pseudonymizer(TEST_KEY)


def make_client(sub: list[Any], api: list[Any] | None = None, **kw: Any) -> LLMClient:
    backends = {"subscription": FakeBackend("subscription", sub)}
    if api is not None:
        backends["api"] = FakeBackend("api", api)
    return LLMClient(
        backends=backends,
        default_backend=kw.pop("default_backend", "subscription"),
        store=LLMStore(":memory:"),
        model="test-model",
        redactor=Pseudonymizer(TEST_KEY).strip_identifiers,
        **kw,
    )
