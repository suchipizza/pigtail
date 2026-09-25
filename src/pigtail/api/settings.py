"""UI/API settings, read only from environment variables (R13.3, CB-19)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PASSWORD_HASH_ENV = "PIGTAIL_OPERATOR_PASSWORD_HASH"


def default_dist_dir() -> Path:
    """`PIGTAIL_UI_DIST`, else `ui/dist` at the repo root (src layout)."""
    env = os.environ.get("PIGTAIL_UI_DIST")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "ui" / "dist"


@dataclass(frozen=True)
class UISettings:
    # argon2id PHC string from `pigtail ui hash-password`. Required: the app is private by default.
    password_hash: str = field(repr=False)
    session_hours: float = 12.0  # absolute session lifetime
    idle_minutes: float = 120.0  # session ends after this much inactivity
    # Login rate limit, counted from the audit log (works across workers and restarts).
    login_window_minutes: float = 15.0
    login_max_failures_per_client: int = 5
    login_max_failures_total: int = 30
    # None = decide per request: Secure unless the request host is loopback (http://localhost).
    secure_cookie: bool | None = None
    dist_dir: Path = field(default_factory=default_dist_dir)
    audit_retention_days: int = 365  # CB-18 log retention ceiling

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> UISettings:
        from pigtail.config import LOG_MAX_DAYS, _days

        e = dict(os.environ) if env is None else env
        ph = (e.get(PASSWORD_HASH_ENV) or "").strip()
        if not ph:
            raise ValueError(
                f"{PASSWORD_HASH_ENV} is not set. The web app is private by default: create a "
                "hash with `pigtail ui hash-password` and export it."
            )
        if not ph.startswith("$argon2"):
            raise ValueError(
                f"{PASSWORD_HASH_ENV} must be an argon2 hash (`pigtail ui hash-password`)"
            )
        secure_raw = (e.get("PIGTAIL_UI_SECURE_COOKIE") or "").strip().lower()
        secure = None if secure_raw in ("", "auto") else secure_raw in ("1", "true", "yes")
        return cls(
            password_hash=ph,
            session_hours=float(e.get("PIGTAIL_UI_SESSION_HOURS") or 12),
            idle_minutes=float(e.get("PIGTAIL_UI_IDLE_MINUTES") or 120),
            secure_cookie=secure,
            dist_dir=Path(e["PIGTAIL_UI_DIST"]) if e.get("PIGTAIL_UI_DIST") else default_dist_dir(),
            audit_retention_days=_days(e, "LOG_RETENTION_DAYS", LOG_MAX_DAYS),
        )
