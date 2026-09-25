"""Tests for LLMClient (PRD F15: R15.1, R15.4, R15.5; §10 redaction before model calls)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from pigtail.llm import QueuePaused, StructuredOutputError, UsageLimitReached
from pigtail.llm.errors import BackendError
from tests.conftest import Echo, FakeBackend, make_client


def test_r15_4_structured_output_validated_and_provenance(prompt):
    c = make_client([{"value": 1, "label": "a"}])
    r = c.complete(prompt, "hello", Echo, job="j")
    assert r.output == Echo(value=1, label="a")
    assert r.backend == "subscription" and r.prompt_version == "1" and not r.cached
    assert set(r.provenance()) == {
        "backend",
        "model",
        "prompt_id",
        "prompt_version",
        "prompt_fingerprint",
        "input_hash",
    }


def test_r15_4_schema_sent_is_closed(prompt):
    c = make_client([{"value": 1, "label": "a"}])
    c.complete(prompt, "x", Echo, job="j")
    fake = c.backends["subscription"]
    assert isinstance(fake, FakeBackend)
    assert fake.calls[0]["schema"]["additionalProperties"] is False


def test_r15_4_cache_keyed_on_prompt_version_and_input(prompt):
    c = make_client([{"value": 1, "label": "a"}, {"value": 2, "label": "b"}])
    assert c.complete(prompt, "same", Echo, job="j").output.value == 1
    again = c.complete(prompt, "same", Echo, job="j")
    assert again.cached and again.output.value == 1
    bumped = type(prompt)(id=prompt.id, version="2", system=prompt.system, template=prompt.template)
    assert c.complete(bumped, "same", Echo, job="j").output.value == 2
    assert c.store.summary()["subscription"]["cache_hits"] == 1


def test_invalid_output_retried_once_then_fails(prompt):
    c = make_client([{"value": "nope"}, {"value": 3, "label": "ok"}])
    assert c.complete(prompt, "x", Echo, job="j").output.value == 3
    c2 = make_client([{"bad": 1}, {"bad": 2}])
    with pytest.raises(StructuredOutputError):
        c2.complete(prompt, "x", Echo, job="j")


def test_r15_5_limit_pauses_backend_and_is_logged(prompt):
    reset = datetime.now(UTC) + timedelta(minutes=30)
    c = make_client([UsageLimitReached("limit", reset), {"value": 1, "label": "a"}])
    with pytest.raises(UsageLimitReached):
        c.complete(prompt, "x", Echo, job="j")
    with pytest.raises(QueuePaused) as ei:
        c.complete(prompt, "x", Echo, job="j")
    assert ei.value.until == reset
    assert c.store.summary()["subscription"]["limit_hits"] == 1
    assert len(c.store.pause_log()) == 1


def test_r15_5_limit_without_reset_uses_default_pause(prompt):
    c = make_client([UsageLimitReached("limit")], limit_pause_seconds=600)
    with pytest.raises(UsageLimitReached):
        c.complete(prompt, "x", Echo, job="j")
    until = c.store.paused_until("subscription")
    assert until is not None and until > datetime.now(UTC) + timedelta(seconds=500)


def test_r15_5_pause_expires(prompt):
    c = make_client([{"value": 1, "label": "a"}])
    c.store.pause("subscription", datetime.now(UTC) - timedelta(seconds=1), "old")
    assert c.complete(prompt, "x", Echo, job="j").output.value == 1


def test_r15_5_per_job_override_routes_to_api(prompt):
    c = make_client([], api=[{"value": 9, "label": "api"}], overrides={"tier2_extraction": "api"})
    r = c.complete(prompt, "x", Echo, job="tier2_extraction")
    assert r.backend == "api"


def test_r15_1_unconfigured_backend_rejected():
    with pytest.raises(ValueError):
        make_client([], default_backend="api")


def test_backend_error_recorded(prompt):
    c = make_client([BackendError("boom")])
    with pytest.raises(BackendError):
        c.complete(prompt, "x", Echo, job="j")
    assert c.store.summary()["subscription"]["errors"] == 1


def test_prd10_identifiers_stripped_before_model_call(prompt):
    c = make_client([{"value": 1, "label": "a"}])
    c.complete(prompt, "ping @someuser or mail someone@example.com", Echo, job="j")
    fake = c.backends["subscription"]
    assert isinstance(fake, FakeBackend)
    sent = fake.calls[0]["prompt"]
    assert "someuser" not in sent and "someone@example.com" not in sent
    assert "[email]" in sent and "@p_" in sent


def test_r15_5_paused_backend_still_serves_cache(prompt):
    c = make_client([{"value": 1, "label": "a"}])
    c.complete(prompt, "x", Echo, job="j")
    c.store.pause("subscription", datetime.now(UTC) + timedelta(hours=1), "test")
    assert c.complete(prompt, "x", Echo, job="j").cached
    with pytest.raises(QueuePaused):
        c.complete(prompt, "other", Echo, job="j")


def test_prompt_edit_without_version_bump_misses_cache(prompt):
    c = make_client([{"value": 1, "label": "a"}, {"value": 2, "label": "b"}])
    c.complete(prompt, "x", Echo, job="j")
    edited = type(prompt)(
        id=prompt.id, version=prompt.version, system="changed", template=prompt.template
    )
    assert c.complete(edited, "x", Echo, job="j").output.value == 2
