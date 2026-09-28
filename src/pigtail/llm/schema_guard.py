"""A size and complexity guard for the JSON Schemas pigtail sends as structured output (ADR-086
addendum 1).

The API compiles an `output_config.format` JSON Schema into a grammar and refuses a schema whose
grammar is too large (HTTP 400 `invalid_request_error`: "The compiled grammar is too large, which
would cause performance issues"). The limit is not published, so the guard is calibrated on two
observed schemas: the pilot's nested coder schema 1.0.0 (7.8 KB, refused) and the adjudicator's
(1.5 KB, accepted). It measures the schema **with every `$ref` inlined** (what a grammar has to
expand: the nested coder schema reused one `$def` 12 times) and bounds its JSON size, its enum
values, its `anyOf`/`oneOf` branches, its objects and their properties. The refused schema is
over every bound, the accepted one well under all of them. The live contract check
(`tests/smoke/test_llm_schema_contract_smoke.py`) is the ground truth; this guard catches a
regression in a unit test, without a network call.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from pydantic import BaseModel

from pigtail.llm.types import schema_of


@dataclass(frozen=True)
class SchemaComplexity:
    json_bytes: int  # compact JSON of the schema with every $ref inlined
    enum_values: int
    branches: int  # anyOf / oneOf alternatives (a nullable field is one anyOf of 2)
    objects: int
    properties: int

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


# Calibrated (see the module docstring): the refused coder schema 1.0.0 measured 25,464 bytes,
# 329 enum values, 50 branches, 52 objects, 196 properties; the accepted adjudicator schema
# 1,292 bytes, 10 enum values, 2 branches, 3 objects, 10 properties.
LIMITS = SchemaComplexity(json_bytes=4_000, enum_values=60, branches=8, objects=8, properties=30)


def _inline(node: Any, defs: dict[str, Any], depth: int = 0) -> Any:
    if depth > 32:
        raise ValueError("schema nesting too deep (recursive $ref?)")
    if isinstance(node, dict):
        if "$ref" in node:
            return _inline(defs[str(node["$ref"]).rsplit("/", 1)[-1]], defs, depth + 1)
        return {k: _inline(v, defs, depth + 1) for k, v in node.items() if k != "$defs"}
    if isinstance(node, list):
        return [_inline(v, defs, depth + 1) for v in node]
    return node


def _walk(node: Any, acc: dict[str, int]) -> None:
    if isinstance(node, dict):
        if isinstance(node.get("enum"), list):
            acc["enum_values"] += len(node["enum"])
        for k in ("anyOf", "oneOf"):
            if isinstance(node.get(k), list):
                acc["branches"] += len(node[k])
        if node.get("type") == "object" and isinstance(node.get("properties"), dict):
            acc["objects"] += 1
            acc["properties"] += len(node["properties"])
        for v in node.values():
            _walk(v, acc)
    elif isinstance(node, list):
        for v in node:
            _walk(v, acc)


def complexity(schema: dict[str, Any] | type[BaseModel]) -> SchemaComplexity:
    """The complexity of a JSON Schema (or of a pydantic model's structured-output schema)."""
    s = schema_of(schema) if isinstance(schema, type) else schema
    flat = _inline(s, dict(s.get("$defs") or {}))
    acc = {"enum_values": 0, "branches": 0, "objects": 0, "properties": 0}
    _walk(flat, acc)
    return SchemaComplexity(len(json.dumps(flat, separators=(",", ":"))), **acc)


def over_limits(
    schema: dict[str, Any] | type[BaseModel], limits: SchemaComplexity = LIMITS
) -> dict[str, tuple[int, int]]:
    """Every measure above its limit: name -> (measured, limit). Empty when the schema fits."""
    got = complexity(schema).to_dict()
    lim = limits.to_dict()
    return {k: (v, lim[k]) for k, v in got.items() if v > lim[k]}
