"""Single-operator authentication and audit log for the private UI (R13.3, DPIA CB-19).

- The password is checked against an argon2id hash from `PIGTAIL_OPERATOR_PASSWORD_HASH`; the
  plain password is never stored or logged.
- A session is a random 256-bit token in an HttpOnly, SameSite=Strict cookie (Secure unless the
  app is reached on a loopback host). Only its SHA-256 is stored (`ui_sessions`), so a database
  read does not yield a usable cookie. Sessions end after `session_hours` or `idle_minutes`.
- Login attempts are rate-limited per client network and globally, counted from the audit log,
  so the limit holds across workers and restarts.
- The audit log (`ui_audit_log`) records logins, logouts and snapshot views. It stores no IP: the
  client is a keyed hash of the truncated address (IPv4 /24, IPv6 /48), cut to 16 hex chars.
  That is enough to rate-limit and to spot brute force (GDPR Art. 32), and it cannot be reversed
  to an address without the deployment key (derived from the password hash, so it rotates with
  the password).
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from psycopg_pool import ConnectionPool

from pigtail.api.settings import UISettings

COOKIE_NAME = "pigtail_session"
_HASHER = PasswordHasher()  # argon2id, library defaults (RFC 9106 low-memory profile)
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("use a password of at least 12 characters")
    return _HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _HASHER.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def client_key(address: str | None, secret: str) -> str:
    """Keyed hash of the truncated client address (no IP is ever stored)."""
    key = hashlib.sha256(b"pigtail-ui-client|" + secret.encode()).digest()
    try:
        ip = ipaddress.ip_address(address or "")
    except ValueError:
        label = "unknown"  # no or non-IP peer (unix socket, test client): one shared bucket
    else:
        prefix = 24 if ip.version == 4 else 48
        label = str(ipaddress.ip_network(f"{ip}/{prefix}", strict=False))
    return hmac.new(key, label.encode(), hashlib.sha256).hexdigest()[:16]


def is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    h = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    if h.startswith("[") and "]" in h:
        h = h[: h.index("]") + 1]
    return h.lower() in _LOOPBACK_HOSTS


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    retry_after_s: int = 0


class AuthStore:
    """Sessions and audit rows (the only writes the UI process makes)."""

    def __init__(self, pool: ConnectionPool[Any], settings: UISettings) -> None:
        self.pool = pool
        self.s = settings

    # --- sessions ----------------------------------------------------------------------------
    def create_session(self, now: datetime | None = None) -> tuple[str, str]:
        """Return (cookie token, token hash)."""
        now = now or utcnow()
        token = secrets.token_urlsafe(32)
        th = token_hash(token)
        with self.pool.connection() as c:
            c.execute(
                "INSERT INTO ui_sessions (token_hash, created_at, last_seen_at, expires_at)"
                " VALUES (%s, %s, %s, %s)",
                (th, now, now, now + timedelta(hours=self.s.session_hours)),
            )
            # Housekeeping on every login: expired sessions and audit rows past retention.
            c.execute("DELETE FROM ui_sessions WHERE expires_at < %s", (now,))
            c.execute(
                "DELETE FROM ui_audit_log WHERE at < %s",
                (now - timedelta(days=self.s.audit_retention_days),),
            )
        return token, th

    def check_session(self, token: str | None, now: datetime | None = None) -> str | None:
        """Token hash of a valid session (and refresh its idle timer), else None."""
        if not token or len(token) > 200:
            return None
        now = now or utcnow()
        th = token_hash(token)
        idle = timedelta(minutes=self.s.idle_minutes)
        with self.pool.connection() as c:
            row = c.execute(
                "SELECT last_seen_at, expires_at FROM ui_sessions WHERE token_hash = %s", (th,)
            ).fetchone()
            if row is None:
                return None
            last_seen, expires = row
            if expires <= now or last_seen + idle <= now:
                c.execute("DELETE FROM ui_sessions WHERE token_hash = %s", (th,))
                return None
            if now - last_seen > timedelta(minutes=1):  # limit writes to one per minute
                c.execute(
                    "UPDATE ui_sessions SET last_seen_at = %s WHERE token_hash = %s", (now, th)
                )
        return th

    def end_session(self, token: str | None) -> None:
        if not token:
            return
        with self.pool.connection() as c:
            c.execute("DELETE FROM ui_sessions WHERE token_hash = %s", (token_hash(token),))

    # --- rate limit -------------------------------------------------------------------------
    def login_allowed(self, client: str | None, now: datetime | None = None) -> RateDecision:
        now = now or utcnow()
        window = timedelta(minutes=self.s.login_window_minutes)
        since = now - window
        with self.pool.connection() as c:
            total, oldest_total = c.execute(
                "SELECT count(*), min(at) FROM ui_audit_log"
                " WHERE event = 'login_failure' AND at > %s",
                (since,),
            ).fetchone() or (0, None)
            mine, oldest_mine = (0, None)
            if client is not None:
                mine, oldest_mine = c.execute(
                    "SELECT count(*), min(at) FROM ui_audit_log"
                    " WHERE event = 'login_failure' AND client = %s AND at > %s",
                    (client, since),
                ).fetchone() or (0, None)
        for n, limit, oldest in (
            (mine, self.s.login_max_failures_per_client, oldest_mine),
            (total, self.s.login_max_failures_total, oldest_total),
        ):
            if n >= limit and oldest is not None:
                retry = int((oldest + window - now).total_seconds()) + 1
                return RateDecision(False, max(retry, 1))
        return RateDecision(True)

    # --- audit ------------------------------------------------------------------------------
    def audit(
        self,
        event: str,
        route: str,
        *,
        status: int | None = None,
        evidence_id: str | None = None,
        content_hash: str | None = None,
        session: str | None = None,
        client: str | None = None,
        at: datetime | None = None,
    ) -> None:
        with self.pool.connection() as c:
            c.execute(
                "INSERT INTO ui_audit_log (at, event, route, status, evidence_id, content_hash,"
                " session, client) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    at or utcnow(),
                    event,
                    route[:200],
                    status,
                    evidence_id,
                    content_hash,
                    session[:16] if session else None,
                    client,
                ),
            )
