"""LLM relevance filter (PRD R4.6, R4.10; R15.8-R15.10; Directive §6.3, ADR-064.3).

Every candidate of a brief version is judged against a **written rubric derived from the brief**
(`build_rubric`: core field, include and exclude boundaries, the declared widening steps, target
users and the problem statement). The rubric is versioned by its content hash
(`rubric-v1-<sha12>`) and sent as the cached prefix of the prompt (`PromptSpec.context`), the
prompt is versioned (`relevance_filter` v1) and the job runs on the relevance stage's model
(Haiku, R15.8) through the Message Batches API (R15.9), about 20 candidates per request.

Per candidate the model returns a verdict (`relevant`, `not_relevant`, `uncertain`), a reason
(at most 30 words; longer reasons are cut and flagged), the distance from the core field (0 =
core, 1 and 2 = the brief's widening steps, R4.10) and the panel (`field`, `exemplar`,
`reference`; a project named in the brief keeps the panel the brief gives it, and the model's
answer is kept as `model_panel`). Each verdict is stored on the candidate with its provenance:
rubric version, prompt id, version and fingerprint, model, backend, batch id, input hash, trim
and redaction versions, and the request (chunk) it came from.

**Inputs are public repo metadata only** (R4.6): the repo *name* without its owner, the owner
type, the description, topics, language and a trimmed README excerpt (R15.10). The owner login
is removed from every field in memory (`[owner]`), and `LLMClient` then redacts identifiers with
per-call aliases (`alias-v1`), so no handle reaches the model, the cache or the logs.

**Resumable, no double spend.** The chunk plan is stored in the run's checkpoint, so a resumed
run rebuilds exactly the same requests: requests already answered come from the LLM result
cache, and batches still in flight are collected by the batch id stored in `llm_batches`
instead of being submitted again. Each group of requests passes a per-request cost estimate to
`BudgetGuard.before_submit`, which stops the stage before a batch that would cross the brief's
or the month's cap (R15.11).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from pigtail.briefs.candidates import Candidate, CandidateStore, strip_owner
from pigtail.briefs.model import Brief, sha256_json
from pigtail.llm import BatchItem, LLMClient, PromptSpec
from pigtail.llm.client import BeforeSubmit
from pigtail.llm.pricing import TokenUsage, cost_usd
from pigtail.llm.trim import TrimPolicy, trim_text

JOB = "relevance_filter"
PROMPT_ID = "relevance_filter"
PROMPT_VERSION = "1"
RUBRIC_PREFIX = "rubric-v1"
CHUNK = 20  # candidates per request
GROUP = 25  # requests per batch submission (the budget check runs per group)
REASON_MAX_WORDS = 30
README_CHARS = 1_200
OUTPUT_TOKENS_PER_CANDIDATE = 70
README_POLICY = TrimPolicy(
    excerpt_above_chars=README_CHARS, max_item_chars=README_CHARS, max_total_chars=README_CHARS
)

SYSTEM = (
    "You screen open-source projects for a research study of how projects in one field grow. "
    "For each candidate, decide whether it belongs to the field described in the rubric, using "
    "only the public metadata given (name, description, topics, language, README excerpt). "
    "verdict: relevant if it clearly fits the core field or one of the widening steps and none "
    "of the exclusions; not_relevant if it clearly does not; uncertain if the metadata is not "
    "enough to tell. distance: 0 for the core field, 1 for the first widening step, 2 for the "
    "second or a later one (use the closest that fits; for not_relevant, the closest anyway). "
    "panel: 'reference' or 'exemplar' only when the candidate's named_in_brief says so, "
    "otherwise 'field'. reason: at most 30 words, about the project only, never about people. "
    "Return exactly one verdict per candidate id. Reply only through the requested JSON schema."
)
TEMPLATE = (
    "Candidates as JSON (one object per candidate; `id` is local to this request):\n\n{input}\n\n"
    "Judge every candidate against the rubric."
)


class CandidateVerdict(BaseModel):
    id: str = Field(description="The candidate's id from the input")
    verdict: Literal["relevant", "not_relevant", "uncertain"]
    reason: str = Field(description="At most 30 words, about the project only")
    distance: Literal[0, 1, 2] = Field(description="0 core field; 1-2 widening steps")
    panel: Literal["field", "exemplar", "reference"]


class RelevanceOutput(BaseModel):
    verdicts: list[CandidateVerdict]


# --- rubric and prompt ----------------------------------------------------------------------
def build_rubric(brief: Brief) -> str:
    """The written rubric (R4.6): field boundaries, widening steps, target users and problem.
    Nothing else from the brief (no project name, context, reference names, audience or budget)."""
    f = brief.field
    tu = brief.project.target_users
    problem = (
        brief.expansion.problem_statement
        if brief.expansion is not None and brief.expansion.problem_statement
        else brief.project.description
    )
    lines = [f"Core field (distance 0): {f.core_field}", "Include:"]
    lines += [f"- {x}" for x in f.include]
    lines.append("Exclude (never relevant):")
    lines += [f"- {x}" for x in f.exclude] or ["- (none)"]
    if f.widening_steps:
        lines.append("Widening steps (adjacent fields, in order):")
        lines += [f"- distance {min(i + 1, 2)}: {x}" for i, x in enumerate(f.widening_steps)]
    else:
        lines.append("Widening steps: none declared (anything outside the core is not_relevant).")
    lines.append(f"Target users: {tu.primary}" + (f"; also {tu.secondary}" if tu.secondary else ""))
    lines.append(f"Problem the user's project addresses: {' '.join(problem.split())}")
    return "\n".join(lines)


def rubric_version(rubric: str) -> str:
    return f"{RUBRIC_PREFIX}-{sha256_json(rubric)[:12]}"


def prompt_for(brief: Brief) -> tuple[PromptSpec, str]:
    rubric = build_rubric(brief)
    rv = rubric_version(rubric)
    return (
        PromptSpec(
            id=PROMPT_ID,
            version=PROMPT_VERSION,
            system=SYSTEM,
            template=TEMPLATE,
            context=f"RUBRIC {rv}\n{rubric}",
        ),
        rv,
    )


# --- inputs ---------------------------------------------------------------------------------
_IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_TAG = re.compile(r"<[^>]{0,500}>")
_BADGE_LINE = re.compile(r"^\s*(\[!\[.*|<img .*|<a .*)$", re.M)


def clean_readme(raw: bytes) -> str:
    """Markdown/HTML README -> plain text (badges, images and tags removed, links kept as text)."""
    text = raw.decode("utf-8", errors="replace")
    text = _BADGE_LINE.sub(" ", text)
    text = _IMG.sub(" ", text)
    text = _LINK.sub(r"\1", text)
    text = _TAG.sub(" ", text)
    text = re.sub(r"[#*_`>|=~-]{2,}", " ", text)
    return " ".join(text.split())


def candidate_input(c: Candidate, local_id: str, readme_excerpt: str | None) -> dict[str, Any]:
    """What the model sees about one candidate: public project metadata, no owner login."""
    full = c.repo_full_name or ""
    owner, _, name = full.partition("/")
    m = c.metadata
    return {
        "id": local_id,
        "name": strip_owner(name, owner),
        "owner_type": m.get("owner_type") or "unknown",
        "description": strip_owner(m.get("description"), owner),
        "topics": [t for t in m.get("topics") or [] if isinstance(t, str)][:20],
        "language": m.get("language"),
        "readme_excerpt": strip_owner(readme_excerpt, owner),
        "named_in_brief": c.panel if c.is_named else None,
    }


def readme_excerpt(raw: bytes | None, terms: Sequence[str]) -> tuple[str | None, str | None]:
    """(excerpt, trim version) of a README (R15.10: relevant excerpts, cut to 1,200 chars)."""
    if raw is None:
        return None, None
    t = trim_text(clean_readme(raw), terms=terms, policy=README_POLICY)
    return (t.text or None), t.report.version


def chunk_refs(refs: Sequence[str], size: int = CHUNK) -> list[list[str]]:
    return [list(refs[i : i + size]) for i in range(0, len(refs), size)]


def cut_reason(reason: str) -> tuple[str, bool]:
    words = reason.split()
    if len(words) <= REASON_MAX_WORDS:
        return " ".join(words), False
    return " ".join(words[:REASON_MAX_WORDS]) + " …", True


def est_request_usd(
    model: str, prompt: PromptSpec, input_text: str, n: int, *, batch: bool
) -> float | None:
    """List-price estimate of one request (chars / 4 tokens; no cache hit assumed)."""
    tin = (len(prompt.system) + len(prompt.context) + len(prompt.template) + len(input_text)) // 4
    return cost_usd(
        model, TokenUsage(input=tin + 50, output=n * OUTPUT_TOKENS_PER_CANDIDATE), batch=batch
    )


# --- stage ----------------------------------------------------------------------------------
@dataclass
class RelevanceResult:
    rubric_version: str = ""
    judged: int = 0
    already_judged: int = 0
    requests: int = 0
    missing: int = 0
    failed_requests: int = 0
    reasons_cut: int = 0
    batch_ids: list[str] = field(default_factory=list)
    verdicts: dict[str, int] = field(default_factory=dict)
    readmes_fetched: int = 0
    readme_evidence: list[str] = field(default_factory=list)
    est_usd: float | None = 0.0

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d.pop("readme_evidence")
        return d


ReadmeLoader = Callable[[Candidate], tuple[bytes | None, str | None]]


@dataclass
class Relevance:
    """One run of the relevance stage for one brief version (see the module docstring).

    `readme` returns (raw README bytes or None, evidence id or None) for a candidate: from the
    snapshot of an earlier fetch, else fetched now (the runner wires the GitHub connector)."""

    brief: Brief
    client: LLMClient
    store: CandidateStore
    brief_run_id: str | None
    checkpoint: dict[str, Any]
    save: Callable[[dict[str, Any]], None]
    readme: ReadmeLoader
    before_submit: BeforeSubmit | None = None
    on_group: Callable[[], None] = lambda: None  # refresh spend after each group
    group: int = GROUP
    poll_seconds: float = 60.0
    timeout_seconds: float | None = None
    sleep: Callable[[float], None] | None = None
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    result: RelevanceResult = field(default_factory=RelevanceResult)

    def pending(self, rv: str) -> list[Candidate]:
        out = []
        for c in self.store.all():
            if c.repo_full_name is None:
                continue  # an unresolved named project: nothing to judge until confirmed
            if c.verdict is not None and c.rubric_version == rv:
                continue
            out.append(c)
        return out

    def _plan(self, rv: str, pending: list[Candidate]) -> list[list[str]]:
        plan = self.checkpoint.get("plan")
        if not plan or plan.get("rubric_version") != rv:
            plan = {
                "rubric_version": rv,
                "chunks": chunk_refs([c.ref for c in pending]),
                "done": [],
                "retry": 0,
            }
            self.checkpoint["plan"] = plan
            self.save(self.checkpoint)
        return list(plan["chunks"])

    def run_stage(self) -> RelevanceResult:
        prompt, rv = prompt_for(self.brief)
        self.result.rubric_version = rv
        terms = [
            *(self.brief.expansion.keywords if self.brief.expansion else []),
            self.brief.field.core_field,
        ]
        backend = self.client.backend_for(JOB)
        model = self.client.model_for(JOB)
        batch = self.client.batches_for(JOB)
        self.result.already_judged = sum(
            1
            for c in self.store.all()
            if c.repo_full_name is not None and c.verdict is not None and c.rubric_version == rv
        )
        for _attempt in range(2):  # the plan, then one retry pass for missing verdicts
            pending = {c.ref: c for c in self.pending(rv)}
            if not pending:
                break
            chunks = self._plan(rv, list(pending.values()))
            plan = self.checkpoint["plan"]
            todo = [(i, ch) for i, ch in enumerate(chunks) if i not in plan["done"]]
            for g in range(0, len(todo), self.group):
                group = todo[g : g + self.group]
                items: list[BatchItem] = []
                refs_of: dict[str, list[str]] = {}
                est_max: float | None = 0.0
                for idx, refs in group:
                    live = [r for r in refs if r in pending]
                    payload = []
                    for k, ref in enumerate(live):
                        c = pending[ref]
                        raw, ev = self.readme(c)
                        if ev is not None and ev not in self.result.readme_evidence:
                            self.result.readme_evidence.append(ev)
                        excerpt, _tv = readme_excerpt(raw, terms)
                        payload.append(candidate_input(c, f"c{k + 1:02d}", excerpt))
                    if not payload:
                        plan["done"].append(idx)
                        continue
                    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                    ref = f"chunk{idx:04d}"
                    items.append(BatchItem(ref=ref, input_text=text, namespace="github"))
                    refs_of[ref] = live
                    if backend.name == "api":
                        e = est_request_usd(model, prompt, text, len(live), batch=batch)
                        est_max = None if e is None or est_max is None else max(est_max, e)
                if not items:
                    self.save(self.checkpoint)
                    continue
                if backend.name != "api":
                    est_max = 0.0  # subscription: no money (its usage limits pause the queue)
                kw: dict[str, Any] = {"poll_seconds": self.poll_seconds}
                if self.sleep is not None:
                    kw["sleep"] = self.sleep
                run = self.client.run_batch(
                    prompt,
                    items,
                    RelevanceOutput,
                    job=JOB,
                    brief_run_id=self.brief_run_id,
                    before_submit=self.before_submit,
                    est_usd_per_item=est_max,
                    timeout_seconds=self.timeout_seconds,
                    **kw,
                )
                self.result.requests += len(items)
                for bid in run.batch_ids:
                    if bid not in self.result.batch_ids:
                        self.result.batch_ids.append(bid)
                for ref, res in run.results.items():
                    self._store(ref, refs_of[ref], res, rv)
                self.result.failed_requests += len(run.failed)
                for idx, _refs in group:
                    if f"chunk{idx:04d}" not in run.failed and idx not in plan["done"]:
                        plan["done"].append(idx)
                self.save(self.checkpoint)
                self.on_group()
            # every chunk of this plan is done: a retry pass only for verdicts the model missed
            if plan.get("retry", 0) >= 1 or self.result.failed_requests:
                break
            left = self.pending(rv)
            if not left:
                break
            self.checkpoint["plan"] = {
                "rubric_version": rv,
                "chunks": chunk_refs([c.ref for c in left]),
                "done": [],
                "retry": 1,
            }
            self.save(self.checkpoint)
        self.result.missing = len(self.pending(rv))
        return self.result

    def _store(self, chunk_ref: str, refs: list[str], res: Any, rv: str) -> None:
        by_id = {f"c{k + 1:02d}": ref for k, ref in enumerate(refs)}
        prov = res.provenance()
        seen: set[str] = set()
        for v in res.output.verdicts:
            ref = by_id.get(v.id)
            if ref is None or ref in seen:
                continue  # an id we didn't send, or a duplicate: ignored (counted as missing)
            seen.add(ref)
            reason, was_cut = cut_reason(v.reason)
            self.result.reasons_cut += int(was_cut)
            c = self.store.get(ref)
            panel = c.panel if c is not None and c.is_named else "field"
            self.store.set_verdict(
                ref,
                verdict=v.verdict,
                reason=reason,
                distance=int(v.distance),
                model_panel=v.panel,
                rubric_version=rv,
                provenance={
                    **prov,
                    "rubric_version": rv,
                    "chunk": chunk_ref,
                    "reason_cut": was_cut,
                    "panel_source": "brief" if panel != "field" else "model_or_default",
                    "cached": bool(res.cached),
                },
                judged_at=self.clock(),
                brief_run_id=self.brief_run_id,
            )
            self.result.judged += 1
            self.result.verdicts[v.verdict] = self.result.verdicts.get(v.verdict, 0) + 1


def requests_for(candidates: int) -> int:
    return math.ceil(candidates / CHUNK) if candidates else 0
