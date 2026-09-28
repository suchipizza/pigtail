"""ADR-086 addendum 1: live contract check of every structured-output schema the product sends
(coder, adjudicator, relevance, surface, title check, PH check, expansion) against the API.

The first live pilot's coder schema was refused with HTTP 400 ("The compiled grammar is too
large") on every call; the unit guard (`pigtail.llm.schema_guard`) is calibrated on that, and this
test is the ground truth. Each schema goes out once as `output_config.format` with the job's
model, the product's request shape (`build_params`: cached system block), a trivial prompt and
`max_tokens=1`: the request is compiled and accepted, and the answer is cut after one token
(`stop_reason` `max_tokens`). A few cents in all. Runs only with PIGTAIL_RUN_SMOKE=1 and
ANTHROPIC_API_KEY (read at import: the autouse conftest fixtures may scrub the environment of
each test). Nothing is stored.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from pigtail.config import stage_models
from pigtail.llm.api import build_params
from pigtail.llm.stages import stage_for
from pigtail.llm.types import schema_of
from tests.llm_contract import product_schemas

# read at import (see the module docstring)
API_KEY = os.environ.get("ANTHROPIC_API_KEY") or None
MODELS = {str(k): v for k, v in stage_models(dict(os.environ)).items()}


@pytest.mark.smoke
@pytest.mark.skipif(os.environ.get("PIGTAIL_RUN_SMOKE") != "1", reason="set PIGTAIL_RUN_SMOKE=1")
@pytest.mark.skipif(API_KEY is None, reason="ANTHROPIC_API_KEY not set")
@pytest.mark.parametrize("name,job,schema", product_schemas(), ids=lambda x: str(x)[:20])
def test_structured_output_schema_is_accepted_live(name: str, job: str, schema: Any) -> None:
    import anthropic

    model = MODELS[stage_for(job)]
    params = build_params(
        system="Contract check of a structured-output schema. Answer with the JSON object.",
        prompt="Reply with any valid object.",
        json_schema=schema_of(schema),
        model=model,
        max_tokens=1,
    )
    client = anthropic.Anthropic(api_key=API_KEY, max_retries=2)
    try:
        msg = client.messages.create(**params)
    except anthropic.BadRequestError as e:  # the failure this test exists for
        pytest.fail(f"{name} ({job}, {model}): HTTP 400 {e.message}")
    assert msg.stop_reason in ("max_tokens", "end_turn"), (name, msg.stop_reason)
