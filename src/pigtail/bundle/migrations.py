"""Bundle version compatibility. Migrations are explicit; nothing is coerced silently (spec §5.2)."""

from __future__ import annotations

from pigtail.bundle.models import SCHEMA_VERSION
from pigtail.errors import UnsupportedBundleVersion

SUPPORTED = (SCHEMA_VERSION,)


def check_version(data: dict) -> None:
    v = data.get("schema_version")
    if v not in SUPPORTED:
        raise UnsupportedBundleVersion(
            f"Unsupported Research Bundle version {v!r}. This Pigtail reads {', '.join(SUPPORTED)}.",
            hint="Upgrade Pigtail, or re-run the research with this version. No automatic migration exists.",
        )
