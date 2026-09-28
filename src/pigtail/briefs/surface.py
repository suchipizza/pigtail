"""Distribution surface: a balance variable coded before any outcome (ADR-084; owner decision
2026-09-27, option 1: "a balance variable 'distribution surface', coded before looking at
outcomes").

Per shortlisted repo, a Haiku classification (job `distribution_surface`, the relevance stage's
model, prompt `distribution_surface` v1) codes:

- `surface`: one of `SURFACES` (`mcp_server`, `cli`, `library`, `editor_or_agent_plugin`,
  `hosted_app`, `other`, `unknown`);
- `install_paths`: a set from `INSTALL_PATHS` (`npx`, `pip`, `brew`, `binary`, `marketplace`,
  `other`, `unknown`).

**Input: public project-level text only**, the same as the relevance filter's (R4.6): the repo
name without its owner, description, topics, primary language and the README excerpt the
relevance filter uses (`relevance.readme_excerpt`: cleaned and trimmed to 1,200 characters);
the owner login is replaced by `[owner]` in memory and `LLMClient` then redacts identifiers
with per-call aliases. **Output:** a strict JSON schema (`SurfaceOutput`: enums only, no free
text), so nothing but the two labels is stored.

**When.** It is the first step of the selection stage (`selection_store.run_stage`), before the
launch lookup, the GitHub releases, the mention search and the star-history fetch, and it reads
nothing those steps produce: no star data, anchor or outcome value exists for the brief version
when a repo is coded. Its inputs are the shortlist's own stored metadata and README excerpts.

**Paid step, fail closed.** Batched 20 repos per request (`CHUNK`, like relevance) and 25
requests per batch submission (`GROUP`), through `LLMClient.run_batch` (Batch API on `api`),
each group budget-checked by `BudgetGuard.before_submit` (approval with `--approve-paid`, the
brief's and the month's caps, no backend switch through `check_backend`), cached by the LLM
cache (a cached answer needs no approval), counted in the estimate. Without a client
(`SurfaceCodingUnavailable`), without approval or over a cap (`BudgetStop` propagates) the
selection is **refused**: it never runs without the coding. A batch still running raises
`BatchPending` (the stage waits and resumes). A repo the model leaves out gets one retry
request; if it is still missing, it is stored as `unknown` with `missing: true` (the coding
ran; its answer for that repo is unknown).

**Stored per repo** (candidate metadata `distribution_surface`, so purge, export and inventory
of `brief_candidate` cover it): `surface`, `install_paths` (sorted), `coded_by` (model, backend,
prompt id, version and fingerprint, schema hash, batch id, cached), `coded_at` and `rule`
(`SURFACE_VERSION`). A repo already coded under the same prompt fingerprint and model is not
coded again (its row is kept, with its original `coded_at`).
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from pigtail.briefs.candidates import Candidate, CandidateStore, strip_owner
from pigtail.briefs.selection import SelectionError
from pigtail.llm import BatchItem, LLMClient, PromptSpec
from pigtail.llm.pricing import TokenUsage, cost_usd
from pigtail.llm.types import schema_hash, sha256_text

SURFACE_VERSION = "surface-v1"
JOB = "distribution_surface"
PROMPT_ID = "distribution_surface"
PROMPT_VERSION = "1"
NAMESPACE = "github"
CHUNK = 20  # repos per request (as the relevance filter)
GROUP = 25  # requests per batch submission (the budget check runs per group)
OUTPUT_TOKENS_PER_REPO = 25
METADATA_KEY = "distribution_surface"

SURFACES = (
    "mcp_server",
    "cli",
    "library",
    "editor_or_agent_plugin",
    "hosted_app",
    "other",
    "unknown",
)
INSTALL_PATHS = ("npx", "pip", "brew", "binary", "marketplace", "other", "unknown")

Surface = Literal[
    "mcp_server", "cli", "library", "editor_or_agent_plugin", "hosted_app", "other", "unknown"
]
InstallPath = Literal["npx", "pip", "brew", "binary", "marketplace", "other", "unknown"]

SYSTEM = (
    "You classify open-source projects by how they reach their users, using only the public "
    "project facts given (name, description, topics, primary language, README excerpt). "
    "surface: 'mcp_server' for a Model Context Protocol server; 'cli' for a command-line tool; "
    "'library' for a package other code imports (SDK, framework, module); "
    "'editor_or_agent_plugin' for an extension or plugin of an editor, IDE or AI agent; "
    "'hosted_app' for a web or desktop application or service people use directly; 'other' "
    "for anything else; 'unknown' when the facts are not enough. If several fit, choose the "
    "one the README presents first as the way to use it. install_paths: every documented way "
    "to install or run it among 'npx' (npx, npm, pnpm, yarn or bunx), 'pip' (pip, pipx, uv, "
    "uvx or poetry from PyPI), 'brew' (Homebrew), 'binary' (a downloadable release binary or "
    "installer script), 'marketplace' (an editor, browser or agent extension marketplace), "
    "'other' (any other way, e.g. cargo, go install, docker, source build); ['unknown'] when "
    "none is stated. Never guess from the language alone. Return exactly one item per "
    "project id. Reply only through the requested JSON schema."
)
TEMPLATE = (
    "Projects as JSON (one object per project; `id` is local to this request):\n\n{input}\n\n"
    "Classify every project."
)
PROMPT = PromptSpec(id=PROMPT_ID, version=PROMPT_VERSION, system=SYSTEM, template=TEMPLATE)

SURFACE_RULE = (
    f"{SURFACE_VERSION}: Haiku (job {JOB}, relevance stage) classifies each shortlisted repo "
    f"from public project-level text only (repo name without owner, description, topics, "
    "primary language, the relevance filter's README excerpt; owner login as [owner], then "
    "alias redaction) into surface in {mcp_server, cli, library, editor_or_agent_plugin, "
    "hosted_app, other, unknown} and install_paths, a set from {npx, pip, brew, binary, "
    f"marketplace, other, unknown}}; strict JSON schema, {CHUNK} repos per request, Batch API, "
    "budget-checked, cached; first step of the selection stage, before the launch lookup, "
    "releases, mention search and star history, reading none of them; not approved, over a "
    "cap or no client: the selection is refused; a repo missing after one retry is unknown; "
    "balance only (SMD per level next to language), never a matching key, never excludes a pair"
)


class SurfaceCodingUnavailable(SelectionError):
    """ADR-084: the distribution-surface coding can't run (no LLM client or backend refused),
    so the selection is refused before any star, anchor or outcome data is fetched."""


class SurfaceItem(BaseModel):
    id: str = Field(description="The project's id from the input")
    surface: Surface
    install_paths: list[InstallPath] = Field(min_length=1)


class SurfaceOutput(BaseModel):
    items: list[SurfaceItem]


def surface_input(c: Candidate, local_id: str, readme_excerpt: str | None) -> dict[str, Any]:
    """What the model sees about one repo: public project facts, no owner login."""
    full = c.repo_full_name or ""
    owner, _, name = full.partition("/")
    m = c.metadata
    return {
        "id": local_id,
        "name": strip_owner(name, owner),
        "description": strip_owner(m.get("description"), owner),
        "topics": [t for t in m.get("topics") or [] if isinstance(t, str)][:20],
        "language": m.get("language"),
        "readme_excerpt": strip_owner(readme_excerpt, owner),
    }


def coded(c: Candidate, model: str) -> dict[str, Any] | None:
    """The repo's stored coding when it was made under the current prompt and `model`."""
    rec = c.metadata.get(METADATA_KEY)
    if not isinstance(rec, dict):
        return None
    by = rec.get("coded_by") or {}
    if by.get("prompt_fingerprint") != PROMPT.fingerprint or by.get("model") != model:
        return None
    return rec


