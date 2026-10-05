"""Read and validate a Research Bundle from disk."""

from __future__ import annotations

import json
from pathlib import Path

from pigtail.bundle.migrations import check_version
from pigtail.bundle.models import ResearchBundle
from pigtail.bundle.validator import ValidationReport, validate_data
from pigtail.errors import BundleValidationError, UsageError


def load_raw(path: Path) -> dict:
    if not path.exists():
        raise UsageError(f"Bundle file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BundleValidationError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise BundleValidationError(f"{path} does not contain a JSON object")
    return data


def read_bundle(path: Path, *, published: bool = False) -> tuple[ResearchBundle, ValidationReport]:
    data = load_raw(path)
    check_version(data)
    report = validate_data(data, published=published)
    if not report.ok:
        raise BundleValidationError(f"{path} failed validation", errors=report.errors)
    return ResearchBundle.model_validate(data), report
