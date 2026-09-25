"""LLMClient: the single interface for all product LLM calls (PRD F15, R15.1–R15.5).

Every call:
1. picks the backend (`LLM_BACKEND`, or a per-job override),
2. strips direct identifiers from the input before it leaves the process (PRD §10),
3. serves from the cache keyed on (prompt id+version, input hash, schema, model, backend),
4. refuses new model calls while that backend is paused after a limit hit (cache hits
   still served),
5. validates the output against the pydantic schema (one retry on invalid output),
6. records usage in the ledger and returns the output with its provenance record.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ValidationError

from pigtail.config import BackendName, Settings
from pigtail.llm.errors import BackendError, QueuePaused, StructuredOutputError, UsageLimitReached
from pigtail.llm.store import LLMStore, UsageRow
from pigtail.llm.types import (
    Backend,
    BackendResponse,
    LLMResult,
    PromptSpec,
    schema_hash,
    schema_of,
    sha256_text,
)
from pigtail.pseudonymize import Pseudonymizer

Redactor = Callable[[str], str]


class LLMClient:
    def __init__(
        self,
        *,
        backends: Mapping[str, Backend],
        default_backend: BackendName,
        store: LLMStore,
        model: str,
        redactor: Redactor,
        overrides: Mapping[str, BackendName] | None = None,
        limit_pause_seconds: int = 1800,
        max_invalid_retries: int = 1,
    ) -> None:
        if default_backend not in backends:
            raise ValueError(f"default backend {default_backend!r} not configured")
        self.backends = dict(backends)
        self.default_backend = default_backend
        self.overrides = dict(overrides or {})
        self.store = store
        self.model = model
        self.redact = redactor
        self.limit_pause_seconds = limit_pause_seconds
        self.max_invalid_retries = max_invalid_retries

    def backend_for(self, job: str) -> Backend:
        name = self.overrides.get(job, self.default_backend)
        try:
            return self.backends[name]
        except KeyError as e:
            raise ValueError(f"job {job!r} is routed to unconfigured backend {name!r}") from e

    @staticmethod
    def cache_key(
        backend: str, model: str, prompt: PromptSpec, schema_h: str, input_hash: str
    ) -> str:
        return "|".join(
            (prompt.id, prompt.version, prompt.fingerprint, input_hash, schema_h, model, backend)
        )

    def complete[T: BaseModel](
        self,
        prompt: PromptSpec,
        input_text: str,
        schema: type[T],
        *,
        job: str,
        model: str | None = None,
        use_cache: bool = True,
    ) -> LLMResult[T]:
        backend = self.backend_for(job)
        model = model or self.model
        safe_input = self.redact(input_text)
        input_hash = sha256_text(safe_input)
        key = self.cache_key(backend.name, model, prompt, schema_hash(schema), input_hash)

        def rec(status: str, used_model: str, resp: BackendResponse | None = None) -> None:
            self.store.record(
                UsageRow(
                    backend=backend.name,
                    job=job,
                    model=used_model,
                    prompt_id=prompt.id,
                    prompt_version=prompt.version,
                    status=status,
                    input_tokens=resp.input_tokens if resp else 0,
                    output_tokens=resp.output_tokens if resp else 0,
                    cost_usd=resp.cost_usd if resp else 0.0,
                )
            )

        if use_cache and (hit := self.store.cache_get(key)) is not None:
            data, cached_model = hit
            rec("cached", cached_model)
            return self._result(
                schema.model_validate(data),
                backend.name,
                cached_model,
                prompt,
                input_hash,
                cached=True,
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
                    system=prompt.system, prompt=rendered, json_schema=json_schema, model=model
                )
            except UsageLimitReached as e:
                until = e.reset_at or datetime.now(UTC) + timedelta(
                    seconds=self.limit_pause_seconds
                )
                self.store.pause(backend.name, until, f"limit hit in job {job}")
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
            self.store.cache_put(key, output.model_dump(mode="json"), resp.model)
            return self._result(output, backend.name, resp.model, prompt, input_hash, cached=False)
        raise StructuredOutputError(f"output failed schema validation: {last_err}")

    @staticmethod
    def _result[T: BaseModel](
        output: T, backend: str, model: str, prompt: PromptSpec, input_hash: str, *, cached: bool
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
        )


def build_client(settings: Settings | None = None, store: LLMStore | None = None) -> LLMClient:
    """Construct the client from environment settings. Changing LLM_BACKEND needs only a restart."""
    from pigtail.llm.api import ApiBackend
    from pigtail.llm.subscription import SubscriptionBackend

    s = settings or Settings.from_env()
    if not s.pseudonym_key:
        raise ValueError("PSEUDONYM_KEY must be set: inputs are pseudonymized before LLM calls")
    pz = Pseudonymizer(s.pseudonym_key)
    return LLMClient(
        backends={"subscription": SubscriptionBackend(), "api": ApiBackend()},
        default_backend=s.llm_backend,
        overrides=s.llm_backend_overrides,
        store=store or LLMStore(s.data_dir / "llm.sqlite3"),
        model=s.llm_model,
        redactor=pz.strip_identifiers,
        limit_pause_seconds=s.llm_limit_pause_seconds,
    )
