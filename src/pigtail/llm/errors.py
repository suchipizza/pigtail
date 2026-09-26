from __future__ import annotations

from datetime import datetime


class LLMError(Exception):
    """Base class for LLM client errors."""


class BackendError(LLMError):
    """The backend failed in a way that is not a usage limit."""


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
