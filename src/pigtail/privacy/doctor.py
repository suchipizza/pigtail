"""`pigtail doctor`: startup checks for the privacy controls (DPIA CB-03, CB-01, CB-05, CB-13).

Encryption at rest (CB-03) can be checked from inside pigtail only for the S3 bucket
(`GetBucketEncryption`). Postgres volume encryption and the local snapshot directory depend on
the host's disk or volume encryption, which a database client cannot see; those checks report
`manual` and point at docs/guides/operator.md.

Statuses: `ok`, `warn`, `fail`, `manual` (needs an operator check). The command exits 1 on any
`fail`, and with `--strict` also on `warn`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlparse

from pigtail.config import Settings

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

Status = Literal["ok", "warn", "fail", "manual"]
GUIDE = "docs/guides/operator.md#privacy-operations"


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def check_bucket_encryption(client: S3Client, bucket: str) -> Check:
    """CB-03: default server-side encryption on the snapshot bucket."""
    from botocore.exceptions import BotoCoreError, ClientError

    name = "snapshot_bucket_encryption"
    try:
        resp = client.get_bucket_encryption(Bucket=bucket)
    except ClientError as e:
        code = str(e.response.get("Error", {}).get("Code", ""))
        if code == "ServerSideEncryptionConfigurationNotFoundError":
            return Check(
                name,
                "warn",
                f"bucket {bucket!r} has no default encryption; enable SSE (S3_SSE_KEK for the "
                f"bundled SeaweedFS) or use an encrypted bucket/volume ({GUIDE})",
            )
        return Check(
            name,
            "manual",
            f"GetBucketEncryption not available ({code or 'error'}); confirm the bucket or its "
            f"volume is encrypted ({GUIDE})",
        )
    except BotoCoreError as e:
        return Check(name, "fail", f"object store unreachable: {type(e).__name__}")
    rules = resp.get("ServerSideEncryptionConfiguration", {}).get("Rules", [])
    algos = sorted(
        {
            str(r.get("ApplyServerSideEncryptionByDefault", {}).get("SSEAlgorithm", ""))
            for r in rules
        }
        - {""}
    )
    if not algos:
        return Check(name, "warn", f"bucket {bucket!r} returned an empty encryption rule set")
    return Check(
        name,
        "ok",
        f"bucket {bucket!r} default encryption: {', '.join(algos)} (applies to objects written "
        "after it was enabled)",
    )


def _endpoint_check(s: Settings) -> Check:
    host = urlparse(s.s3_endpoint or "").hostname or ""
    if not s.s3_endpoint or urlparse(s.s3_endpoint).scheme == "https":
        return Check("snapshot_transport", "ok", "S3 endpoint uses TLS or the AWS default")
    if host in ("localhost", "127.0.0.1", "::1", "objectstore"):
        return Check("snapshot_transport", "ok", "plain HTTP to a local object store")
    return Check("snapshot_transport", "warn", f"S3 endpoint {host!r} is not HTTPS")


def run_checks(
    s: Settings,
    *,
    s3_client: S3Client | None = None,
    db_check: bool = True,
) -> list[Check]:
    out: list[Check] = []

    # Pseudonym key (retention-policy.md §3).
    key = s.pseudonym_key or ""
    if not key:
        out.append(Check("pseudonym_key", "fail", "PSEUDONYM_KEY is not set"))
    elif len(key) < 16:
        out.append(Check("pseudonym_key", "fail", "PSEUDONYM_KEY is shorter than 16 characters"))
    elif len(key) < 32:
        out.append(Check("pseudonym_key", "warn", "PSEUDONYM_KEY: 32+ bytes recommended"))
    else:
        out.append(Check("pseudonym_key", "ok", "set (value not shown)"))

    # Database + migrations (CB-01/CB-08/CB-13 need migration 0003).
    if not s.database_url:
        out.append(Check("database", "fail", "DATABASE_URL is not set"))
    elif db_check:
        out.append(_db_check(s.database_url))
    out.append(
        Check(
            "postgres_volume_encryption",
            "manual",
            "cannot be verified from a database client; the Postgres data volume must sit on an "
            f"encrypted disk or volume (LUKS, cloud volume encryption, FileVault) ({GUIDE})",
        )
    )

    # Snapshot store encryption (CB-03).
    if s.snapshot_backend == "s3":
        client = s3_client
        if client is None:
            from pigtail.capture.snapshots import S3SnapshotStore

            client = S3SnapshotStore.from_settings(s).client
        out.append(check_bucket_encryption(client, s.s3_bucket))
        out.append(_endpoint_check(s))
    else:
        out.append(
            Check(
                "snapshot_bucket_encryption",
                "manual",
                f"SNAPSHOT_BACKEND=local: {s.data_dir / 'snapshots'} must be on an encrypted "
                f"disk; production should use an encrypted S3 bucket ({GUIDE})",
            )
        )

    # Retention settings (CB-01, CB-05, CB-18): report, they are capped by config.
    out.append(
        Check(
            "retention_settings",
            "ok",
            f"person-level {s.person_level_retention_days} d, GH Archive raw "
            f"{s.gharchive_raw_retention_days} d, LLM cache {s.llm_cache_retention_days} d, "
            f"run error text {s.log_retention_days} d; run `pigtail retention purge` daily",
        )
    )
    return out


def _db_check(url: str) -> Check:
    import psycopg

    from pigtail.db.migrate import default_migrations_dir, discover

    try:
        with psycopg.connect(url, connect_timeout=5) as c:
            done = {r[0] for r in c.execute("SELECT version FROM schema_migrations")}
    except psycopg.errors.UndefinedTable:
        return Check("database", "warn", "reachable, no migrations applied (pigtail db migrate)")
    except psycopg.Error as e:
        return Check("database", "fail", f"unreachable: {type(e).__name__}")
    pending = [m.version for m in discover(default_migrations_dir()) if m.version not in done]
    if pending:
        return Check("database", "warn", f"pending migrations {pending} (pigtail db migrate)")
    return Check("database", "ok", "reachable, migrations up to date")


def exit_code(checks: list[Check], strict: bool = False) -> int:
    bad: set[str] = {"fail", "warn"} if strict else {"fail"}
    return 1 if any(c.status in bad for c in checks) else 0
