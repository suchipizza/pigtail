from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pigtail.llm.types import BackendResponse


class LLMError(Exception):
    """Base class for LLM client errors."""


class BackendError(LLMError):
    """The backend failed in a way that is not a usage limit."""


class BilledBackendError(BackendError):
    """A response came back and was billed, but can't be used: `stop_reason` `max_tokens` or
    `refusal`, or text that isn't a JSON object (ADR-087). `response` carries the usage and cost
    the API reported (data empty), so the ledger records the spend; `kind` is `max_tokens`,
    `refusal` or `non_json_output`."""

    def __init__(self, message: str, response: BackendResponse, kind: str) -> None:
        super().__init__(message)
        self.response = response
        self.kind = kind


class StructuredOutputError(LLMError):
    """The model output did not validate against the requested schema."""


class UsageLimitReached(LLMError):
    """The backend reported a usage/rate limit (R15.5)."""

    def __init__(self, message: str, reset_at: datetime | None = None) -> None:
        super().__init__(message)
        self.reset_at = reset_at


class QueuePaused(LLMError):
    """Calls on this backend are paused until `until` after a limit hit (R15.5)."""

    def __init__(self, backend: str, until: datetime) -> None:
        super().__init__(f"{backend} backend paused until {until.isoformat()}")
        self.backend = backend
        self.until = until


class BatchPending(LLMError):
    """Batches are still running after the wait limit (R15.9). Their ids are stored, so calling
    `run_batch` again (or resuming the brief run) collects them instead of resubmitting."""

    def __init__(self, batch_ids: list[str]) -> None:
        super().__init__(f"{len(batch_ids)} batch(es) still running: {', '.join(batch_ids)}")
        self.batch_ids = list(batch_ids)


MAX_ERROR_MESSAGE_CHARS = 300
_SECRETISH = re.compile(
    r"(sk-ant-[A-Za-z0-9_\-]+|Bearer\s+\S+|(?:api[_-]?key|token|authorization)\s*[:=]\s*(?:bearer\s+)?\S+)",
    re.IGNORECASE,
)


def safe_error_message(message: object, limit: int = MAX_ERROR_MESSAGE_CHARS) -> str | None:
    """An API error message fit to store with a run and show in a private report: identifiers
    redacted (CB-18 `scrub`), anything shaped like a key or token masked, cut to `limit`
    characters. API error messages describe the request's shape, not its content; this is the
    belt to that brace. None for an empty message."""
    if message is None:
        return None
    from pigtail.logsafe import scrub

    text = _SECRETISH.sub("[secret]", " ".join(str(message).split()))
    text = scrub(text, limit=limit)
    return text or None
