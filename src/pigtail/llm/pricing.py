"""Anthropic API list prices for the cost estimator and the actual-cost ledger (PRD R15.11,
R18.5; Directive §6.4, ADR-064.4).

**Source and date.** First-party Claude API list prices from the `claude-api` skill's model
table ("Current Models", cached 2026-06-24; read 2026-09-26 for M21b), which mirrors
https://www.anthropic.com/pricing. Multipliers from the same skill's prompt-caching and Message
Batches references:

- prompt-cache **write**: 1.25 x the base input price (5-minute TTL; 2 x for the 1-hour TTL);
- prompt-cache **read**: 0.1 x the base input price (Claude Opus 5.5 is listed at $0.20/MTok,
  which is the same 0.1 x);
- **Message Batches API**: 50 % of standard prices on all token usage; applied on top of the
  cache multipliers.

Prices change: re-check them against the pricing page before a large run, then bump
`PRICES_AS_OF` and log the change in `ops/DECISIONS.md`. A model missing from the table has an
unknown price: `price_for` returns None, and callers treat the call as paid and unknown (it
still needs explicit approval, H6).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

PRICES_AS_OF = "2026-06-24"
PRICES_SOURCE = (
    "claude-api skill model table (cached 2026-06-24, read 2026-09-26), mirroring "
    "https://www.anthropic.com/pricing"
)
CACHE_WRITE_MULTIPLIER = {"5m": 1.25, "1h": 2.0}
CACHE_READ_MULTIPLIER = 0.1
BATCH_DISCOUNT = 0.5  # batch requests cost 50 % of standard prices

CacheTTL = Literal["5m", "1h"]


@dataclass(frozen=True)
class ModelPrice:
    """USD per million tokens."""

    input: float
    output: float
    cache_read: float | None = None  # None: CACHE_READ_MULTIPLIER x input

    def read(self) -> float:
        if self.cache_read is not None:
            return self.cache_read
        return self.input * CACHE_READ_MULTIPLIER

    def write(self, ttl: CacheTTL = "5m") -> float:
        return self.input * CACHE_WRITE_MULTIPLIER[ttl]

    def to_dict(self) -> dict[str, float]:
        return {
            "input": self.input,
            "output": self.output,
            "cache_write_5m": self.write("5m"),
            "cache_write_1h": self.write("1h"),
            "cache_read": self.read(),
        }


# The three models of R15.8 first; the earlier default kept so older ledger rows still price.
PRICES: dict[str, ModelPrice] = {
    "claude-haiku-4-5": ModelPrice(1.0, 5.0),
    "claude-sonnet-5": ModelPrice(2.0, 10.0),
    "claude-opus-5-5": ModelPrice(4.0, 20.0, cache_read=0.20),
    "claude-opus-5": ModelPrice(5.0, 25.0),
}
_DATE_SUFFIX = re.compile(r"-\d{8}$")


def canonical_model(model: str) -> str:
    """`claude-haiku-4-5-20251001` -> `claude-haiku-4-5` (dated snapshot of the same model)."""
    return _DATE_SUFFIX.sub("", model.strip())


def price_for(model: str) -> ModelPrice | None:
    return PRICES.get(canonical_model(model))


@dataclass(frozen=True)
class TokenUsage:
    """Tokens of one call as the API reports them. `input` excludes cache reads and writes."""

    input: int = 0
    output: int = 0
    cache_write: int = 0
    cache_read: int = 0

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            self.input + other.input,
            self.output + other.output,
            self.cache_write + other.cache_write,
            self.cache_read + other.cache_read,
        )

    @property
    def total(self) -> int:
        return self.input + self.output + self.cache_write + self.cache_read

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input,
            "output_tokens": self.output,
            "cache_write_tokens": self.cache_write,
            "cache_read_tokens": self.cache_read,
        }


def cost_usd(
    model: str,
    usage: TokenUsage,
    *,
    batch: bool = False,
    ttl: CacheTTL = "5m",
) -> float | None:
    """List-price cost of `usage` on `model`; None when the model's price is unknown."""
    p = price_for(model)
    if p is None:
        return None
    usd = (
        usage.input * p.input
        + usage.output * p.output
        + usage.cache_write * p.write(ttl)
        + usage.cache_read * p.read()
    ) / 1_000_000
    return usd * BATCH_DISCOUNT if batch else usd


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Standard-call cost without caching (0.0 for an unknown model; see `cost_usd`)."""
    return cost_usd(model, TokenUsage(input_tokens, output_tokens)) or 0.0


def pricing_table() -> dict[str, Any]:
    """The table as shown by `pigtail brief estimate --json` and the UI (labelled, dated)."""
    return {
        "as_of": PRICES_AS_OF,
        "source": PRICES_SOURCE,
        "unit": "USD per million tokens",
        "batch_discount": BATCH_DISCOUNT,
        "models": {m: p.to_dict() for m, p in PRICES.items()},
    }