def est_request_usd(model: str, text: str, n: int, *, batch: bool) -> float | None:
    """List-price estimate of one request (chars / 4 tokens; no cache hit assumed)."""
    tin = (len(SYSTEM) + len(TEMPLATE) + len(text)) // 4 + 50
    return cost_usd(model, TokenUsage(input=tin, output=n * OUTPUT_TOKENS_PER_REPO), batch=batch)


def requests_for(repos: int) -> int:
    return math.ceil(repos / CHUNK) if repos else 0


@dataclass
class SurfaceResult:
    repos: int = 0
    already_coded: int = 0
    coded: int = 0
    requests: int = 0
    missing: int = 0
    batch_ids: list[str] = field(default_factory=list)
    surfaces: dict[str, int] = field(default_factory=dict)
    install_paths: dict[str, int] = field(default_factory=dict)
    readme_evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d.pop("readme_evidence")
        return d


ReadmeLoader = Callable[[Candidate], tuple[bytes | None, str | None]]


@dataclass
class SurfaceCoder:
    """Codes the distribution surface of a final shortlist (module docstring). `readme` returns
    (raw README bytes or None, evidence id or None) for a repo, like the relevance filter's
    loader; `terms` are the relevance filter's trim terms (so the excerpt is the one it used)."""

    llm: LLMClient | None
    brief_run_id: str | None = None
    before_submit: Callable[[str, int, float | None], None] | None = None
    check_backend: Callable[[str], None] | None = None
    readme: ReadmeLoader | None = None
    terms: Sequence[str] = ()
    group: int = GROUP
    poll_seconds: float = 60.0
    timeout_seconds: float | None = None
    sleep: Callable[[float], None] | None = None
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)

    def model(self) -> str:
        if self.llm is None:
            raise SurfaceCodingUnavailable(UNAVAILABLE)
        return self.llm.model_for(JOB)

    def _key(self, text: str) -> str:
        """The LLM cache key of one request (as `LLMClient` computes it)."""
        assert self.llm is not None
        backend = self.llm.backend_for(JOB).name
        model = self.llm.model_for(JOB)
        ih = sha256_text(self.llm.redact(text, NAMESPACE))
        return self.llm.cache_key(backend, model, PROMPT, schema_hash(SurfaceOutput), ih, job=JOB)

    def blocked(self) -> str | None:
        """Why the coding can't run at all (no client), or None. Approval and the caps are
        checked at submission (`BudgetStop` propagates)."""
        return UNAVAILABLE if self.llm is None else None

    def run(
        self,
        store: CandidateStore,
        cands: Sequence[Candidate],
        *,
        checkpoint: dict[str, Any],
        save: Callable[[dict[str, Any]], None],
    ) -> SurfaceResult:
        from pigtail.briefs.relevance import readme_excerpt

        if self.llm is None:
            raise SurfaceCodingUnavailable(UNAVAILABLE)
        llm = self.llm
        backend = llm.backend_for(JOB).name
        model = llm.model_for(JOB)
        if self.check_backend is not None:
            self.check_backend(backend)  # BudgetStop: never a backend switch
        todo_all = sorted((c for c in cands if c.repo_full_name), key=lambda c: c.ref)
        by_ref = {c.ref: c for c in todo_all}
        res = SurfaceResult(repos=len(todo_all))
        batch = llm.batches_for(JOB)
        key = f"{PROMPT.fingerprint}:{model}"

        def uncoded() -> list[str]:
            latest = {c.ref: c for c in store.all()}
            return [c.ref for c in todo_all if coded(latest.get(c.ref, c), model) is None]

        res.already_coded = len(todo_all) - len(uncoded())
        # The chunk plan is kept in the checkpoint (as the relevance filter does), so a resumed
        # run rebuilds exactly the same requests: answered ones come from the LLM cache and a
        # batch still running is collected by its stored id, never submitted twice.
        for _pass in range(2):  # the plan, then one retry for repos the model left out
            todo = uncoded()
            if not todo:
                break
            plan = checkpoint.get("surface_plan")
            if not plan or plan.get("key") != key or plan.get("finished"):
                attempt = int(plan["attempt"]) + 1 if plan and plan.get("key") == key else 0
                if attempt > 1:
                    break
                plan = {
                    "key": key,
                    "attempt": attempt,
                    "chunks": [todo[i : i + CHUNK] for i in range(0, len(todo), CHUNK)],
                    "done": [],
                }
                checkpoint["surface_plan"] = plan
                save(checkpoint)
            chunks = list(enumerate(plan["chunks"]))
            pending = [(i, ch) for i, ch in chunks if i not in plan["done"]]
            for g in range(0, len(pending), self.group):
                group = pending[g : g + self.group]
                items: list[BatchItem] = []
                refs_of: dict[str, list[str]] = {}
                texts: dict[str, str] = {}
                est: float | None = 0.0
                for idx, refs in group:
                    payload = []
                    for j, ref in enumerate(refs):
                        c = by_ref[ref]
                        raw, ev = self.readme(c) if self.readme is not None else (None, None)
                        if ev is not None and ev not in res.readme_evidence:
                            res.readme_evidence.append(ev)
                        excerpt, _tv = readme_excerpt(raw, list(self.terms))
                        payload.append(surface_input(c, f"c{j + 1:02d}", excerpt))
                    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                    item_ref = f"s{plan['attempt']}c{idx:04d}"
                    items.append(BatchItem(ref=item_ref, input_text=text, namespace=NAMESPACE))
                    refs_of[item_ref] = list(refs)
                    texts[item_ref] = text
                    if backend == "api":
                        e = est_request_usd(model, text, len(refs), batch=batch)
                        est = None if e is None or est is None else max(est, e)
                if backend != "api":
                    est = 0.0  # subscription: no money
                # requests answered before come from the LLM cache: no money, no approval
                cached = [it for it in items if llm.store.cache_get(self._key(texts[it.ref]))]
                for it in cached:
                    out = llm.complete(
                        PROMPT,
                        texts[it.ref],
                        SurfaceOutput,
                        job=JOB,
                        namespace=NAMESPACE,
                        brief_run_id=self.brief_run_id,
                    )
                    self._store(store, refs_of[it.ref], out, model, res)
                items = [it for it in items if it not in cached]
                kw: dict[str, Any] = {"poll_seconds": self.poll_seconds}
                if self.sleep is not None:
                    kw["sleep"] = self.sleep
                # BudgetStop (approval, caps) and BatchPending propagate: the selection never
                # runs without the coding (fail closed)
                run = llm.run_batch(
                    PROMPT,
                    items,
                    SurfaceOutput,
                    job=JOB,
                    brief_run_id=self.brief_run_id,
                    before_submit=self.before_submit,
                    est_usd_per_item=est,
                    timeout_seconds=self.timeout_seconds,
                    **kw,
                )
                res.requests += len(items)
                for bid in run.batch_ids:
                    if bid not in res.batch_ids:
                        res.batch_ids.append(bid)
                for item_ref, out in run.results.items():
                    self._store(store, refs_of[item_ref], out, model, res)
                for idx, _refs in group:
                    if f"s{plan['attempt']}c{idx:04d}" not in run.failed:
                        plan["done"].append(idx)
                save(checkpoint)
            plan["finished"] = True
            save(checkpoint)
        latest = {c.ref: c for c in store.all()}
        for c in todo_all:  # still missing after the retry: unknown, marked missing
            if coded(latest.get(c.ref, c), model) is None:
                res.missing += 1
                store.set_metadata(
                    c.ref,
                    {
                        METADATA_KEY: {
                            "surface": "unknown",
                            "install_paths": ["unknown"],
                            "missing": True,
                            "coded_by": self._by(model, backend, None, False),
                            "coded_at": self.clock().isoformat(),
                            "rule": SURFACE_VERSION,
                        }
                    },
                )
        latest = {c.ref: c for c in store.all()}
        for c in todo_all:
            rec = coded(latest.get(c.ref, c), model) or {}
            s = str(rec.get("surface", "unknown"))
            res.surfaces[s] = res.surfaces.get(s, 0) + 1
            for p in rec.get("install_paths") or ["unknown"]:
                res.install_paths[str(p)] = res.install_paths.get(str(p), 0) + 1
        res.surfaces = dict(sorted(res.surfaces.items()))
        res.install_paths = dict(sorted(res.install_paths.items()))
        checkpoint["surface_done"] = True
        save(checkpoint)
        return res

    def _by(self, model: str, backend: str, batch_id: str | None, cached: bool) -> dict[str, Any]:
        return {
            "model": model,
            "backend": backend,
            "job": JOB,
            "prompt_id": PROMPT.id,
            "prompt_version": PROMPT.version,
            "prompt_fingerprint": PROMPT.fingerprint,
            "schema_sha": schema_hash(SurfaceOutput),
            "thinking": (  # ADR-087
                self.llm.thinking_label(JOB, model, backend) if self.llm is not None else None
            ),
            "batch_id": batch_id,
            "cached": cached,
        }

    def _store(
        self,
        store: CandidateStore,
        refs: list[str],
        out: Any,
        model: str,
        res: SurfaceResult,
    ) -> None:
        by_id = {f"c{k + 1:02d}": ref for k, ref in enumerate(refs)}
        prov = out.provenance()
        seen: set[str] = set()
        for it in out.output.items:
            ref = by_id.get(it.id)
            if ref is None or ref in seen:
                continue  # an id we didn't send, or a duplicate: ignored (retried as missing)
            seen.add(ref)
            store.set_metadata(
                ref,
                {
                    METADATA_KEY: {
                        "surface": it.surface,
                        "install_paths": sorted(set(it.install_paths)),
                        "coded_by": self._by(
                            str(prov.get("model") or model),
                            str(prov.get("backend") or ""),
                            prov.get("batch_id"),
                            bool(out.cached),
                        ),
                        "coded_at": self.clock().isoformat(),
                        "rule": SURFACE_VERSION,
                    }
                },
            )
            res.coded += 1


