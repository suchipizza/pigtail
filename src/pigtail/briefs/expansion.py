"""LLM expansion of a brief (PRD R18.7; D7 "LLM expansion").

From the brief's own words, the model **proposes** a problem statement, users, keywords, topics,
competitors (names and optional URLs), GitHub topic suggestions and search queries. The
proposal is never saved by this module's `propose_expansion`: the user reviews and edits it,
and only accepting it (`apply_expansion`, the CLI's `--accept`, or saving the edited form in
`/briefs`) stores a new brief version with the `expansion` block and its provenance.

What the model sees, and nothing else: `project.description`, `project.target_users`,
`project.business_model`, `field.include` and `field.exclude` (`expansion_input`). The project
name, context, seed projects, reference cases, audience, budget and notes are never sent. The
input goes through `LLMClient` (identifier stripping, cache, usage ledger; PRD F15) as job
`brief_expansion` (stage `synthesis`, a standard call: it is interactive; R15.8), after
`BudgetGuard` has checked the backend (never switched automatically, ADR-053.1) and, on the
`api` backend, approval and the brief's and the month's money caps (R15.11, ADR-072.4).

Model output is a proposal from a language model, not evidence: competitors and URLs are
unverified until the user checks them, and every proposal says so.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from pigtail.briefs.budget import BudgetGuard, BudgetStop
from pigtail.briefs.model import (
    Brief,
    BriefInvalid,
    BriefProblem,
    Expansion,
    ExpansionProvenance,
    problems_of,
    validate_brief,
)
from pigtail.briefs.store import BriefStore, StoredBrief
from pigtail.llm import LLMClient, PromptSpec

JOB = "brief_expansion"

EXPANSION_PROMPT = PromptSpec(
    id="brief_expansion",
    version="1",
    system=(
        "You help a user describe the neighbourhood of their own open-source project so that a "
        "research tool can find comparable projects. You only propose; the user reviews and "
        "edits everything. Use only what the user wrote and your general knowledge. Do not "
        "invent facts about the user's project. Name a competitor only if you believe it "
        "exists; give its URL only if you are confident of it, otherwise null. Reply only "
        "through the requested JSON schema."
    ),
    template=(
        "The user's description of their project and its field, as JSON:\n\n{input}\n\n"
        "Propose:\n"
        "- problem_statement: one or two sentences on the problem the project solves.\n"
        "- users: up to 8 kinds of users who have that problem.\n"
        "- keywords: up to 15 short phrases people use when searching for such tools.\n"
        "- topics: up to 10 subject areas, in words.\n"
        "- competitors: up to 15 existing open-source or commercial projects that solve the same "
        "problem for the same users, each with a name and a homepage or repository URL (or "
        "null). Respect the include and exclude boundaries.\n"
        "- github_topics: up to 15 GitHub topic slugs (lowercase letters, digits and dashes) "
        "that comparable repositories are likely to use.\n"
        "- search_queries: up to 10 search queries for finding comparable projects on GitHub "
        "and developer forums."
    ),
)

# (input, output) tokens assumed for one expansion call; shared with the run estimate.
EXPANSION_TOKENS = (3_000, 1_500)

MAX_ITEMS = {
    "users": 20,
    "keywords": 40,
    "topics": 40,
    "competitors": 40,
    "github_topics": 40,
    "search_queries": 40,
}
_TOPIC = re.compile(r"^[a-z0-9][a-z0-9-]{0,49}$")
_URL = re.compile(r"^https?://\S+$")

UNVERIFIED_NOTE = (
    "This is a proposal from a language model, not evidence: check every competitor and URL "
    "before accepting. Nothing is saved until you accept it."
)


class ProposedCompetitor(BaseModel):
    name: str = Field(description="The project's name.")
    url: str | None = Field(description="Homepage or repository URL, or null if unsure.")


class ExpansionOutput(BaseModel):
    """Structured output the model must return (schema sent to the backend, closed)."""

    problem_statement: str
    users: list[str]
    keywords: list[str]
    topics: list[str]
    competitors: list[ProposedCompetitor]
    github_topics: list[str] = Field(description="GitHub topic slugs")
    search_queries: list[str]


def expansion_input(brief: Brief) -> str:
    """The only brief content sent to the model (R18.7; see the module docstring)."""
    p = brief.project
    return json.dumps(
        {
            "description": p.description,
            "target_users": p.target_users.model_dump(mode="json", exclude_none=True),
            "business_model": p.business_model,
            "field_include": list(brief.field.include),
            "field_exclude": list(brief.field.exclude),
        },
        ensure_ascii=False,
        indent=2,
    )


def _clean(items: list[str], limit: int, max_len: int = 500) -> tuple[list[str], int]:
    out: list[str] = []
    seen: set[str] = set()
    dropped = 0
    for raw in items:
        s = " ".join(str(raw).split())[:max_len]
        if not s or s.lower() in seen:
            dropped += 1 if s else 0
            continue
        seen.add(s.lower())
        out.append(s)
    if len(out) > limit:
        dropped += len(out) - limit
        out = out[:limit]
    return out, dropped


def _slug(s: str) -> str:
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9-]+", "-", s.strip().lower())).strip("-")[:50]


def normalize_output(out: ExpansionOutput) -> tuple[dict[str, Any], list[str]]:
    """Fit the model's output to the brief schema; say what was changed or dropped."""
    notes: list[str] = []
    fields: dict[str, Any] = {}
    ps = " ".join(out.problem_statement.split())[:2000]
    fields["problem_statement"] = ps or None
    for name in ("users", "keywords", "topics", "search_queries"):
        vals, dropped = _clean(getattr(out, name), MAX_ITEMS[name])
        fields[name] = vals
        if dropped:
            notes.append(f"{name}: {dropped} duplicate or surplus item(s) dropped")
    topics: list[str] = []
    bad = 0
    for t in out.github_topics:
        slug = _slug(t)
        if _TOPIC.match(slug) and slug not in topics:
            topics.append(slug)
        elif not _TOPIC.match(slug):
            bad += 1
    if bad:
        notes.append(f"github_topics: {bad} suggestion(s) that aren't valid topic slugs dropped")
    fields["github_topics"] = topics[: MAX_ITEMS["github_topics"]]
    comps: list[dict[str, Any]] = []
    names: set[str] = set()
    bad_urls = 0
    for c in out.competitors:
        name = " ".join(c.name.split())[:200]
        if not name or name.lower() in names:
            continue
        names.add(name.lower())
        url = (c.url or "").strip() or None
        if url is not None and (not _URL.match(url) or len(url) > 500):
            url = None
            bad_urls += 1
        comps.append({"name": name, "url": url})
    if bad_urls:
        notes.append(f"competitors: {bad_urls} URL(s) that aren't http(s) links removed")
    fields["competitors"] = comps[: MAX_ITEMS["competitors"]]
    return fields, notes


