"""Budget hard stop for brief runs (PRD R15.11, R18.5; Directive §6.4, ADR-064.4, ADR-072.4).

Two money caps, both hard stops:

- **Per brief:** `budget.money_usd` is the brief's total money cap over all its runs, API spend
  included (brief schema v1.2; the owner set USD 150 for her first full brief). Spend already
  recorded for the brief (the cost ledger, carried over when a run resumes) counts against it.
- **Per month:** `BUDGET_USD_MONTH` (default USD 200) caps the instance's API spend in the
  current calendar month (UTC), measured from the usage ledger, plus any other paid step this
  guard charged.

Every paid step (an API call or batch, or an enabled paid service) needs the user's **explicit
approval** of the estimate (`approved_paid=True`, from `pigtail brief estimate --approve-paid`
or the D7 run dialog). A refused check raises `BudgetStop` whose message says that going on
needs H6 (the owner's approval of spend above the caps); the stage writes a resumable
checkpoint (`pigtail.briefs.cache.BriefRun`) and the run ends with status `paused_budget`, never
with partial silent spending.

The `subscription` backend costs no money. `budget.subscription_share` applies only to the
agents building pigtail (ADR-064.4) and is not enforced on product calls; usage limits on that
backend pause the queue (R15.5, `LLMClient`). `resolve_allowance` is kept for the agents'
allowance reporting. The guard **never switches backends**: a call routed to a backend other
than the brief's `llm_backend` is refused unless the user set an explicit per-job override
(R15.5).

Stages call `check_*` before a unit of work and `charge_*` after it.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

from pigtail.briefs.model import Budget

WEEK = timedelta(days=7)
# Used only when the allowance is neither configured nor calibrated (agents' allowance
# reporting; ADR-055.2). An assumption, not a measurement.
ASSUMED_WEEKLY_TOKENS = 5_000_000
ALLOWANCE_ENV = "PIGTAIL_SUBSCRIPTION_WEEKLY_TOKENS"
H6 = (
    "going on needs H6, the owner's approval of spend above the caps (Directive §6.4); the "
    "run paused with a resumable checkpoint"
)

StopKind = Literal["money", "month", "approval", "backend"]
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


def month_start(now: datetime) -> datetime:
    """Start of the calendar month (UTC) the monthly cap is measured over."""
    n = now.astimezone(UTC)
    return n.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


@dataclass
class BudgetGuard:
    budget: Budget
    usage: UsageSource
    month_cap_usd: float = 200.0
    approved_paid: bool = False
    # Money already spent on this brief (earlier runs and, on resume, this run's checkpoint).
    spent_usd: float = 0.0
    overrides: dict[str, str] = field(default_factory=dict)  # explicit per-job backends (R15.5)
    clock: Callable[[], datetime] = utcnow
    log: list[dict[str, Any]] = field(default_factory=list)
    # Non-API money charged by this guard this month (not in the LLM usage ledger).
    paid_this_month_usd: float = 0.0

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

    # --- the two caps --------------------------------------------------------------------------
    def month_spent(self) -> float:
        """API spend this calendar month (usage ledger) plus other paid steps charged here."""
        api = self.usage.usage_since("api", month_start(self.clock()))["cost_usd"]
        return api + self.paid_this_month_usd

    def _check_money(self, step: str, usd: float | None, what: str) -> None:
        if usd is not None and usd < 0:
            raise ValueError("usd must be >= 0")
        if usd == 0:
            return
        shown = "an unknown amount" if usd is None else f"an estimated ${usd:.2f}"
        if not self.approved_paid:
            raise BudgetStop(
                "approval",
                step,
                f"{what} costs {shown}; paid steps need explicit approval of the estimate "
                "(pigtail brief estimate <id> --approve-paid)",
            )
        if usd is None:
            return  # approved although the amount is unknown (caps are re-checked on charge)
        cap = self.budget.money_usd
        if self.spent_usd + usd > cap + 1e-9:
            raise BudgetStop(
                "money",
                step,
                f"${self.spent_usd:.2f} spent on this brief + ${usd:.2f} would exceed "
                f"budget.money_usd ${cap:.2f} (the brief's total cap, API included); {H6}",
            )
        month = self.month_spent()
        if month + usd > self.month_cap_usd + 1e-9:
            raise BudgetStop(
                "month",
                step,
                f"${month:.2f} spent this month + ${usd:.2f} would exceed the monthly cap "
                f"BUDGET_USD_MONTH ${self.month_cap_usd:.2f}; {H6}",
            )

    # --- paid services other than the API ------------------------------------------------------
    def check_paid(self, step: str, usd: float) -> None:
        self._check_money(step, usd, "this step")

    def charge_paid(self, step: str, usd: float) -> None:
        self.check_paid(step, usd)
        self.spent_usd += usd
        self.paid_this_month_usd += usd
        self.log.append({"step": step, "kind": "money", "usd": usd, "at": self.clock().isoformat()})

    # --- LLM -----------------------------------------------------------------------------------
    def check_llm(
        self, step: str, *, est_usd: float | None, backend: str | None = None, est_tokens: int = 0
    ) -> None:
        """Before an LLM call or batch: approval and both caps on `api`; nothing on
        `subscription` (no money; its usage limits pause the queue, R15.5)."""
        b = backend or self.budget.llm_backend
        if b == "subscription":
            return
        self._check_money(step, est_usd, "LLM work on the api backend")

    def charge_api(self, step: str, usd: float) -> None:
        """Record actual API spend (the usage ledger already holds it for the monthly cap)."""
        self.spent_usd += usd
        self.log.append({"step": step, "kind": "api", "usd": usd, "at": self.clock().isoformat()})

    def before_submit(self, job: str, requests: int, est_usd: float | None) -> None:
        """`LLMClient.run_batch` hook: the budget check before a batch is submitted."""
        self.check_llm(f"{job} batch of {requests}", est_usd=est_usd, backend="api")

    def status(self) -> dict[str, Any]:
        return {
            "brief_usd": {"spent": round(self.spent_usd, 6), "cap": self.budget.money_usd},
            "month_usd": {"spent": round(self.month_spent(), 6), "cap": self.month_cap_usd},
            "approved_paid": self.approved_paid,
            "subscription_share": {
                "value": self.budget.subscription_share,
                "applies_to": "agents building pigtail only (ADR-064.4)",
            },
            "label": "estimate",
        }
