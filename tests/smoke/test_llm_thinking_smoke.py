"""ADR-087: live check that each structured job, sent with its real parameters (the job's model,
schema and thinking setting, the product's request shape from `build_params`), answers
`end_turn` without spending its output on thinking.

The second live pilot failure: `claude-sonnet-5` thinks by default, and a coder request spent
all 16,000 output tokens on a `thinking` block. With `thinking: {"type": "disabled"}` the same
request ended `end_turn`. One small request per job (a trivial prompt, `max_tokens` 4,000), a
few cents in all. On a model that can't disable thinking (claude-opus-5-5: the setting is sent
as low effort) thinking blocks are allowed, and only `end_turn` is asserted.

Runs only with PIGTAIL_RUN_SMOKE=1 and ANTHROPIC_API_KEY (read at import: the autouse conftest
fixtures may scrub the environment of each test). Nothing is stored.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from pigtail.config import stage_models
from pigtail.llm.api import build_params
from pigtail.llm.pricing import canonical_model
from pigtail.llm.stages import stage_for
from pigtail.llm.thinking import ALWAYS_THINKING, mode_for, synthesis_mode
from pigtail.llm.types import schema_of
from tests.llm_contract import product_schemas

# read at import (see the module docstring)
API_KEY = os.environ.get("ANTHROPIC_API_KEY") or None
MODELS = {str(k): v for k, v in stage_models(dict(os.environ)).items()}
SYNTHESIS = synthesis_mode(dict(os.environ))


@pytest.mark.smoke
@pytest.mark.skipif(os.environ.get("PIGTAIL_RUN_SMOKE") != "1", reason="set PIGTAIL_RUN_SMOKE=1")
@pytest.mark.skipif(API_KEY is None, reason="ANTHROPIC_API_KEY not set")
@pytest.mark.parametrize("name,job,schema", product_schemas(), ids=lambda x: str(x)[:20])
def test_job_answers_end_turn_without_thinking_live(name: str, job: str, schema: Any) -> None:
    import anthropic

    model = MODELS[stage_for(job)]
    params = build_params(
        system="Smoke check of a structured job. Answer with a short, valid JSON object; use "
        "empty lists and 'unknown' wherever the schema allows them.",
        prompt="There is no evidence to code. Reply with the smallest valid object.",
        json_schema=schema_of(schema),
        model=model,
        max_tokens=4000,
        thinking=mode_for(job, SYNTHESIS),
    )
    client = anthropic.Anthropic(api_key=API_KEY, max_retries=2)
    msg = client.messages.create(**params)
    kinds = [b.type for b in msg.content]
    assert msg.stop_reason == "end_turn", (name, model, msg.stop_reason, msg.usage)
    if canonical_model(model) not in ALWAYS_THINKING:
        assert "thinking" not in kinds and "redacted_thinking" not in kinds, (name, kinds)
