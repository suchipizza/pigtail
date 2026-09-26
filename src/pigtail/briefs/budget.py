"""Budget hard stop for brief runs (PRD R18.5; ADR-053.1).

Two separate caps from the brief's `budget`:

- `money_usd`: non-LLM paid services (BigQuery beyond its free tier, Trendshift, X, any paid
  API). Default 0. A step that costs money also needs the user's **explicit approval** of the
  estimate (`approved_paid=True`, from `pigtail brief estimate --approve-paid` or the D7 run
  dialog); without it the step is refused even if the cap would allow it.
- `subscription_share`: the maximum share of the user's weekly Claude subscription allowance
  that pigtail may use. Claude Code exposes no machine-readable remaining allowance, so the
  share is measured against pigtail's own usage ledger (`LLMStore`, trailing 7 days) and an
  allowance that is configured (`PIGTAIL_SUBSCRIPTION_WEEKLY_TOKENS`), calibrated from the last
  observed limit hit, or, failing both, an assumed default. The basis is always reported, and
  every figure is labelled an estimate.

On the `api` backend LLM calls cost money: they are capped by `budget.llm_api_usd` and need the
same approval. The guard **never switches backends**: a call routed to a backend other than the
brief's `llm_backend` is refused unless the user set an explicit per-job override (R15.5).

Stages call `check_*` before a unit of work and `charge_*` after it. A refused check raises
`BudgetStop`; the stage writes a resumable checkpoint (see `pigtail.briefs.cache.BriefRun`) and
the run ends with status `paused_budget`, never with partial silent spending.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

from pigtail.briefs.model import Budget

WEEK = timedelta(days=7)
# Used only when the allowance is neither configured nor calibrated. An assumption, not a
# measurement: Anthropic publishes no token figure for subscription plans. Deliberately low so
# an uncalibrated install stops early rather than late. Override with
# PIGTAIL_SUBSCRIPTION_WEEKLY_TOKENS once you know your plan's behaviour.
ASSUMED_WEEKLY_TOKENS = 5_000_000
ALLOWANCE_ENV = "PIGTAIL_SUBSCRIPTION_WEEKLY_TOKENS"

StopKind = Literal["money", "approval", "subscription_share", "api_usd", "backend"]
AllowanceBasis = Literal["configured", "calibrated_from_limit_event", "assumed_default"]


class UsageSource(Protocol):
    """The LLM usage ledger (`pigtail.llm.store.LLMStore` implements it)."""

    def usage_since(self, backend: str, since: datetime) -> dict[str, float]: ...

    def last_limit(self, backend: str) -> datetime | None: ...

    def usage_between(self, backend: str, start: datetime, end: datetime) -> float: ...


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Allowance:
    """Estimated weekly subscription allowance in tokens, with how it was obtained."""

    weekly_tokens: int
    basis: AllowanceBasis

    def to_dict(self) -> dict[str, Any]:
        return {"weekly_tokens": self.weekly_tokens, "basis": self.basis, "label": "estimate"}


def resolve_allowance(
    usage: UsageSource | None,
    env: dict[str, str] | None = None,
    *,
    now: datetime | None = None,
) -> Allowance:
    """Configured > calibrated from the last limit hit (tokens used in the 7 days before it,
    within the last 5 weeks) > assumed default."""
    e = dict(os.environ) if env is None else env
    raw = (e.get(ALLOWANCE_ENV) or "").strip()
    if raw:
        n = int(raw)
        if n <= 0:
            raise ValueError(f"{ALLOWANCE_ENV} must be a positive number of tokens, got {raw}")
        return Allowance(n, "configured")
    if usage is not None:
        hit = usage.last_limit("subscription")
        t = now or utcnow()
        if hit is not None and t - hit <= 5 * WEEK:
            used = usage.usage_between("subscription", hit - WEEK, hit)
            if used > 0:
                return Allowance(int(used), "calibrated_from_limit_event")
    return Allowance(ASSUMED_WEEKLY_TOKENS, "assumed_default")


class BudgetStop(Exception):
    """A cap would be exceeded (or approval is missing); no work was done for this step."""

    def __init__(self, kind: StopKind, step: str, detail: str) -> None:
        super().__init__(f"budget stop ({kind}) before {step}: {detail}")
        self.kind = kind
        self.step = step
        self.detail = detail

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "step": self.step, "detail": self.detail}


@dataclass
class BudgetGuard:
    budget: Budget
    usage: UsageSource
    allowance: Allowance
    approved_paid: bool = False
    spent_money_usd: float = 0.0  # carried over from the checkpoint when a run resumes
    spent_api_usd: float = 0.0
    overrides: dict[str, str] = field(default_factory=dict)  # explicit per-job backends (R15.5)
    clock: Callable[[], datetime] = utcnow
    log: list[dict[str, Any]] = field(default_factory=list)

    # --- backend -------------------------------------------------------------------------------
    def check_backend(self, backend: str, *, job: str) -> None:
        """Refuse a call on a backend the user didn't choose (no automatic switch, ADR-053.1)."""
        chosen = self.overrides.get(job, self.budget.llm_backend)
        if backend != chosen:
            raise BudgetStop(
                "backend",
                job,
                f"routed to {backend!r} but the brief uses {chosen!r}; pigtail never switches "
                "backends on its own (set an explicit per-job override to allow it)",
            )

    # --- money (non-LLM paid services) --------------------------------------------------------
    def check_paid(self, step: str, usd: float) -> None:
        if usd < 0:
            raise ValueError("usd must be >= 0")
        if usd == 0:
            return
        if not self.approved_paid:
            raise BudgetStop(
                "approval",
                step,
                f"costs an estimated ${usd:.2f}; paid steps need explicit approval of the "
                "estimate (pigtail brief estimate <id> --approve-paid)",
            )
        if self.spent_money_usd + usd > self.budget.money_usd + 1e-9:
            raise BudgetStop(
                "money",
                step,
                f"${self.spent_money_usd:.2f} spent + ${usd:.2f} would exceed "
                f"budget.money_usd ${self.budget.money_usd:.2f}",
            )

    def charge_paid(self, step: str, usd: float) -> None:
        self.check_paid(step, usd)
        self.spent_money_usd += usd
        self.log.append({"step": step, "kind": "money", "usd": usd, "at": self.clock().isoformat()})

    # --- LLM -----------------------------------------------------------------------------------
    def subscription_used(self) -> float:
        return self.usage.usage_since("subscription", self.clock() - WEEK)["tokens"]

    def subscription_cap(self) -> float:
        return self.budget.subscription_share * self.allowance.weekly_tokens

    def check_llm(
        self, step: str, *, est_tokens: int, backend: str | None = None, est_usd: float = 0.0
    ) -> None:
        """Before an LLM call or chunk: would it exceed the subscription share (or API cap)?"""
        b = backend or self.budget.llm_backend
        if b == "subscription":
            used = self.subscription_used()
            cap = self.subscription_cap()
            if used + est_tokens > cap:
                raise BudgetStop(
                    "subscription_share",
                    step,
                    f"estimated {used:,.0f} tokens used in the last 7 days + {est_tokens:,} "
                    f"would exceed {self.budget.subscription_share:.0%} of the weekly allowance "
                    f"({cap:,.0f} tokens; allowance basis: {self.allowance.basis}); the run "
                    "pauses and can resume when older usage leaves the 7-day window",
                )
            return
        if not self.approved_paid and est_usd > 0:
            raise BudgetStop(
                "approval", step, "LLM calls on the api backend cost money and need approval"
            )
        if self.spent_api_usd + est_usd > self.budget.llm_api_usd + 1e-9:
            raise BudgetStop(
                "api_usd",
                step,
                f"${self.spent_api_usd:.2f} spent + ${est_usd:.2f} would exceed "
                f"budget.llm_api_usd ${self.budget.llm_api_usd:.2f}",
            )

    def charge_api(self, step: str, usd: float) -> None:
        self.spent_api_usd += usd
        self.log.append({"step": step, "kind": "api", "usd": usd, "at": self.clock().isoformat()})

    def status(self) -> dict[str, Any]:
        return {
            "money_usd": {"spent": self.spent_money_usd, "cap": self.budget.money_usd},
            "llm_api_usd": {"spent": self.spent_api_usd, "cap": self.budget.llm_api_usd},
            "subscription": {
                "used_tokens_7d": self.subscription_used(),
                "cap_tokens": self.subscription_cap(),
                "share_cap": self.budget.subscription_share,
                "allowance": self.allowance.to_dict(),
            },
            "approved_paid": self.approved_paid,
            "label": "estimate",
        }
