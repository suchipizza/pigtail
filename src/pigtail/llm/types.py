from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel


@dataclass(frozen=True)
class PromptSpec:
    """A versioned prompt (R7.4, R15.4). Bump `version` whenever `system` or `template` changes."""

    id: str
    version: str
    system: str
    template: str  # must contain "{input}"

    def render(self, input_text: str) -> str:
        return self.template.replace("{input}", input_text)

    @property
    def fingerprint(self) -> str:
        return sha256_text(self.system + "\x00" + self.template)[:12]


@dataclass(frozen=True)
class BackendResponse:
    """What a backend returns before schema validation."""

    data: dict[str, Any]
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0  # api: estimated from list prices; subscription: CLI-reported equivalent
    raw_meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMResult[T: BaseModel]:
    output: T
    backend: str
    model: str
    prompt_id: str
    prompt_version: str
    input_hash: str
    cached: bool
    created_at: datetime

    def provenance(self) -> dict[str, str]:
        """Version record stored alongside every coded output (R7.4)."""
        return {
            "backend": self.backend,
            "model": self.model,
            "prompt_id": self.prompt_id,
            "prompt_version": self.prompt_version,
            "input_hash": self.input_hash,
        }


class Backend(Protocol):
    name: str

    def complete(
        self, *, system: str, prompt: str, json_schema: dict[str, Any], model: str
    ) -> BackendResponse: ...


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _close_objects(node: Any) -> None:
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            node.setdefault("additionalProperties", False)
        for v in node.values():
            _close_objects(v)
    elif isinstance(node, list):
        for v in node:
            _close_objects(v)


def schema_of(model_cls: type[BaseModel]) -> dict[str, Any]:
    """JSON Schema for structured output; every object is closed (additionalProperties: false)."""
    schema = model_cls.model_json_schema()
    _close_objects(schema)
    return schema


def schema_hash(model_cls: type[BaseModel]) -> str:
    return sha256_text(json.dumps(schema_of(model_cls), sort_keys=True))[:12]
