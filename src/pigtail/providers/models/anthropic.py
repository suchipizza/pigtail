"""Anthropic (Claude) model adapter. Structured outputs via messages.parse + Pydantic."""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path

import anthropic
from pydantic import ValidationError

from pigtail.errors import CredentialsError, ResearchError
from pigtail.logging import get_logger
from pigtail.providers.base import Meter, StructuredModelRequest, T

log = get_logger("model.anthropic")

# USD per million tokens (input, output). Cached 2026-09-25 from Anthropic's published prices.
PRICES: dict[str, tuple[float, float]] = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
# Models that accept output_config.effort.
EFFORT_MODELS = {
    "claude-fable-5-1",
    "claude-fable-5",
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-sonnet-5-5",
    "claude-sonnet-5",
}


def price_for(model: str) -> tuple[float, float]:
    return PRICES.get(model, (5.0, 25.0))


class AnthropicModelProvider:
    provider_key = "anthropic"

    def __init__(self, model_id: str, api_key: str | None, meter: Meter, max_concurrency: int = 6):
        if not api_key:
            raise CredentialsError("ANTHROPIC_API_KEY is not set.", hint="Run `pigtail doctor` for setup help.")
        if model_id not in PRICES:
            log.warning("Unknown Anthropic model %s; cost estimates use Opus pricing", model_id)
        self.model_id = model_id
        self.client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=3, timeout=600)
        self.meter = meter
        self.sem = asyncio.Semaphore(max_concurrency)

    def _account(self, usage: object) -> None:
        pin, pout = price_for(self.model_id)
        inp = getattr(usage, "input_tokens", 0) or 0
        cache_w = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_r = getattr(usage, "cache_read_input_tokens", 0) or 0
        outp = getattr(usage, "output_tokens", 0) or 0
        self.meter.model_input_tokens += inp + cache_w + cache_r
        self.meter.model_output_tokens += outp
        self.meter.model_cost += (inp * pin + cache_w * pin * 1.25 + cache_r * pin * 0.1 + outp * pout) / 1e6
        self.meter.model_calls += 1

    def _cache_path(self, request: StructuredModelRequest[T]) -> Path | None:
        """Developer-only response cache (PIGTAIL_DEV_CACHE=1). Never enabled by default."""
        if os.environ.get("PIGTAIL_DEV_CACHE") != "1":
            return None
        key = hashlib.sha256(
            "\x00".join([self.model_id, request.system, request.prompt, request.output_type.__name__]).encode()
        ).hexdigest()
        return Path(".pigtail-cache") / f"{key}.json"

    async def structured(self, request: StructuredModelRequest[T]) -> T:
        cache = self._cache_path(request)
        if cache and cache.exists():
            return request.output_type.model_validate_json(cache.read_text())
        result = await self._structured(request)
        if cache:
            cache.parent.mkdir(exist_ok=True)
            cache.write_text(result.model_dump_json())
        return result

    async def _structured(self, request: StructuredModelRequest[T]) -> T:
        kwargs: dict = {}
        if self.model_id in EFFORT_MODELS:
            kwargs["output_config"] = {"effort": request.effort}
        last_err: Exception | None = None
        for attempt in range(3):
            async with self.sem:
                try:
                    async with self.client.messages.stream(
                        model=self.model_id,
                        max_tokens=request.max_tokens,
                        system=request.system,
                        messages=[{"role": "user", "content": request.prompt}],
                        output_format=request.output_type,
                        **kwargs,
                    ) as stream:
                        resp = await stream.get_final_message()
                except anthropic.AuthenticationError as exc:
                    raise CredentialsError("The Anthropic API rejected the API key.") from exc
                except anthropic.NotFoundError as exc:
                    raise CredentialsError(f"Model {self.model_id!r} is not available to this API key.") from exc
                except anthropic.BadRequestError as exc:
                    raise ResearchError(f"Model request rejected: {exc.message}") from exc
                except (
                    anthropic.RateLimitError,
                    anthropic.APIConnectionError,
                    anthropic.InternalServerError,
                ) as exc:
                    self.meter.retries += 1
                    last_err = exc
                    await asyncio.sleep(5 * (attempt + 1))
                    continue
            self._account(resp.usage)
            if resp.stop_reason == "refusal":
                raise ResearchError(f"The model declined the {request.purpose} request.")
            if resp.stop_reason == "max_tokens":
                self.meter.retries += 1
                last_err = ResearchError("model output was truncated")
                request.max_tokens = min(64000, request.max_tokens * 2)
                continue
            parsed = getattr(resp, "parsed_output", None)
            if parsed is None:
                text = "".join(getattr(b, "text", "") for b in resp.content)
                try:
                    parsed = request.output_type.model_validate_json(text)
                except ValidationError as exc:
                    self.meter.retries += 1
                    last_err = exc
                    continue
            return parsed
        raise ResearchError(f"Model call for {request.purpose} failed after retries: {last_err}")
