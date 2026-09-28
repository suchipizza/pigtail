"""Thinking setting per job, and how it goes into a Messages request (ADR-087).

**Why.** `claude-sonnet-5` thinks by default (adaptive thinking when the `thinking` field is
left out). In the first live pilot (2026-09-28) a coder request of 9,083 input tokens spent all
16,000 output tokens (`max_tokens`) on a `thinking` block and returned no text; the same request
with `thinking: {"type": "disabled"}` ended `end_turn` after 4,941 output tokens with a valid
answer. Every product job returns structured output (`output_config.format`) that the prompts
fully specify, so thinking is off by default.

**The setting** (`JOB_THINKING`): `disabled` for every job, the selection's jobs (relevance,
title and Product Hunt checks, distribution surface), double coding, adjudication and brief
expansion included. The synthesis jobs (`SYNTHESIS_JOBS`: patterns, synthesis, report, plan,
asset drafting) are configurable with `LLM_THINKING_SYNTHESIS` (`disabled`, the default for
now, or `adaptive`); every other job is fixed in code.

**How a setting becomes request fields** (`request_fields`; the claude-api skill's per-model
thinking table, read 2026-09-28):

- claude-sonnet-5, claude-opus-5 and the 4.x models: `disabled` sends
  `thinking: {"type": "disabled"}`, `adaptive` sends `thinking: {"type": "adaptive"}`.
- claude-haiku-4-5: `disabled` sends `thinking: {"type": "disabled"}` (its default anyway: no
  thinking); `adaptive` is refused (Haiku 4.5 has only budgeted thinking).
- claude-opus-5-5, claude-fable-5(-1), claude-mythos-5(-1): thinking can't be disabled
  (`{"type": "disabled"}` is an HTTP 400 at every effort). `disabled` sends no `thinking` field
  and `output_config.effort: "low"`; `adaptive` sends no `thinking` field (always adaptive).

`effort` is not an alternative to disabling on the models that can disable: it lowers thinking
depth but thinking stays on (and `effort` is an error on Haiku 4.5). On Claude Opus 5, disabled
thinking is accepted only at effort `high` or below; pigtail sends no effort there (default
`high`). On the models that always think, low effort is the closest to "off" there is.

**Recorded** as a short label (`label`, e.g. `disabled`, `adaptive+effort:low`): in each output's
provenance, in the LLM result cache key (an output made under another setting is not served) and,
for the selection's jobs, in the pre-registered selection parameters (ADR-087).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, Literal, get_args

from pigtail.llm.pricing import canonical_model

ThinkingMode = Literal["disabled", "adaptive"]
THINKING_MODES: tuple[ThinkingMode, ...] = get_args(ThinkingMode)
THINKING_VERSION = "thinking-v1"
THINKING_ENV = "LLM_THINKING_SYNTHESIS"

# The jobs whose thinking is configurable (`LLM_THINKING_SYNTHESIS`); brief expansion runs in
# the synthesis stage but is a structured check of proposals, fixed to `disabled`.
SYNTHESIS_JOBS = frozenset({"patterns", "synthesis", "report", "plan", "asset_drafting"})

JOB_THINKING: dict[str, ThinkingMode] = {
    # relevance stage: the selection's jobs and the relevance filter
    "relevance": "disabled",
    "relevance_filter": "disabled",
    "smoke": "disabled",
    "title_match_check": "disabled",
    "distribution_surface": "disabled",
    "ph_match_check": "disabled",
    # extraction stage
    "extraction": "disabled",
    "tier2_extraction": "disabled",
    "coding": "disabled",
    "double_coding": "disabled",
    "adjudication": "disabled",
    # synthesis stage (SYNTHESIS_JOBS configurable; expansion fixed)
    "patterns": "disabled",
    "synthesis": "disabled",
    "report": "disabled",
    "plan": "disabled",
    "asset_drafting": "disabled",
    "brief_expansion": "disabled",
}
DEFAULT_MODE: ThinkingMode = "disabled"  # jobs without an entry (tests, ad-hoc calls)

# Models on which thinking is always on: `{"type": "disabled"}` is refused (HTTP 400).
ALWAYS_THINKING = frozenset(
    {
        "claude-opus-5-5",
        "claude-fable-5",
        "claude-fable-5-1",
        "claude-mythos-5",
        "claude-mythos-5-1",
    }
)
# Models without adaptive thinking (budgeted thinking only; `effort` is an error).
NO_ADAPTIVE = frozenset({"claude-haiku-4-5"})
ALWAYS_ON_EFFORT = "low"


def parse_mode(raw: str | None, name: str = THINKING_ENV) -> ThinkingMode:
    value = (raw or "").strip().lower() or DEFAULT_MODE
    for mode in THINKING_MODES:
        if value == mode:
            return mode
    raise ValueError(f"{name} must be one of {', '.join(THINKING_MODES)}, got {raw!r}")


def synthesis_mode(env: Mapping[str, str] | None = None) -> ThinkingMode:
    """`LLM_THINKING_SYNTHESIS` (default `disabled`)."""
    e = os.environ if env is None else env
    return parse_mode(e.get(THINKING_ENV))


def mode_for(job: str, synthesis: ThinkingMode = DEFAULT_MODE) -> ThinkingMode:
    """The thinking setting of `job` (`launch_<job>` is `<job>`'s)."""
    base = job.removeprefix("launch_")
    if base in SYNTHESIS_JOBS:
        return synthesis
    return JOB_THINKING.get(base, DEFAULT_MODE)


def request_fields(mode: ThinkingMode, model: str) -> dict[str, Any]:
    """The request fields `mode` means on `model`: `{"thinking": {...}}`, or
    `{"output_config": {"effort": ...}}` on a model that always thinks (module docstring)."""
    m = canonical_model(model)
    if mode == "disabled":
        if m in ALWAYS_THINKING:
            return {"output_config": {"effort": ALWAYS_ON_EFFORT}}
        return {"thinking": {"type": "disabled"}}
    if mode == "adaptive":
        if m in NO_ADAPTIVE:
            raise ValueError(f"{model} has no adaptive thinking; use thinking 'disabled'")
        if m in ALWAYS_THINKING:
            return {}
        return {"thinking": {"type": "adaptive"}}
    raise ValueError(f"unknown thinking mode {mode!r}")


def label(mode: ThinkingMode, model: str) -> str:
    """What is actually sent, in short: `disabled`, `adaptive` or `adaptive+effort:low`."""
    f = request_fields(mode, model)
    if "thinking" in f:
        return str(f["thinking"]["type"])
    effort = (f.get("output_config") or {}).get("effort")
    return "adaptive" + (f"+effort:{effort}" if effort else "")


def job_params(job: str, model: str, synthesis: ThinkingMode = DEFAULT_MODE) -> dict[str, str]:
    """The thinking record of a job for pre-registered parameters and reports."""
    mode = mode_for(job, synthesis)
    return {"version": THINKING_VERSION, "setting": mode, "sent": label(mode, model)}
