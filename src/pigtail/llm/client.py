"""LLMClient: the single interface for all product LLM calls (PRD F15, R15.1-R15.5, R15.8-R15.10).

Every call:
1. picks the backend (`LLM_BACKEND`, or an explicit per-job override; never switched on its
   own) and the model of the job's stage (R15.8, `pigtail.llm.stages`),
2. trims the evidence when asked to (R15.10, `pigtail.llm.trim`), then strips direct
   identifiers from the input before it leaves the process (PRD §10; e-mails, phones, profile
   URLs, DIDs and @mentions, DPIA CB-06), replacing people with per-call, non-keyed aliases
   (`@user1`, ...; `pigtail.llm.redact`, ADR-066 follow-up) per source `namespace`,
3. serves from the cache keyed on (prompt id+version+fingerprint, input hash, schema, model,
   backend); cache rows expire after LLM_CACHE_RETENTION_DAYS and can be linked to an
   `evidence_id` so they are purged with their source (CB-05),
4. refuses new model calls while that backend is paused after a limit hit (cache hits
   still served),
5. validates the output against the pydantic schema (one retry on invalid output),
6. records usage (tokens, prompt-cache tokens, batch id, cost, brief run and case) in the
   ledger and returns the output with its provenance record (model id, prompt version and
   batch id with every output, Directive §6.3).

`run_batch` does the same for many inputs through the Message Batches API (R15.9) when the
backend supports it and the job isn't time-sensitive; otherwise it makes standard calls. The
result cache is the source of truth: a batch fills it, and a resumed run first collects the
batches it already submitted (ids in the `BatchStore`), then serves those items from the cache.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ValidationError

from pigtail.config import BackendName, Settings
from pigtail.llm.batch import (
    BatchRecord,
    BatchRequestRecord,
    BatchStore,
    CostSink,
    MemoryBatchStore,
    custom_id_for,
)
from pigtail.llm.errors import (
    BackendError,
    BatchPending,
    QueuePaused,
    StructuredOutputError,
    UsageLimitReached,
)
from pigtail.llm.redact import REDACTION_VERSION, alias_redact
from pigtail.llm.stages import UnknownJob, stage_for, time_sensitive
from pigtail.llm.store import LLMStore, UsageRow
from pigtail.llm.trim import Trimmed, TrimPolicy, trim_text
from pigtail.llm.types import (
    Backend,
    BackendResponse,
    LLMResult,
    PromptSpec,
    schema_hash,
    schema_of,
    sha256_text,
)

# (text, namespace) -> redacted text; `pigtail.llm.redact.alias_redact` in production.
Redactor = Callable[[str, str], str]
# Called before a batch is submitted with (job, requests, estimated USD or None); raises to stop
# (the brief run's `BudgetGuard`).
BeforeSubmit = Callable[[str, int, float | None], None]


@dataclass(frozen=True)
class BatchItem:
    """One input of `run_batch`. `ref` is the caller's key for the result (e.g. a chunk id)."""

    ref: str
    input_text: str | Trimmed
    namespace: str = "generic"
    evidence_id: str | None = None
    case_ref: str | None = None


@dataclass
class BatchRun:
    """What `run_batch` did: results per item ref, failures, and the batches it used."""

    results: dict[str, LLMResult[Any]] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)
    batch_ids: list[str] = field(default_factory=list)
    mode: str = "batch"  # batch | standard
    standard_fallbacks: int = 0


@dataclass(frozen=True)
class _Prepared:
    item: BatchItem
    text: str
    input_hash: str
    key: str
    trim_version: str | None


