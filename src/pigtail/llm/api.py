"""`api` backend: Anthropic SDK with ANTHROPIC_API_KEY (R15.3), standard calls and the Message
Batches API (R15.9), with prompt caching on the stable prefix.

Zero data retention is an organization-level agreement with Anthropic, not a request flag; the
operator guide documents it (PRD §10). Credentials are never read or logged here: the SDK
resolves them from the environment.

**Prompt caching (R15.9).** The system prompt and the prompt's `context` (the codebook) are
sent as system blocks, with `cache_control: ephemeral` (5-minute TTL) on the last one, so all
calls of a stage share one cached prefix; the per-call input goes in the user turn after it.
Prefixes shorter than the model's minimum cacheable length are not cached (no error; usage
then shows no cache writes).

**Batches (R15.9).** `submit_batch` sends one Message Batches request per item (the same
parameters as a standard call), `batch_status` polls it and `batch_results` streams the
results, which arrive in any order and are keyed by `custom_id`. Batch usage costs 50 % of the
standard price (`pigtail.llm.pricing`). The batch id is stored by the caller
(`pigtail.llm.batch`), so a paused or restarted run collects the same batch instead of paying
for it twice.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import anthropic

from pigtail.llm.errors import BackendError, UsageLimitReached
from pigtail.llm.pricing import PRICES, TokenUsage, cost_usd, estimate_cost
from pigtail.llm.types import BackendResponse

__all__ = [
    "PRICES",
    "ApiBackend",
    "BatchItemResult",
    "BatchStatus",
    "build_params",
    "estimate_cost",
    "parse_message",
]

CACHE_CONTROL = {"type": "ephemeral"}


def build_params(
    *,
    system: str,
    prompt: str,
    json_schema: dict[str, Any],
    model: str,
    max_tokens: int,
    context: str = "",
) -> dict[str, Any]:
    """Messages API parameters for one structured call; the stable prefix is cache-marked."""
    blocks: list[dict[str, Any]] = [{"type": "text", "text": system}]
    if context:
        blocks.append({"type": "text", "text": context})
    blocks[-1]["cache_control"] = dict(CACHE_CONTROL)
    return {
        "model": model,
        "max_tokens": max_tokens,
        "system": blocks,
        "messages": [{"role": "user", "content": prompt}],
        "output_config": {"format": {"type": "json_schema", "schema": json_schema}},
    }


def _usage(msg: Any) -> TokenUsage:
    u = msg.usage
    return TokenUsage(
        input=int(getattr(u, "input_tokens", 0) or 0),
        output=int(getattr(u, "output_tokens", 0) or 0),
        cache_write=int(getattr(u, "cache_creation_input_tokens", 0) or 0),
        cache_read=int(getattr(u, "cache_read_input_tokens", 0) or 0),
    )


def parse_message(msg: Any, *, batch_id: str | None = None) -> BackendResponse:
    """A Messages API response (standard or from a batch) as a `BackendResponse`."""
    if msg.stop_reason in ("refusal", "max_tokens"):
        raise BackendError(f"api stop_reason={msg.stop_reason}")
    text = "".join(b.text for b in msg.content if b.type == "text")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise BackendError("api returned non-JSON output") from e
    if not isinstance(data, dict):
        raise BackendError("structured output is not a JSON object")
    usage = _usage(msg)
    return BackendResponse(
        data=data,
        model=msg.model,
        input_tokens=usage.input,
        output_tokens=usage.output,
        cost_usd=cost_usd(msg.model, usage, batch=batch_id is not None) or 0.0,
        raw_meta={"id": msg.id},
        cache_write_tokens=usage.cache_write,
        cache_read_tokens=usage.cache_read,
        batch_id=batch_id,
    )


@dataclass(frozen=True)
class BatchStatus:
    batch_id: str
    processing_status: str  # in_progress | canceling | ended
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def ended(self) -> bool:
        return self.processing_status == "ended"


@dataclass(frozen=True)
class BatchItemResult:
    custom_id: str
    kind: str  # succeeded | errored | canceled | expired
    response: BackendResponse | None = None
    error: str | None = None  # error type only, never content
    retryable: bool = False


def _rate_limited(e: anthropic.RateLimitError) -> UsageLimitReached:
    retry_after = e.response.headers.get("retry-after")
    reset = datetime.now(UTC) + timedelta(seconds=float(retry_after)) if retry_after else None
    return UsageLimitReached("api rate limit (429)", reset)


class ApiBackend:
    name = "api"
    supports_batch = True

    def __init__(self, client: anthropic.Anthropic | None = None, max_tokens: int = 16000) -> None:
        self._client = client
        self.max_tokens = max_tokens

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic(max_retries=3)
        return self._client

    def params(
        self,
        *,
        system: str,
        prompt: str,
        json_schema: dict[str, Any],
        model: str,
        context: str = "",
    ) -> dict[str, Any]:
        return build_params(
            system=system,
            prompt=prompt,
            json_schema=json_schema,
            model=model,
            max_tokens=self.max_tokens,
            context=context,
        )

    def complete(
        self,
        *,
        system: str,
        prompt: str,
        json_schema: dict[str, Any],
        model: str,
        context: str = "",
    ) -> BackendResponse:
        params = self.params(
            system=system, prompt=prompt, json_schema=json_schema, model=model, context=context
        )
        try:
            msg = self.client.messages.create(**params)
        except anthropic.RateLimitError as e:
            raise _rate_limited(e) from e
        except anthropic.APIStatusError as e:
            raise BackendError(f"api error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise BackendError(f"api connection error: {e}") from e
        return parse_message(msg)

    # --- Message Batches API (R15.9) --------------------------------------------------------
    def submit_batch(self, requests: Sequence[tuple[str, dict[str, Any]]]) -> str:
        """Create one batch of (custom_id, params) requests; returns the batch id."""
        from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
        from anthropic.types.messages.batch_create_params import Request

        if not requests:
            raise ValueError("a batch needs at least one request")
        reqs = [
            Request(custom_id=cid, params=MessageCreateParamsNonStreaming(**params))  # type: ignore[typeddict-item]
            for cid, params in requests
        ]
        try:
            batch = self.client.messages.batches.create(requests=reqs)
        except anthropic.RateLimitError as e:
            raise _rate_limited(e) from e
        except anthropic.APIStatusError as e:
            raise BackendError(f"api batch error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise BackendError(f"api connection error: {e}") from e
        return str(batch.id)

    def batch_status(self, batch_id: str) -> BatchStatus:
        try:
            b = self.client.messages.batches.retrieve(batch_id)
        except anthropic.APIStatusError as e:
            raise BackendError(f"api batch error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise BackendError(f"api connection error: {e}") from e
        rc = b.request_counts
        counts = {
            k: int(getattr(rc, k, 0) or 0)
            for k in ("processing", "succeeded", "errored", "canceled", "expired")
        }
        return BatchStatus(batch_id, str(b.processing_status), counts)

    def batch_results(self, batch_id: str) -> Iterator[BatchItemResult]:
        """Every result of an ended batch, in any order (key them by `custom_id`)."""
        try:
            results = self.client.messages.batches.results(batch_id)
        except anthropic.APIStatusError as e:
            raise BackendError(f"api batch error {e.status_code}: {e.message}") from e
        for r in results:
            res = r.result
            kind = res.type
            if res.type == "succeeded":
                try:
                    resp = parse_message(res.message, batch_id=batch_id)
                except BackendError as e:
                    yield BatchItemResult(r.custom_id, "errored", error=str(e), retryable=True)
                    continue
                yield BatchItemResult(r.custom_id, "succeeded", response=resp)
            elif kind == "errored":
                etype = getattr(getattr(r.result, "error", None), "error", None)
                etype_s = str(getattr(etype, "type", "error"))
                yield BatchItemResult(
                    r.custom_id,
                    "errored",
                    error=etype_s,
                    retryable=etype_s != "invalid_request_error",
                )
            else:  # canceled | expired: safe to resubmit
                yield BatchItemResult(r.custom_id, kind, retryable=True)
