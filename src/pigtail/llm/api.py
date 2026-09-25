"""`api` backend: Anthropic SDK with ANTHROPIC_API_KEY (R15.3).

Zero data retention is an organization-level agreement with Anthropic, not a request flag; the
operator guide documents it (PRD §10).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import anthropic

from pigtail.llm.errors import BackendError, UsageLimitReached
from pigtail.llm.types import BackendResponse

# USD per million tokens (input, output), Anthropic first-party list prices as of 2026-09-25
# (https://www.anthropic.com/pricing). Used for the cost ledger only.
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pin, pout = PRICES.get(model, (0.0, 0.0))
    return (input_tokens * pin + output_tokens * pout) / 1_000_000


class ApiBackend:
    name = "api"

    def __init__(self, client: anthropic.Anthropic | None = None, max_tokens: int = 16000) -> None:
        self._client = client
        self.max_tokens = max_tokens

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic(max_retries=3)
        return self._client

    def complete(
        self, *, system: str, prompt: str, json_schema: dict[str, Any], model: str
    ) -> BackendResponse:
        try:
            msg = self.client.messages.create(
                model=model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_config={"format": {"type": "json_schema", "schema": json_schema}},
            )
        except anthropic.RateLimitError as e:
            retry_after = e.response.headers.get("retry-after")
            reset = (
                datetime.now(UTC) + timedelta(seconds=float(retry_after)) if retry_after else None
            )
            raise UsageLimitReached("api rate limit (429)", reset) from e
        except anthropic.APIStatusError as e:
            raise BackendError(f"api error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise BackendError(f"api connection error: {e}") from e

        if msg.stop_reason in ("refusal", "max_tokens"):
            raise BackendError(f"api stop_reason={msg.stop_reason}")
        text = "".join(b.text for b in msg.content if b.type == "text")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise BackendError("api returned non-JSON output") from e
        if not isinstance(data, dict):
            raise BackendError("structured output is not a JSON object")
        u = msg.usage
        return BackendResponse(
            data=data,
            model=msg.model,
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cost_usd=estimate_cost(msg.model, u.input_tokens, u.output_tokens),
            raw_meta={"id": msg.id},
        )
