"""Single entry point for every product LLM call (PRD F15)."""

from pigtail.llm.client import BatchItem, BatchRun, LLMClient, build_client
from pigtail.llm.errors import (
    BackendError,
    BatchPending,
    LLMError,
    QueuePaused,
    StructuredOutputError,
    UsageLimitReached,
)
from pigtail.llm.types import LLMResult, PromptSpec

__all__ = [
    "BackendError",
    "BatchItem",
    "BatchPending",
    "BatchRun",
    "LLMClient",
    "LLMError",
    "LLMResult",
    "PromptSpec",
    "QueuePaused",
    "StructuredOutputError",
    "UsageLimitReached",
    "build_client",
]
