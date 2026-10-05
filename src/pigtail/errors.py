"""Error types mapped to the frozen CLI exit codes (spec §25.8)."""

from __future__ import annotations

DOCS_URL = "https://github.com/suchipizza/pigtail/blob/main/docs"


class PigtailError(Exception):
    exit_code = 1

    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint


class UsageError(PigtailError):
    exit_code = 2


class CredentialsError(PigtailError):
    exit_code = 3


class TargetResolutionError(PigtailError):
    exit_code = 4


class ResearchError(PigtailError):
    exit_code = 5


class BundleValidationError(PigtailError):
    exit_code = 6

    def __init__(self, message: str, errors: list[str] | None = None, hint: str | None = None):
        super().__init__(message, hint)
        self.errors = errors or []


class RenderError(PigtailError):
    exit_code = 7


class SourcePolicyError(PigtailError):
    exit_code = 8


class UnsupportedBundleVersion(BundleValidationError):
    pass
