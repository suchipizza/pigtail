"""Fake `api` backends for the distribution-surface coding (ADR-084; no network, no key).

`surface_answer` reads the projects JSON of one request and codes each project from its
synthetic name: `mcp` -> `mcp_server`, `cli` or `lint` -> `cli`, `plugin` -> the editor or
agent plugin, `app` -> `hosted_app`, `lib` -> `library`, else `other`; install paths from the
language (`JavaScript`/`TypeScript` -> `npx`, `Python` -> `pip`, `Go` -> `binary` and `brew`,
else `unknown`). `forget` names are left out of the answer once (the retry path). Every request
is kept, so tests can check what reached the "model" and when.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from pigtail.llm.api import BatchItemResult, BatchStatus, build_params
from pigtail.llm.types import BackendResponse

MARKER = "Classify every project."


def is_surface_prompt(text: str) -> bool:
    return MARKER in text


def code_one(p: dict[str, Any]) -> dict[str, Any]:
    name = (p.get("name") or "").lower()
    surface = "other"
    for key, label in (
        ("mcp", "mcp_server"),
        ("plugin", "editor_or_agent_plugin"),
        ("cli", "cli"),
        ("lint", "cli"),
        ("app", "hosted_app"),
        ("lib", "library"),
    ):
        if key in name:
            surface = label
            break
    lang = p.get("language")
    paths = {
        "JavaScript": ["npx"],
        "TypeScript": ["npx"],
        "Python": ["pip"],
        "Go": ["binary", "brew"],
    }.get(lang or "", ["unknown"])
    return {"id": p["id"], "surface": surface, "install_paths": paths}


def surface_answer(prompt_text: str, forget: set[str] | None = None) -> dict[str, Any]:
    start, end = prompt_text.index("["), prompt_text.rindex("]") + 1
    projects = json.loads(prompt_text[start:end])
    return {
        "items": [
            code_one(p) for p in projects if not forget or (p.get("name") or "") not in forget
        ]
    }


@dataclass
class SurfaceBackend:
    """Standard-call fake (`api`)."""

    name: str = "api"
    calls: list[str] = field(default_factory=list)
    forget: set[str] = field(default_factory=set)  # names left out of the next answer
    always_forget: bool = False  # ... and of every later one

    def complete(self, *, prompt: str, model: str, **kw: Any) -> BackendResponse:
        self.calls.append(prompt)
        data = surface_answer(prompt, self.forget)
        if not self.always_forget:
            self.forget = set()
        return BackendResponse(
            data=data, model=model, input_tokens=4000, output_tokens=300, cost_usd=0.0045
        )


@dataclass
class SurfaceBatchBackend:
    """Batch-capable fake: `polls_until_end` status calls before a batch ends."""

    name: str = "api"
    supports_batch: bool = True
    polls_until_end: int = 0
    submitted: list[list[tuple[str, dict[str, Any]]]] = field(default_factory=list)
    polls: dict[str, int] = field(default_factory=dict)

    def params(self, **kw: Any) -> dict[str, Any]:
        return build_params(max_tokens=2000, **kw)

    def complete(self, **kw: Any) -> BackendResponse:
        raise AssertionError("no standard call expected")

    def submit_batch(self, requests: list[tuple[str, dict[str, Any]]]) -> str:
        self.submitted.append(list(requests))
        return f"msgbatch_s{len(self.submitted)}"

    def batch_status(self, batch_id: str) -> BatchStatus:
        self.polls[batch_id] = self.polls.get(batch_id, 0) + 1
        ended = self.polls[batch_id] > self.polls_until_end
        return BatchStatus(batch_id, "ended" if ended else "in_progress", {"succeeded": 1})

    def batch_results(self, batch_id: str) -> Iterator[BatchItemResult]:
        n = int(batch_id.removeprefix("msgbatch_s"))
        for cid, params in self.submitted[n - 1]:
            yield BatchItemResult(
                cid,
                "succeeded",
                response=BackendResponse(
                    data=surface_answer(params["messages"][0]["content"]),
                    model=params["model"],
                    input_tokens=4000,
                    output_tokens=300,
                    cost_usd=0.0022,
                    batch_id=batch_id,
                ),
            )


def coder(llm: Any, guard: Any = None, **kw: Any) -> Any:
    """A `SurfaceCoder` on `llm`, through `guard` when given (approval, caps, backend)."""
    from pigtail.briefs.surface import JOB, SurfaceCoder

    return SurfaceCoder(
        llm,
        brief_run_id=None,
        before_submit=None if guard is None else guard.before_submit,
        check_backend=None if guard is None else (lambda b: guard.check_backend(b, job=JOB)),
        sleep=lambda s: None,
        poll_seconds=0,
        **kw,
    )


def default_coder() -> Any:
    """An approved coder on a fresh in-memory client (for stage tests that don't look at it)."""
    from pigtail.config import DEFAULT_STAGE_MODELS
    from pigtail.llm import LLMClient
    from pigtail.llm.batch import MemoryBatchStore
    from pigtail.llm.redact import alias_redact
    from pigtail.llm.store import LLMStore

    llm = LLMClient(
        backends={"api": SurfaceBackend()},
        default_backend="api",
        store=LLMStore(":memory:"),
        models={str(k): v for k, v in DEFAULT_STAGE_MODELS.items()},
        redactor=alias_redact,
        use_batch=False,
        batch_store=MemoryBatchStore(),
    )
    return coder(llm)
