"""ADR-087: the thinking setting per job in every request, and billed failures in the ledger.

Synthetic fixtures only; no network."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from pigtail.briefs.budget import BudgetGuard
from pigtail.briefs.model import Budget
from pigtail.llm import BatchItem, LLMClient
from pigtail.llm.api import ApiBackend, BatchItemResult, build_params, parse_message
from pigtail.llm.batch import MemoryBatchStore
from pigtail.llm.errors import BackendError, BilledBackendError
from pigtail.llm.pricing import TokenUsage, cost_usd
from pigtail.llm.redact import alias_redact
from pigtail.llm.stages import JOB_STAGES
from pigtail.llm.store import LLMStore, UsageRow
from pigtail.llm.thinking import (
    JOB_THINKING,
    SYNTHESIS_JOBS,
    job_params,
    label,
    mode_for,
    parse_mode,
    request_fields,
    synthesis_mode,
)
from pigtail.llm.types import BackendResponse
from tests.conftest import Echo, FakeBackend

SONNET, HAIKU, OPUS55 = "claude-sonnet-5", "claude-haiku-4-5-20251001", "claude-opus-5-5"
MODELS = {"relevance": HAIKU, "extraction": SONNET, "synthesis": OPUS55}


# --- the setting ---------------------------------------------------------------------------
def test_every_job_has_a_setting_and_all_default_to_disabled() -> None:
    assert set(JOB_THINKING) == set(JOB_STAGES)
    assert set(JOB_THINKING.values()) == {"disabled"}
    for job in (
        "double_coding",
        "adjudication",
        "relevance_filter",
        "distribution_surface",
        "title_match_check",
        "ph_match_check",
        "brief_expansion",
        "launch_adjudication",
    ):
        assert mode_for(job) == "disabled"
    # synthesis jobs follow LLM_THINKING_SYNTHESIS; brief expansion does not
    assert mode_for("report", "adaptive") == "adaptive"
    assert mode_for("brief_expansion", "adaptive") == "disabled"
    assert set(JOB_STAGES) >= SYNTHESIS_JOBS
    assert synthesis_mode({}) == "disabled"
    assert synthesis_mode({"LLM_THINKING_SYNTHESIS": " Adaptive "}) == "adaptive"
    with pytest.raises(ValueError, match="LLM_THINKING_SYNTHESIS"):
        parse_mode("on")


def test_request_fields_per_model() -> None:
    # sonnet 5 and haiku 4.5 accept an explicit disable
    assert request_fields("disabled", SONNET) == {"thinking": {"type": "disabled"}}
    assert request_fields("disabled", HAIKU) == {"thinking": {"type": "disabled"}}
    assert request_fields("adaptive", SONNET) == {"thinking": {"type": "adaptive"}}
    # opus 5.5 can't disable thinking (HTTP 400): low effort, no thinking field
    assert request_fields("disabled", OPUS55) == {"output_config": {"effort": "low"}}
    assert request_fields("adaptive", OPUS55) == {}
    with pytest.raises(ValueError):
        request_fields("adaptive", HAIKU)  # no adaptive thinking on Haiku 4.5
    assert label("disabled", SONNET) == "disabled"
    assert label("disabled", OPUS55) == "adaptive+effort:low"
    assert job_params("distribution_surface", HAIKU) == {
        "version": "thinking-v1",
        "setting": "disabled",
        "sent": "disabled",
    }


def test_build_params_carries_the_setting_next_to_the_output_format() -> None:
    kw: dict[str, Any] = {
        "system": "s",
        "prompt": "p",
        "json_schema": {"type": "object"},
        "max_tokens": 100,
    }
    p = build_params(model=SONNET, **kw)
    assert p["thinking"] == {"type": "disabled"}
    assert p["output_config"] == {"format": {"type": "json_schema", "schema": {"type": "object"}}}
    p = build_params(model=OPUS55, **kw)
    assert "thinking" not in p and p["output_config"]["effort"] == "low"
    assert p["output_config"]["format"]["type"] == "json_schema"
    p = build_params(model=SONNET, thinking="adaptive", **kw)
    assert p["thinking"] == {"type": "adaptive"}


def test_client_sends_the_job_setting_on_standard_and_batch_requests() -> None:
    fb = FakeBackend("api", [{"value": 1, "label": "a"}])
    c = LLMClient(
        backends={"api": fb},
        default_backend="api",
        store=LLMStore(":memory:"),
        models=MODELS,
        redactor=alias_redact,
    )
    prompt = _prompt()
    r = c.complete(prompt, "x", Echo, job="double_coding")
    assert fb.calls[0]["thinking"] == "disabled"
    assert r.thinking == "disabled" and r.provenance()["thinking"] == "disabled"
    assert c.thinking_label("report", OPUS55, "api") == "adaptive+effort:low"
    assert c.thinking_label("report", OPUS55, "subscription") == "cli-default"
    # the cache key carries it; the key without a job is the default (the selection's checks)
    k = c.cache_key("api", HAIKU, prompt, "sh", "ih", job="title_match_check")
    assert k.endswith("|thinking=disabled")
    assert k == c.cache_key("api", HAIKU, prompt, "sh", "ih")
    assert k == c.cache_key("api", HAIKU, prompt, "sh", "ih", job="ph_match_check")
    adaptive = LLMClient(
        backends={"api": fb},
        default_backend="api",
        store=LLMStore(":memory:"),
        models=MODELS,
        redactor=alias_redact,
        thinking_synthesis="adaptive",
    )
    assert adaptive.cache_key("api", OPUS55, prompt, "sh", "ih", job="report") != c.cache_key(
        "api", OPUS55, prompt, "sh", "ih", job="report"
    )


def test_selection_params_record_the_setting() -> None:
    from pigtail.briefs.confirm import confirmation_params, ph_confirmation_params
    from pigtail.briefs.surface import surface_params

    for params in (
        surface_params(),
        confirmation_params()["haiku"],
        ph_confirmation_params()["haiku"],
    ):
        assert params["thinking"]["setting"] == "disabled"
        assert params["thinking"]["sent"] == "disabled"


# --- billed failures -----------------------------------------------------------------------
def _msg(stop: str, text: str = "", model: str = SONNET) -> Any:
    return SimpleNamespace(
        id="msg_1",
        model=model,
        stop_reason=stop,
        content=[SimpleNamespace(type="thinking", thinking="")]
        + ([SimpleNamespace(type="text", text=text)] if text else []),
        usage=SimpleNamespace(
            input_tokens=9083,
            output_tokens=16000,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        ),
    )


@pytest.mark.parametrize("stop,kind", [("max_tokens", "max_tokens"), ("refusal", "refusal")])
def test_unusable_responses_carry_their_billed_usage(stop: str, kind: str) -> None:
    usage = TokenUsage(input=9083, output=16000)
    with pytest.raises(BilledBackendError) as ei:
        parse_message(_msg(stop))
    e = ei.value
    assert isinstance(e, BackendError) and e.kind == kind
    assert (e.response.input_tokens, e.response.output_tokens) == (9083, 16000)
    assert e.response.cost_usd == pytest.approx(cost_usd(SONNET, usage) or 0)
    with pytest.raises(BilledBackendError) as eb:
        parse_message(_msg(stop), batch_id="msgbatch_1")
    assert eb.value.response.cost_usd == pytest.approx(cost_usd(SONNET, usage, batch=True) or 0)
    with pytest.raises(BilledBackendError) as ej:
        parse_message(_msg("end_turn", text="not json"))
    assert ej.value.kind == "non_json_output" and ej.value.response.output_tokens == 16000


def test_api_backend_sends_disabled_thinking_and_raises_billed() -> None:
    sent: dict[str, Any] = {}

    def create(**kw: Any) -> Any:
        sent.update(kw)
        return _msg("max_tokens")

    b = ApiBackend(client=SimpleNamespace(messages=SimpleNamespace(create=create)))  # type: ignore[arg-type]
    with pytest.raises(BilledBackendError):
        b.complete(system="s", prompt="p", json_schema={}, model=SONNET)
    assert sent["thinking"] == {"type": "disabled"}


class BilledBackend:
    """Standard calls end `max_tokens` (billed); batch results too (succeeded + max_tokens)."""

    name = "api"
    supports_batch = True

    def __init__(self) -> None:
        self.submitted: list[list[tuple[str, dict[str, Any]]]] = []
        self.standard = 0

    def _billed(self, model: str, batch_id: str | None) -> BackendResponse:
        u = TokenUsage(input=9083, output=16000)
        return BackendResponse(
            data={},
            model=model,
            input_tokens=u.input,
            output_tokens=u.output,
            cost_usd=cost_usd(model, u, batch=batch_id is not None) or 0.0,
            batch_id=batch_id,
        )

    def params(self, **kw: Any) -> dict[str, Any]:
        return build_params(max_tokens=16000, **kw)

    def complete(self, **kw: Any) -> BackendResponse:
        self.standard += 1
        raise BilledBackendError(
            "api stop_reason=max_tokens", self._billed(kw["model"], None), kind="max_tokens"
        )

    def submit_batch(self, requests: list[tuple[str, dict[str, Any]]]) -> str:
        self.submitted.append(list(requests))
        return f"msgbatch_{len(self.submitted)}"

    def batch_status(self, batch_id: str) -> Any:
        from pigtail.llm.api import BatchStatus

        return BatchStatus(batch_id, "ended", {})

    def batch_results(self, batch_id: str) -> Any:
        n = int(batch_id.rsplit("_", 1)[1])
        for cid, p in self.submitted[n - 1]:
            yield BatchItemResult(
                cid,
                "errored",
                error="max_tokens",
                retryable=True,
                message="api stop_reason=max_tokens",
                billed=self._billed(p["model"], batch_id),
            )


class Sink:
    def __init__(self) -> None:
        self.rows: list[UsageRow] = []

    def record(self, row: UsageRow) -> None:
        self.rows.append(row)


def _prompt() -> Any:
    from pigtail.llm import PromptSpec

    return PromptSpec(id="echo", version="1", system="sys", template="Input: {input}")


def test_standard_call_billed_failure_is_in_the_ledger_and_the_budget() -> None:
    store, sink = LLMStore(":memory:"), Sink()
    c = LLMClient(
        backends={"api": BilledBackend()},
        default_backend="api",
        store=store,
        models=MODELS,
        redactor=alias_redact,
        cost_sink=sink,
    )
    with pytest.raises(BilledBackendError):
        c.complete(_prompt(), "x", Echo, job="double_coding", brief_run_id="run_1")
    (row,) = sink.rows
    usage = TokenUsage(input=9083, output=16000)
    assert row.status == "error_billed" and (row.input_tokens, row.output_tokens) == (9083, 16000)
    assert row.cost_usd == pytest.approx(cost_usd(SONNET, usage) or 0) and row.cost_usd > 0
    since = datetime.now(UTC) - timedelta(hours=1)
    assert store.usage_since("api", since)["cost_usd"] == pytest.approx(row.cost_usd)
    assert store.summary()["api"]["errors"] == 1
    assert store.cost_by_brief_run("run_1")["-"]["cost_usd"] == pytest.approx(row.cost_usd)
    guard = BudgetGuard(Budget(money_usd=150.0, llm_backend="api"), store)
    assert guard.month_spent() == pytest.approx(row.cost_usd)


def test_batch_billed_failure_then_fallback_max_tokens_is_an_item_failure() -> None:
    store, sink, fb = LLMStore(":memory:"), Sink(), BilledBackend()
    c = LLMClient(
        backends={"api": fb},
        default_backend="api",
        store=store,
        models=MODELS,
        redactor=alias_redact,
        cost_sink=sink,
        batch_store=MemoryBatchStore(),
    )
    checks: list[float | None] = []
    run = c.run_batch(
        _prompt(),
        [BatchItem(ref="a", input_text="one", case_ref="c1")],
        Echo,
        job="double_coding",
        brief_run_id="run_1",
        est_usd_per_item=0.01,
        before_submit=lambda _j, _n, usd: checks.append(usd),
        sleep=lambda _s: None,
    )
    # no exception: the item failed, with its type and message
    assert run.results == {} and run.failed == {"a": "max_tokens"}
    assert run.errors["a"]["type"] == "max_tokens" and "max_tokens" in str(run.errors["a"])
    assert fb.standard == 1 and len(checks) == 2  # the batch and the fallback, each checked
    # the batch request was sent with thinking disabled
    assert fb.submitted[0][0][1]["thinking"] == {"type": "disabled"}
    usage = TokenUsage(input=9083, output=16000)
    got = [(r.status, r.batch_id is not None, round(r.cost_usd, 6)) for r in sink.rows]
    assert got == [
        ("error_billed", True, round(cost_usd(SONNET, usage, batch=True) or 0, 6)),
        ("error_billed", False, round(cost_usd(SONNET, usage) or 0, 6)),
    ]
    since = datetime.now(UTC) - timedelta(hours=1)
    assert store.usage_since("api", since)["cost_usd"] == pytest.approx(
        sum(r.cost_usd for r in sink.rows)
    )


def test_standard_mode_run_batch_also_fails_per_item() -> None:
    c = LLMClient(
        backends={"api": BilledBackend()},
        default_backend="api",
        store=LLMStore(":memory:"),
        models=MODELS,
        redactor=alias_redact,
        use_batch=False,
    )
    run = c.run_batch(
        _prompt(),
        [BatchItem(ref="a", input_text="one"), BatchItem(ref="b", input_text="two")],
        Echo,
        job="adjudication",
    )
    assert run.mode == "standard" and run.failed == {"a": "max_tokens", "b": "max_tokens"}