UNAVAILABLE = (
    "the selection's first step, the distribution-surface coding (a paid Haiku step, "
    "ADR-084), has no LLM client: the selection is refused; nothing was fetched, computed or "
    "stored and the run can be resumed"
)


def surface_params() -> dict[str, Any]:
    """The coding as it goes into the pre-registered selection parameters (ADR-084)."""
    from pigtail.briefs.confirm import resolved_model
    from pigtail.llm.thinking import job_params

    return {
        "version": SURFACE_VERSION,
        "rule": SURFACE_RULE,
        "surfaces": list(SURFACES),
        "install_paths": list(INSTALL_PATHS),
        "job": JOB,
        "stage": "relevance",
        "model": resolved_model(JOB),
        "prompt_id": PROMPT.id,
        "prompt_version": PROMPT.version,
        "prompt_fingerprint": PROMPT.fingerprint,
        "schema_sha": schema_hash(SurfaceOutput),
        # ADR-087: the thinking setting and what it sends on the resolved model
        "thinking": job_params(JOB, resolved_model(JOB)),
        "repos_per_request": CHUNK,
        "use": "balance only: SMD per level next to language; never a matching key; never "
        "excludes a pair",
    }


def surface_of(c: Candidate) -> tuple[str, tuple[str, ...]]:
    """(surface, install paths) stored for a repo; ('unknown', ('unknown',)) when not coded."""
    rec = c.metadata.get(METADATA_KEY)
    if not isinstance(rec, dict):
        return "unknown", ("unknown",)
    s = rec.get("surface")
    paths = rec.get("install_paths") or ["unknown"]
    return (
        str(s) if s in SURFACES else "unknown",
        tuple(sorted({str(p) for p in paths if p in INSTALL_PATHS})) or ("unknown",),
    )