class LLMClient:
    def __init__(
        self,
        *,
        backends: Mapping[str, Backend],
        default_backend: BackendName,
        store: LLMStore,
        redactor: Redactor,
        models: Mapping[str, str] | None = None,
        model: str | None = None,
        overrides: Mapping[str, BackendName] | None = None,
        limit_pause_seconds: int = 1800,
        max_invalid_retries: int = 1,
        batch_store: BatchStore | None = None,
        cost_sink: CostSink | None = None,
        use_batch: bool = True,
    ) -> None:
        """`models` maps a stage (relevance, extraction, synthesis) to its model (R15.8);
        `model` is a fallback for stages missing from `models` and for jobs without a stage
        (tests and ad-hoc calls). With neither, a job without a stage is an error."""
        if default_backend not in backends:
            raise ValueError(f"default backend {default_backend!r} not configured")
        self.backends = dict(backends)
        self.default_backend = default_backend
        self.overrides = dict(overrides or {})
        self.store = store
        self.models = dict(models or {})
        self.model = model
        self.redact = redactor
        self.limit_pause_seconds = limit_pause_seconds
        self.max_invalid_retries = max_invalid_retries
        self.batch_store: BatchStore = batch_store or MemoryBatchStore()
        self.cost_sink = cost_sink
        self.use_batch = use_batch

    # --- routing ---------------------------------------------------------------------------
    def backend_for(self, job: str) -> Backend:
        name = self.overrides.get(job, self.default_backend)
        try:
            return self.backends[name]
        except KeyError as e:
            raise ValueError(f"job {job!r} is routed to unconfigured backend {name!r}") from e

    def stage_of(self, job: str) -> str | None:
        try:
            return stage_for(job)
        except UnknownJob:
            if self.model is not None:
                return None
            raise

    def model_for(self, job: str) -> str:
        """R15.8: the model of the job's stage (or the fallback `model`)."""
        stage = self.stage_of(job)
        if stage is not None and stage in self.models:
            return self.models[stage]
        if self.model is not None:
            return self.model
        raise ValueError(f"no model configured for stage {stage!r} (job {job!r})")

    def batches_for(self, job: str) -> bool:
        """R15.9: whether `run_batch` uses the Message Batches API for this job."""
        backend = self.backend_for(job)
        return (
            self.use_batch
            and bool(getattr(backend, "supports_batch", False))
            and not time_sensitive(job)
        )

    @staticmethod
    def cache_key(
        backend: str, model: str, prompt: PromptSpec, schema_h: str, input_hash: str
    ) -> str:
        return "|".join(
            (prompt.id, prompt.version, prompt.fingerprint, input_hash, schema_h, model, backend)
        )

    # --- helpers ---------------------------------------------------------------------------
    def _prepare(
        self,
        text: str | Trimmed,
        namespace: str,
        trim: TrimPolicy | None,
        trim_terms: Sequence[str],
    ) -> tuple[str, str | None]:
        trim_version: str | None = None
        if isinstance(text, Trimmed):
            trim_version = text.report.version
            text = text.text
        elif trim is not None:
            t = trim_text(text, terms=trim_terms, policy=trim)
            text, trim_version = t.text, t.report.version
        return self.redact(text, namespace), trim_version

    def _record(
        self,
        *,
        backend: str,
        job: str,
        stage: str | None,
        model: str,
        prompt: PromptSpec,
        status: str,
        resp: BackendResponse | None = None,
        brief_run_id: str | None = None,
        case_ref: str | None = None,
    ) -> None:
        row = UsageRow(
            backend=backend,
            job=job,
            model=model,
            prompt_id=prompt.id,
            prompt_version=prompt.version,
            status=status,
            input_tokens=resp.input_tokens if resp else 0,
            output_tokens=resp.output_tokens if resp else 0,
            cost_usd=resp.cost_usd if resp else 0.0,
            stage=stage,
            cache_write_tokens=resp.cache_write_tokens if resp else 0,
            cache_read_tokens=resp.cache_read_tokens if resp else 0,
            batch_id=resp.batch_id if resp else None,
            brief_run_id=brief_run_id,
            case_ref=case_ref,
        )
        self.store.record(row)
        if self.cost_sink is not None and status not in ("cached", "limit"):
            self.cost_sink.record(row)

    def _pause_on_limit(self, backend: str, job: str, e: UsageLimitReached) -> None:
        until = e.reset_at or datetime.now(UTC) + timedelta(seconds=self.limit_pause_seconds)
        self.store.pause(backend, until, f"limit hit in job {job}")

    # --- one call --------------------------------------------------------------------------
    def complete[T: BaseModel](
        self,
        prompt: PromptSpec,
        input_text: str | Trimmed,
        schema: type[T],
        *,
        job: str,
        model: str | None = None,
        use_cache: bool = True,
        namespace: str = "generic",
        evidence_id: str | None = None,
        brief_run_id: str | None = None,
        case_ref: str | None = None,
        trim: TrimPolicy | None = None,
        trim_terms: Sequence[str] = (),
    ) -> LLMResult[T]:
        """Run one structured call (a standard call, never batched).

        `namespace` is the pseudonym namespace of the input's source (e.g. "github", "hn") for
        @mentions (CB-06). `evidence_id` links the cached output to the evidence it was derived
        from, so the cache row is purged with that evidence (CB-05). `trim` (or a `Trimmed`
        input) applies evidence trimming first (R15.10). `brief_run_id` and `case_ref` go to
        the cost ledger (R15.11).
        """
        safe_input, trim_version = self._prepare(input_text, namespace, trim, trim_terms)
        return self._complete_safe(
            prompt,
            safe_input,
            schema,
            job=job,
            model=model,
            use_cache=use_cache,
            evidence_id=evidence_id,
            brief_run_id=brief_run_id,
            case_ref=case_ref,
            trim_version=trim_version,
        )

    def _complete_safe[T: BaseModel](
        self,
        prompt: PromptSpec,
        safe_input: str,
        schema: type[T],
        *,
        job: str,
        model: str | None,
        use_cache: bool,
        evidence_id: str | None,
        brief_run_id: str | None,
        case_ref: str | None,
        trim_version: str | None,
    ) -> LLMResult[T]:
        """`complete` on an input that is already trimmed and redacted."""
        backend = self.backend_for(job)
        stage = self.stage_of(job)
        model = model or self.model_for(job)
        input_hash = sha256_text(safe_input)
        key = self.cache_key(backend.name, model, prompt, schema_hash(schema), input_hash)

        def rec(status: str, used_model: str, resp: BackendResponse | None = None) -> None:
            self._record(
                backend=backend.name,
                job=job,
                stage=stage,
                model=used_model,
                prompt=prompt,
                status=status,
                resp=resp,
                brief_run_id=brief_run_id,
                case_ref=case_ref,
            )

        if use_cache and (hit := self.store.cache_get(key)) is not None:
            data, cached_model = hit
            if evidence_id is not None:
                self.store.link_evidence(key, evidence_id)
            rec("cached", cached_model)
            return self._result(
                schema.model_validate(data),
                backend.name,
                cached_model,
                prompt,
                input_hash,
                cached=True,
                stage=stage,
                trim_version=trim_version,
            )

        paused = self.store.paused_until(backend.name)
        if paused is not None:
            raise QueuePaused(backend.name, paused)

        json_schema = schema_of(schema)
        rendered = prompt.render(safe_input)
        last_err: Exception | None = None
        for _ in range(self.max_invalid_retries + 1):
            try:
                resp = backend.complete(
                    system=prompt.system,
                    prompt=rendered,
                    json_schema=json_schema,
                    model=model,
                    context=prompt.context,
                )
            except UsageLimitReached as e:
                self._pause_on_limit(backend.name, job, e)
                rec("limit", model)
                raise
            except BackendError:
                rec("error", model)
                raise
            try:
                output = schema.model_validate(resp.data)
            except ValidationError as e:
                last_err = e
                rec("invalid_output", resp.model, resp)
                continue
            rec("ok", resp.model, resp)
            self.store.cache_put(
                key, output.model_dump(mode="json"), resp.model, evidence_id=evidence_id
            )
            return self._result(
                output,
                backend.name,
                resp.model,
                prompt,
                input_hash,
                cached=False,
                stage=stage,
                trim_version=trim_version,
            )
        raise StructuredOutputError(f"output failed schema validation: {last_err}")

    # --- many calls: Message Batches API (R15.9) --------------------------------------------
    def run_batch[T: BaseModel](
        self,
        prompt: PromptSpec,
        items: Sequence[BatchItem],
        schema: type[T],
        *,
        job: str,
        brief_run_id: str | None = None,
        before_submit: BeforeSubmit | None = None,
        est_usd_per_item: float | None = None,
        trim: TrimPolicy | None = None,
        trim_terms: Sequence[str] = (),
        poll_seconds: float = 60.0,
        timeout_seconds: float | None = None,
        sleep: Callable[[float], None] = time.sleep,
        fallback_standard: bool = True,
    ) -> BatchRun:
        """Structured outputs for many inputs of one job.

        Batched when `batches_for(job)`: collects any batch this brief run already submitted
        for the same job, prompt and model; serves cached items; submits one batch for the
        rest (after `before_submit`, the budget check); polls every `poll_seconds` until every
        batch has ended, then fills the cache from the results. With `timeout_seconds` set and
        a batch still running, raises `BatchPending` (ids are stored: calling again resumes).
        Items whose batch request failed in a retryable way (errored, expired, canceled, or
        invalid output) get one standard call when `fallback_standard`. Otherwise (subscription
        backend, time-sensitive job, `LLM_BATCH=0`) every item is a standard call.
        """
        run = BatchRun()
        if not items:
            return run
        refs = [it.ref for it in items]
        if len(set(refs)) != len(refs):
            raise ValueError("run_batch: item refs must be unique")
        if not self.batches_for(job):
            run.mode = "standard"
            if before_submit is not None and est_usd_per_item is not None:
                before_submit(job, len(items), est_usd_per_item * len(items))
            for it in items:
                run.results[it.ref] = self.complete(
                    prompt,
                    it.input_text,
                    schema,
                    job=job,
                    namespace=it.namespace,
                    evidence_id=it.evidence_id,
                    brief_run_id=brief_run_id,
                    case_ref=it.case_ref,
                    trim=trim,
                    trim_terms=trim_terms,
                )
            return run

        backend = self.backend_for(job)
        stage = self.stage_of(job)
        model = self.model_for(job)
        sh = schema_hash(schema)
        prepared: list[_Prepared] = []
        for it in items:
            text, tv = self._prepare(it.input_text, it.namespace, trim, trim_terms)
            ih = sha256_text(text)
            prepared.append(
                _Prepared(it, text, ih, self.cache_key(backend.name, model, prompt, sh, ih), tv)
            )
        by_key: dict[str, list[_Prepared]] = {}
        for p in prepared:
            by_key.setdefault(p.key, []).append(p)

        # 1. Resume: batches this run already submitted for this job, prompt and model.
        open_ids = [
            b.batch_id
            for b in self.batch_store.open_batches(
                job=job,
                prompt_fingerprint=prompt.fingerprint,
                model=model,
                brief_run_id=brief_run_id,
            )
        ]
        in_flight: set[str] = set()
        for bid in open_ids:
            if self._collect(bid, prompt, schema, backend, stage, brief_run_id):
                continue
            in_flight.update(r.cache_key for r in self.batch_store.requests(bid))
        run.batch_ids.extend(open_ids)

        # 2. Submit the items that are neither cached nor in flight.
        todo = [k for k in by_key if k not in in_flight and self.store.cache_get(k) is None]
        if todo:
            paused = self.store.paused_until(backend.name)
            if paused is not None:
                raise QueuePaused(backend.name, paused)
            est = est_usd_per_item * len(todo) if est_usd_per_item is not None else None
            if before_submit is not None:
                before_submit(job, len(todo), est)
            json_schema = schema_of(schema)
            requests = []
            records = []
            for k in todo:
                p = by_key[k][0]
                cid = custom_id_for(k)
                params = backend.params(  # type: ignore[attr-defined]
                    system=prompt.system,
                    prompt=prompt.render(p.text),
                    json_schema=json_schema,
                    model=model,
                    context=prompt.context,
                )
                requests.append((cid, params))
                records.append(
                    BatchRequestRecord(
                        custom_id=cid,
                        cache_key=k,
                        input_hash=p.input_hash,
                        evidence_id=p.item.evidence_id,
                        case_ref=p.item.case_ref,
                    )
                )
            try:
                bid = backend.submit_batch(requests)  # type: ignore[attr-defined]
            except UsageLimitReached as e:
                self._pause_on_limit(backend.name, job, e)
                raise
            self.batch_store.add(
                BatchRecord(
                    batch_id=bid,
                    job=job,
                    stage=stage or "synthesis",
                    model=model,
                    prompt_id=prompt.id,
                    prompt_version=prompt.version,
                    prompt_fingerprint=prompt.fingerprint,
                    schema_hash=sh,
                    requests=len(requests),
                    brief_run_id=brief_run_id,
                    est_usd=est,
                ),
                records,
            )
            run.batch_ids.append(bid)
            in_flight.update(todo)

        # 3. Poll until every open batch has ended, then collect it.
        started = time.monotonic()
        pending = [b for b in run.batch_ids if self._is_open(b)]
        while pending:
            still = [
                b
                for b in pending
                if not self._collect(b, prompt, schema, backend, stage, brief_run_id)
            ]
            if not still:
                break
            if timeout_seconds is not None and time.monotonic() - started >= timeout_seconds:
                raise BatchPending(still)
            sleep(poll_seconds)
            pending = still

        # 4. Results from the cache; one standard call for retryable failures.
        failed_types = self._failures(run.batch_ids)
        for k, group in by_key.items():
            hit = self.store.cache_get(k)
            for p in group:
                if hit is not None:
                    data, used_model = hit
                    if p.item.evidence_id is not None:
                        self.store.link_evidence(k, p.item.evidence_id)
                    run.results[p.item.ref] = self._result(
                        schema.model_validate(data),
                        backend.name,
                        used_model,
                        prompt,
                        p.input_hash,
                        cached=False,
                        stage=stage,
                        batch_id=self._batch_of(run.batch_ids, k),
                        trim_version=p.trim_version,
                    )
                    continue
                err, retryable = failed_types.get(k, ("missing", True))
                if fallback_standard and retryable:
                    run.standard_fallbacks += 1
                    run.results[p.item.ref] = self._complete_safe(
                        prompt,
                        p.text,
                        schema,
                        job=job,
                        model=model,
                        use_cache=True,
                        evidence_id=p.item.evidence_id,
                        brief_run_id=brief_run_id,
                        case_ref=p.item.case_ref,
                        trim_version=p.trim_version,
                    )
                    hit = self.store.cache_get(k)
                else:
                    run.failed[p.item.ref] = err
        return run

    def _is_open(self, batch_id: str) -> bool:
        rec = self.batch_store.get(batch_id)
        return rec is not None and rec.status in ("submitted", "ended")

    def _batch_of(self, batch_ids: list[str], key: str) -> str | None:
        for bid in batch_ids:
            if any(r.cache_key == key for r in self.batch_store.requests(bid)):
                return bid
        return None

    def _failures(self, batch_ids: list[str]) -> dict[str, tuple[str, bool]]:
        out: dict[str, tuple[str, bool]] = {}
        for bid in batch_ids:
            for r in self.batch_store.requests(bid):
                if r.status in ("errored", "canceled", "expired", "invalid_output"):
                    bad_request = r.error_type == "invalid_request_error"
                    retryable = not (r.status == "errored" and bad_request)
                    out[r.cache_key] = (r.error_type or r.status, retryable)
        return out

    def _collect[T: BaseModel](
        self,
        batch_id: str,
        prompt: PromptSpec,
        schema: type[T],
        backend: Backend,
        stage: str | None,
        brief_run_id: str | None,
    ) -> bool:
        """Collect `batch_id` if it has ended (True), else leave it (False). Idempotent."""
        rec = self.batch_store.get(batch_id)
        if rec is None or rec.status in ("collected", "failed", "canceled"):
            return True
        status = backend.batch_status(batch_id)  # type: ignore[attr-defined]
        if not status.ended:
            return False
        self.batch_store.set_status(batch_id, "ended", status.counts)
        reqs = {r.custom_id: r for r in self.batch_store.requests(batch_id)}
        for res in backend.batch_results(batch_id):  # type: ignore[attr-defined]
            r = reqs.get(res.custom_id)
            if r is None or r.status != "pending":
                continue  # unknown or already collected
            if res.kind != "succeeded" or res.response is None:
                self.batch_store.mark_request(batch_id, r.custom_id, res.kind, res.error)
                if res.kind == "errored":
                    self._record(
                        backend=backend.name,
                        job=rec.job,
                        stage=stage,
                        model=rec.model,
                        prompt=prompt,
                        status="error",
                        brief_run_id=brief_run_id,
                        case_ref=r.case_ref,
                    )
                continue
            resp = res.response
            try:
                output = schema.model_validate(resp.data)
            except ValidationError:
                self.batch_store.mark_request(batch_id, r.custom_id, "invalid_output")
                self._record(
                    backend=backend.name,
                    job=rec.job,
                    stage=stage,
                    model=resp.model,
                    prompt=prompt,
                    status="invalid_output",
                    resp=resp,
                    brief_run_id=brief_run_id,
                    case_ref=r.case_ref,
                )
                continue
            self._record(
                backend=backend.name,
                job=rec.job,
                stage=stage,
                model=resp.model,
                prompt=prompt,
                status="ok",
                resp=resp,
                brief_run_id=brief_run_id,
                case_ref=r.case_ref,
            )
            self.store.cache_put(
                r.cache_key, output.model_dump(mode="json"), resp.model, evidence_id=r.evidence_id
            )
            self.batch_store.mark_request(batch_id, r.custom_id, "succeeded")
        for r in self.batch_store.requests(batch_id):
            if r.status == "pending":  # no result came back for it
                self.batch_store.mark_request(batch_id, r.custom_id, "expired", "no_result")
        self.batch_store.set_status(batch_id, "collected", status.counts)
        return True

    @staticmethod
    def _result[T: BaseModel](
        output: T,
        backend: str,
        model: str,
        prompt: PromptSpec,
        input_hash: str,
        *,
        cached: bool,
        stage: str | None = None,
        batch_id: str | None = None,
        trim_version: str | None = None,
    ) -> LLMResult[T]:
        return LLMResult(
            output=output,
            backend=backend,
            model=model,
            prompt_id=prompt.id,
            prompt_version=prompt.version,
            prompt_fingerprint=prompt.fingerprint,
            input_hash=input_hash,
            cached=cached,
            created_at=datetime.now(UTC),
            redaction_version=REDACTION_VERSION,
            stage=stage,
            batch_id=batch_id,
            trim_version=trim_version,
        )


