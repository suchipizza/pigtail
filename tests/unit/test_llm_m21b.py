"""M21b LLM layer (PRD R15.8-R15.11; Directive §6.3-6.4, ADR-064.3, ADR-072.5): per-stage
models, the Message Batches API (submit, poll, collect, resume by batch id), prompt caching on
the stable prefix, evidence trimming, pricing, and provenance with every output.

Fake backends and a fake Anthropic SDK client only: no real API call is made here.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from pigtail.config import DEFAULT_STAGE_MODELS, Settings, stage_models
from pigtail.llm import BatchItem, BatchPending, LLMClient, PromptSpec
from pigtail.llm.api import ApiBackend, BatchItemResult, BatchStatus, build_params, parse_message
from pigtail.llm.batch import MemoryBatchStore, custom_id_for
from pigtail.llm.pricing import (
    BATCH_DISCOUNT,
    PRICES_AS_OF,
    TokenUsage,
    canonical_model,
    cost_usd,
    price_for,
    pricing_table,
)
from pigtail.llm.redact import alias_redact
from pigtail.llm.stages import UnknownJob, stage_for, time_sensitive
from pigtail.llm.store import LLMStore
from pigtail.llm.trim import (
    TRIM_VERSION,
    TrimPolicy,
    dedupe_reposts,
    relevant_excerpts,
    trim_evidence,
    truncate_thread,
)
from pigtail.llm.types import BackendResponse
from tests.conftest import Echo, FakeBackend

MODELS = dict(DEFAULT_STAGE_MODELS)
PROMPT = PromptSpec(
    id="extract", version="3", system="sys", template="In: {input}", context="CODEBOOK " * 50
)


# --- settings and stages (R15.8, ADR-072.5) ------------------------------------------------------
def test_r15_8_stage_models_env_fallback_and_defaults():
    assert stage_models({}) == {
        "relevance": "claude-haiku-4-5-20251001",
        "extraction": "claude-sonnet-5",
        "synthesis": "claude-opus-5-5",
    }
    got = stage_models({"LLM_MODEL": "fallback-m", "LLM_MODEL_EXTRACTION": "own-m"})
    assert got == {"relevance": "fallback-m", "extraction": "own-m", "synthesis": "fallback-m"}


def test_adr_072_5_code_defaults_backend_subscription_budget_200_batch_on():
    s = Settings.from_env({})
    assert s.llm_backend == "subscription"  # the safe code default when LLM_BACKEND is unset
    assert s.budget_usd_month == 200.0 and s.llm_batch is True
    assert s.model_for("synthesis") == "claude-opus-5-5" and s.llm_model is None
    s = Settings.from_env({"LLM_BACKEND": "api", "BUDGET_USD_MONTH": "150", "LLM_BATCH": "0"})
    assert (s.llm_backend, s.budget_usd_month, s.llm_batch) == ("api", 150.0, False)
    with pytest.raises(ValueError):
        Settings.from_env({"BUDGET_USD_MONTH": "-1"})
    with pytest.raises(ValueError):
        Settings.from_env({"LLM_BATCH": "maybe"})


def test_r15_8_jobs_map_to_stages_and_unknown_jobs_are_refused():
    assert stage_for("relevance") == "relevance"
    assert {stage_for(j) for j in ("extraction", "coding", "adjudication")} == {"extraction"}
    assert {stage_for(j) for j in ("patterns", "report", "plan")} == {"synthesis"}
    assert stage_for("brief_expansion") == "synthesis"  # documented decision (stages.py)
    assert stage_for("launch_extraction") == "extraction"
    with pytest.raises(UnknownJob):
        stage_for("something_new")
    assert time_sensitive("brief_expansion") and time_sensitive("launch_relevance")
    assert not time_sensitive("extraction")


def client(backends: dict[str, Any], default: str = "api", **kw: Any) -> LLMClient:
    return LLMClient(
        backends=backends,
        default_backend=default,  # type: ignore[arg-type]
        store=kw.pop("store", LLMStore(":memory:")),
        models=kw.pop("models", MODELS),
        redactor=alias_redact,
        **kw,
    )


def test_r15_8_client_routes_each_job_to_its_stage_model():
    fake = FakeBackend("api", [{"value": 1, "label": "a"}] * 3)
    c = client({"api": fake})
    for job in ("relevance", "extraction", "report"):
        c.complete(PROMPT, f"x {job}", Echo, job=job)
    assert [call["model"] for call in fake.calls] == [
        "claude-haiku-4-5-20251001",
        "claude-sonnet-5",
        "claude-opus-5-5",
    ]
    assert fake.calls[0]["context"] == PROMPT.context  # the cached prefix is passed on
    with pytest.raises(UnknownJob):  # no fallback model: an unknown job is an error
        c.complete(PROMPT, "x", Echo, job="unmapped")


# --- pricing (R15.11) -----------------------------------------------------------------------------
def test_r15_11_pricing_table_for_the_three_models_labelled_and_dated():
    t = pricing_table()
    assert t["as_of"] == PRICES_AS_OF == "2026-06-24" and "pricing" in t["source"]
    m = t["models"]
    assert (m["claude-haiku-4-5"]["input"], m["claude-haiku-4-5"]["output"]) == (1.0, 5.0)
    assert (m["claude-sonnet-5"]["input"], m["claude-sonnet-5"]["output"]) == (2.0, 10.0)
    assert (m["claude-opus-5-5"]["input"], m["claude-opus-5-5"]["output"]) == (4.0, 20.0)
    assert m["claude-opus-5-5"]["cache_read"] == 0.20
    assert m["claude-sonnet-5"]["cache_write_5m"] == 2.5  # 1.25 x input
    assert m["claude-sonnet-5"]["cache_read"] == pytest.approx(0.2)  # 0.1 x input
    assert canonical_model("claude-haiku-4-5-20251001") == "claude-haiku-4-5"
    assert price_for("claude-haiku-4-5-20251001") is not None
    assert price_for("unknown-model") is None


def test_r15_9_cost_with_cache_and_batch_discount():
    u = TokenUsage(input=1_000_000, output=100_000, cache_write=1_000_000, cache_read=1_000_000)
    std = cost_usd("claude-sonnet-5", u)
    assert std == pytest.approx(2.0 + 1.0 + 2.5 + 0.2)
    assert cost_usd("claude-sonnet-5", u, batch=True) == pytest.approx(std * BATCH_DISCOUNT)
    assert cost_usd("nope", u) is None


# --- prompt caching (R15.9) ---------------------------------------------------------------------
def test_r15_9_cache_control_on_the_last_stable_block_only():
    p = build_params(system="S", context="CB", prompt="P", json_schema={}, model="m", max_tokens=9)
    assert p["system"] == [
        {"type": "text", "text": "S"},
        {"type": "text", "text": "CB", "cache_control": {"type": "ephemeral"}},
    ]
    assert p["messages"] == [{"role": "user", "content": "P"}]  # per-call input after the prefix
    assert p["output_config"]["format"]["type"] == "json_schema"
    p = build_params(system="S", prompt="P", json_schema={}, model="m", max_tokens=9)
    assert p["system"][-1]["cache_control"] == {"type": "ephemeral"}


def fake_message(data: str = '{"value": 1, "label": "a"}', model: str = "claude-sonnet-5") -> Any:
    usage = SimpleNamespace(
        input_tokens=100, output_tokens=20, cache_creation_input_tokens=0,
        cache_read_input_tokens=5000,
    )  # fmt: skip
    return SimpleNamespace(
        id="msg_1",
        model=model,
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text=data)],
        usage=usage,
    )


def test_r15_9_api_backend_reads_cache_tokens_and_prices_them():
    sent: list[dict[str, Any]] = []
    sdk = SimpleNamespace(
        messages=SimpleNamespace(create=lambda **kw: sent.append(kw) or fake_message())
    )
    r = ApiBackend(client=sdk).complete(  # type: ignore[arg-type]
        system="S", prompt="P", json_schema={}, model="claude-sonnet-5", context="CB"
    )
    assert sent[0]["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert (r.input_tokens, r.output_tokens, r.cache_read_tokens) == (100, 20, 5000)
    assert r.cost_usd == pytest.approx((100 * 2 + 20 * 10 + 5000 * 0.2) / 1e6)
    assert r.batch_id is None
    b = parse_message(fake_message(), batch_id="msgbatch_1")
    assert b.batch_id == "msgbatch_1" and b.cost_usd == pytest.approx(r.cost_usd * 0.5)


# --- Message Batches API (R15.9) ------------------------------------------------------------------
@dataclass
class FakeBatchBackend:
    """Batch-capable fake: `polls_until_end` status calls before a batch ends; per-item outcome
    by input marker ("ERR" errored, "BAD" invalid request, "SCHEMA" invalid output)."""

    name: str = "api"
    supports_batch: bool = True
    polls_until_end: int = 1
    submitted: list[list[tuple[str, dict[str, Any]]]] = field(default_factory=list)
    polls: dict[str, int] = field(default_factory=dict)
    standard: FakeBackend = field(default_factory=lambda: FakeBackend("api", []))

    def params(self, **kw: Any) -> dict[str, Any]:
        return build_params(max_tokens=100, **kw)

    def complete(self, **kw: Any) -> BackendResponse:
        return self.standard.complete(**kw)

    def submit_batch(self, requests: list[tuple[str, dict[str, Any]]]) -> str:
        self.submitted.append(list(requests))
        return f"msgbatch_{len(self.submitted)}"

    def batch_status(self, batch_id: str) -> BatchStatus:
        self.polls[batch_id] = self.polls.get(batch_id, 0) + 1
        ended = self.polls[batch_id] > self.polls_until_end
        return BatchStatus(batch_id, "ended" if ended else "in_progress", {"succeeded": 1})

    def batch_results(self, batch_id: str) -> Iterator[BatchItemResult]:
        n = int(batch_id.rsplit("_", 1)[1])
        for cid, params in reversed(self.submitted[n - 1]):  # results come in any order
            text = params["messages"][0]["content"]
            if "ERR" in text:
                yield BatchItemResult(cid, "errored", error="api_error", retryable=True)
            elif "BAD" in text:
                yield BatchItemResult(cid, "errored", error="invalid_request_error")
            else:
                data = {"value": "x"} if "SCHEMA" in text else {"value": len(text), "label": "b"}
                yield BatchItemResult(
                    cid,
                    "succeeded",
                    response=BackendResponse(
                        data=data,
                        model=params["model"],
                        input_tokens=10,
                        output_tokens=2,
                        cost_usd=0.001,
                        cache_read_tokens=7,
                        batch_id=batch_id,
                    ),
                )


def items(*texts: str) -> list[BatchItem]:
    return [
        BatchItem(ref=f"r{i}", input_text=t, case_ref=f"case{i % 2}") for i, t in enumerate(texts)
    ]


def test_r15_9_batch_submit_poll_collect_with_provenance_and_ledger():
    fb = FakeBatchBackend()
    c = client({"api": fb})
    sleeps: list[float] = []
    run = c.run_batch(PROMPT, items("a", "b", "a"), Echo, job="extraction", brief_run_id=None,
                      sleep=sleeps.append, poll_seconds=5)  # fmt: skip
    assert run.mode == "batch" and run.batch_ids == ["msgbatch_1"]
    assert len(fb.submitted[0]) == 2  # the duplicate input is one request
    assert sleeps == [5]  # polled once in progress, then ended
    r = run.results["r0"]
    assert r.batch_id == "msgbatch_1" and r.model == "claude-sonnet-5" and r.stage == "extraction"
    assert r.provenance()["batch_id"] == "msgbatch_1" and r.provenance()["prompt_version"] == "3"
    assert run.results["r2"].output == r.output
    # every request carries the cached prefix
    assert all(p["system"][-1]["cache_control"] for _, p in fb.submitted[0])
    # batch state and the usage ledger
    rec = c.batch_store.get("msgbatch_1")
    assert rec is not None and rec.status == "collected"
    assert {r.status for r in c.batch_store.requests("msgbatch_1")} == {"succeeded"}
    u = c.store.summary()["api"]
    assert u["sessions"] == 2 and u["cost_usd"] == pytest.approx(0.002)
    # a second run of the same items is served from the cache: nothing submitted
    again = c.run_batch(PROMPT, items("a", "b", "a"), Echo, job="extraction", sleep=sleeps.append)
    assert len(fb.submitted) == 1 and set(again.results) == {"r0", "r1", "r2"}


def test_r15_9_resume_collects_the_stored_batch_instead_of_resubmitting():
    fb = FakeBatchBackend(polls_until_end=3)
    store, bstore = LLMStore(":memory:"), MemoryBatchStore()
    c1 = client({"api": fb}, store=store, batch_store=bstore)
    with pytest.raises(BatchPending) as ei:
        c1.run_batch(PROMPT, items("a", "b"), Echo, job="extraction", brief_run_id=None,
                     timeout_seconds=0, sleep=lambda s: None)  # fmt: skip
    assert ei.value.batch_ids == ["msgbatch_1"]
    # a new process (same stores): the open batch is found by id and collected
    c2 = client({"api": fb}, store=store, batch_store=bstore)
    run = c2.run_batch(PROMPT, items("a", "b"), Echo, job="extraction", sleep=lambda s: None)
    assert len(fb.submitted) == 1 and run.batch_ids == ["msgbatch_1"]
    assert set(run.results) == {"r0", "r1"}


def test_r15_9_failed_requests_fall_back_or_are_reported():
    fb = FakeBatchBackend(polls_until_end=0)
    fb.standard.script = [{"value": 7, "label": "std"}, {"value": 8, "label": "std"}]
    c = client({"api": fb})
    run = c.run_batch(PROMPT, items("ok", "ERR", "BAD", "SCHEMA"), Echo, job="extraction",
                      sleep=lambda s: None)  # fmt: skip
    assert run.results["r0"].batch_id == "msgbatch_1"
    assert run.results["r1"].output.label == "std"  # retryable: one standard call
    assert run.results["r3"].output.label == "std"  # invalid output: one standard call
    assert run.failed == {"r2": "invalid_request_error"}  # not retried
    assert run.standard_fallbacks == 2
    states = {r.status for r in c.batch_store.requests("msgbatch_1")}
    assert states == {"succeeded", "errored", "invalid_output"}


def test_r15_11_budget_hook_runs_before_submit_and_can_stop_it():
    fb = FakeBatchBackend()
    c = client({"api": fb})
    seen: list[tuple[str, int, float | None]] = []

    def stop(job: str, n: int, usd: float | None) -> None:
        seen.append((job, n, usd))
        raise RuntimeError("budget stop")

    with pytest.raises(RuntimeError):
        c.run_batch(PROMPT, items("a", "b"), Echo, job="extraction", before_submit=stop,
                    est_usd_per_item=0.01)  # fmt: skip
    assert seen == [("extraction", 2, 0.02)] and fb.submitted == []


def test_r15_9_time_sensitive_jobs_and_subscription_use_standard_calls():
    fb = FakeBatchBackend()
    fb.standard.script = [{"value": 1, "label": "a"}] * 2
    c = client({"api": fb})
    run = c.run_batch(PROMPT, items("a"), Echo, job="launch_extraction")
    assert run.mode == "standard" and fb.submitted == []
    run = client({"api": fb}, use_batch=False).run_batch(PROMPT, items("b"), Echo, job="report")
    assert run.mode == "standard" and fb.submitted == []
    sub = FakeBackend("subscription", [{"value": 1, "label": "a"}])
    run = client({"subscription": sub}, default="subscription").run_batch(
        PROMPT, items("c"), Echo, job="extraction"
    )
    assert run.mode == "standard" and len(sub.calls) == 1


def test_r15_9_custom_ids_are_valid_and_stable():
    cid = custom_id_for("a|b|c")
    assert cid == custom_id_for("a|b|c") and len(cid) <= 64 and cid.replace("_", "").isalnum()


# --- evidence trimming (R15.10) -----------------------------------------------------------------
def test_r15_10_dedupe_reposts_exact_and_near_duplicates():
    posts = [
        "Show HN: a config linter https://example.com/x?utm_source=hn",
        "show hn - a config linter https://example.com/x",
        "Show HN: a config linter for YAML files that runs in CI and in your editor today",
        "Show HN: a config linter for YAML files that runs in CI and in your editor today!",
        "Something else entirely",
    ]
    kept, dropped = dedupe_reposts(posts)
    assert dropped == 2 and kept == [posts[0], posts[2], posts[4]]


def test_r15_10_truncate_long_threads_keeps_head_and_tail():
    thread = [f"comment {i}" for i in range(100)]
    out, omitted = truncate_thread(thread, head=3, tail=2)
    assert omitted == 95 and out[:3] == thread[:3] and out[-2:] == thread[-2:]
    assert "95 item(s) omitted" in out[3]


def test_r15_10_relevant_excerpts_around_terms():
    text = ("filler " * 200) + "the config linter caught it" + (" filler" * 200)
    ex = relevant_excerpts(text, ["config linter"], window_chars=60)
    assert ex is not None and "config linter" in ex and len(ex) < 120
    assert ex.startswith("[…]") and ex.endswith("[…]")
    assert relevant_excerpts(text, ["absent"]) is None


def test_r15_10_trim_evidence_report_counts_and_client_records_the_version():
    posts = ["dup post", "dup post", *[f"reply {i} " + "x" * 50 for i in range(40)]]
    t = trim_evidence(posts, policy=TrimPolicy(head=5, tail=5, max_item_chars=100))
    assert t.report.duplicates_dropped == 1 and t.report.thread_items_omitted == 31
    assert t.report.chars_out < t.report.chars_in and t.report.version == TRIM_VERSION
    fake = FakeBackend("api", [{"value": 1, "label": "a"}])
    r = client({"api": fake}).complete(PROMPT, t, Echo, job="extraction")
    assert r.trim_version == TRIM_VERSION and "omitted" in fake.calls[0]["prompt"]
    long = "word " * 5000
    fake2 = FakeBackend("api", [{"value": 1, "label": "a"}])
    client({"api": fake2}).complete(
        PROMPT, long, Echo, job="extraction", trim=TrimPolicy(max_item_chars=500)
    )
    assert len(fake2.calls[0]["prompt"]) < 700