@dataclass(frozen=True)
class ExpansionProposal:
    """A proposal shown to the user. It is never stored as a brief version by itself."""

    brief_id: str
    base_version: int
    expansion: Expansion
    cached: bool
    est_tokens: int
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": "proposal",
            "saved": False,
            "brief_id": self.brief_id,
            "base_version": self.base_version,
            "expansion": self.expansion.model_dump(mode="json", exclude_none=True),
            "cached": self.cached,
            "est_tokens": self.est_tokens,
            "notes": [UNVERIFIED_NOTE, *self.notes],
        }


def make_guard(
    client: LLMClient,
    brief: Brief,
    *,
    approved_paid: bool = False,
    month_cap_usd: float | None = None,
    spent_usd: float = 0.0,
    brief_ledger: Callable[[], float] | None = None,
) -> BudgetGuard:
    """A `BudgetGuard` on this install's usage ledger, with the client's explicit overrides.
    `spent_usd` is what the brief has already spent (cost ledger); `month_cap_usd` defaults to
    `BUDGET_USD_MONTH`. `brief_ledger` re-reads the brief's API total from the cost ledger
    before every money check, as the runner's guard does (ADR-078.6), so spend recorded after
    the guard was built (another run, another tab) counts."""
    if month_cap_usd is None:
        from pigtail.config import Settings

        month_cap_usd = Settings.from_env().budget_usd_month
    return BudgetGuard(
        budget=brief.budget,
        usage=client.store,
        month_cap_usd=month_cap_usd,
        approved_paid=approved_paid,
        spent_usd=spent_usd,
        overrides=dict(client.overrides),
        brief_ledger=brief_ledger,
    )


