"""Single entry point for every product LLM call (PRD F15)."""

from pigtail.llm.client import LLMClient, build_client
from pigtail.llm.errors import (
    BackendError,
    LLMError,
    QueuePaused,
    StructuredOutputError,
    UsageLimitReached,
)
from pigtail.llm.types import LLMResult, PromptSpec

__all__ = [
    "BackendError",
    "LLMClient",
    "LLMError",
    "LLMResult",
    "PromptSpec",
    "QueuePaused",
    "StructuredOutputError",
    "UsageLimitReached",
    "build_client",
]