def build_client(
    settings: Settings | None = None,
    store: LLMStore | None = None,
    *,
    batch_store: BatchStore | None = None,
    cost_sink: CostSink | None = None,
) -> LLMClient:
    """Construct the client from environment settings. Changing LLM_BACKEND needs only a restart.

    Inputs are redacted with per-call aliases (no key needed, ADR-066 follow-up).
    Per-stage models come from `LLM_MODEL_<STAGE>` (fallback `LLM_MODEL`, then the code
    defaults, R15.8); a job without a stage is refused rather than sent to a default model.
    With DATABASE_URL set, batch ids and the actual-cost ledger go to Postgres (migration
    0020), so batches survive a restart; without it they are kept in memory.
    """
    from pigtail.llm.api import ApiBackend
    from pigtail.llm.subscription import SubscriptionBackend

    s = settings or Settings.from_env()
    if s.database_url and (batch_store is None or cost_sink is None):
        from pigtail.llm.batch import PgBatchStore, PgCostLedger

        try:
            batch_store = batch_store or PgBatchStore.connect(s.database_url)
            cost_sink = cost_sink or PgCostLedger.connect(s.database_url)
        except Exception as e:  # reported; batch state is then kept in memory
            import sys

            print(
                f"warning: no database for batch state and the cost ledger ({type(e).__name__});"
                " batches are kept in memory",
                file=sys.stderr,
            )
    return LLMClient(
        backends={"subscription": SubscriptionBackend(), "api": ApiBackend()},
        default_backend=s.llm_backend,
        overrides=s.llm_backend_overrides,
        store=store
        or LLMStore(s.data_dir / "llm.sqlite3", retention_days=s.llm_cache_retention_days),
        models={str(k): v for k, v in s.llm_models.items()},
        redactor=alias_redact,
        limit_pause_seconds=s.llm_limit_pause_seconds,
        batch_store=batch_store,
        cost_sink=cost_sink,
        use_batch=s.llm_batch,
    )