def propose_expansion(
    brief: Brief,
    client: LLMClient,
    guard: BudgetGuard,
    *,
    now: datetime | None = None,
) -> ExpansionProposal:
    """Ask the model for an expansion proposal (one call, or none on a cache hit).

    Raises `BudgetStop` before any call if the backend isn't the brief's (and has no explicit
    per-job override), or, on `api`, the call isn't approved or would exceed the brief's or the
    month's money cap. LLM errors
    (`QueuePaused`, `UsageLimitReached`, `BackendError`, `StructuredOutputError`) propagate.
    """
    if brief.version is None:
        raise ValueError("propose an expansion for a stored brief version")
    backend = client.backend_for(JOB).name
    guard.check_backend(backend, job=JOB)
    tin, tout = EXPANSION_TOKENS
    est_usd: float | None = 0.0
    if backend == "api":
        from pigtail.llm.pricing import TokenUsage, cost_usd

        model = client.model_for(JOB)
        # None (unknown list price) is still a paid call: it needs explicit approval.
        est_usd = cost_usd(model, TokenUsage(tin, tout))
        if est_usd is None and not guard.approved_paid:
            raise BudgetStop(
                "approval",
                JOB,
                f"LLM calls on the api backend cost money (price of {model!r} unknown) "
                "and need approval",
            )
    guard.check_llm(JOB, est_tokens=tin + tout, backend=backend, est_usd=est_usd)
    res = client.complete(
        EXPANSION_PROMPT, expansion_input(brief), ExpansionOutput, job=JOB, namespace="brief"
    )
    if backend == "api" and not res.cached:
        guard.charge_api(JOB, est_usd or 0.0)
    fields, notes = normalize_output(res.output)
    draft = Expansion.model_validate({**fields, "generated_by": "llm"})
    prov = ExpansionProvenance(
        prompt_id=res.prompt_id,
        prompt_version=res.prompt_version,
        prompt_fingerprint=res.prompt_fingerprint,
        model=res.model,
        backend=res.backend,
        input_hash=res.input_hash,
        proposal_hash=draft.fields_hash(),
        generated_at=(now or datetime.now(UTC)).replace(microsecond=0),
        based_on_version=brief.version,
    )
    expansion = Expansion.model_validate(
        {**draft.model_dump(mode="json"), "provenance": prov.model_dump(mode="json")}
    )
    return ExpansionProposal(
        brief_id=brief.brief_id,
        base_version=brief.version,
        expansion=expansion,
        cached=res.cached,
        est_tokens=tin + tout,
        notes=notes,
    )


def parse_expansion(data: Any) -> Expansion:
    """Validate an (edited) expansion; problems name the field as `expansion.<path>`."""
    if not isinstance(data, dict):
        raise BriefInvalid([BriefProblem("expansion", "must be a mapping of fields")])
    try:
        return Expansion.model_validate(data)
    except ValidationError as e:
        probs = problems_of(e)
        raise BriefInvalid(
            [BriefProblem(_under_expansion(p.path), p.message) for p in probs]
        ) from None


def _under_expansion(path: str) -> str:
    return "expansion" if path == "(brief)" else f"expansion.{path}"


def apply_expansion(
    store: BriefStore, brief_id: str, expansion: Any, *, base_version: int
) -> tuple[StoredBrief, bool]:
    """Accept an (edited) proposal: save a new version of the brief with this expansion.

    The expansion is applied to `base_version`, and the save is refused (`VersionConflict`)
    if that is no longer the latest version, so accepting never reverts a newer edit.
    """
    exp = parse_expansion(expansion)
    base = store.get(brief_id, base_version)
    data = base.brief.model_dump(mode="json")
    data["expansion"] = exp.model_dump(mode="json")
    return store.save_version(validate_brief(data), base_version=base_version)
