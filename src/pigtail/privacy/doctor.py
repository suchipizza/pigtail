"""`pigtail doctor`: startup checks for the privacy controls (DPIA CB-03, CB-01, CB-05, CB-13).

`pseudonym_key_fingerprint` (CB-25, ADR-043) compares the running `PSEUDONYM_KEY` with the
fingerprint stored in the database: `ok`, `fail` on a mismatch (every collector and privacy
command refuses to run), `warn` while none is recorded yet (the first command that uses the key
records it). It never records anything itself.

`optout_name_keys` (CB-13b) warns while repo-name opt-outs from before migration 0009 are still
stored as unkeyed hashes (see `pigtail.privacy.suppression`).

Encryption at rest (CB-03) can be checked from inside pigtail only for the S3 bucket
(`GetBucketEncryption`). Postgres volume encryption and the local snapshot directory depend on
the host's disk or volume encryption, which a database client cannot see; those checks report
`manual` and point at docs/guides/operator.md.

Source flags (M1-T23): `adr022_person_sources` (the ADR-022 flag), `hn_sources` (rank poller
and the person-level HN connectors) and `github_events` (per-repo events, ADR-036/038) report
what is switched on. A person-level connector switched on without the ADR-022 flag is a `fail`
(it would raise `PersonSourceHold`); the ADR-022 flag itself is a `warn`, because only the
operator can confirm its preconditions (e.g. the published notice, CB-12).

Backups (CB-17b): `backup_recipient` warns while `BACKUP_RECIPIENT` is unset (no encrypted backup
can be made); `backup_age` reports the newest `pigtail-backup-*` file in `BACKUP_DIR`: `ok` up to
2 days old, `warn` after 2 days, `fail` after 7 days or when there is none, `warn` while
`BACKUP_DIR` is unset (the directory may live on the host only, outside the app container).

Statuses: `ok`, `warn`, `fail`, `manual` (needs an operator check). The command exits 1 on any
`fail`, and with `--strict` also on `warn`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlparse

from pigtail.config import Settings

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

Status = Literal["ok", "warn", "fail", "manual"]
GUIDE = "docs/guides/operator.md#privacy-operations"
BACKUP_WARN_AGE = timedelta(days=2)  # CB-17b
BACKUP_FAIL_AGE = timedelta(days=7)


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


def _onoff(v: bool) -> str:
    return "on" if v else "off"


def source_flag_checks(s: Settings, env: Mapping[str, str]) -> list[Check]:
    """M1-T23: HN, GitHub-events and ADR-022 flag states. Never reads or prints secrets."""
    from pigtail.connectors.base import ADR022_CONTROLS, ADR022_ENV
    from pigtail.connectors.github import (
        EVENTS_ENABLE_ENV,
        TOKEN_ENV,
        GitHubRepoEventsConnector,
    )
    from pigtail.connectors.hn import HN_ENABLE_ENV, HNAlgoliaConnector, HNFirebaseConnector
    from pigtail.connectors.hn_ranks import HNRanksConnector

    out: list[Check] = []
    raw = env.get(ADR022_ENV, "").strip()
    adr022 = raw == "1"
    if adr022:
        out.append(
            Check(
                "adr022_person_sources",
                "warn",
                f"{ADR022_ENV}=1: person-level sources may run. Confirm every ADR-022 "
                f"precondition is met on this deployment ({', '.join(ADR022_CONTROLS)}; "
                f"ops/DECISIONS.md ADR-022) ({GUIDE})",
            )
        )
    else:
        detail = f"{ADR022_ENV} not set: person-level sources are held (ADR-022)"
        if raw:
            detail = f"{ADR022_ENV}={raw!r} is not '1': person-level sources are held (ADR-022)"
        out.append(Check("adr022_person_sources", "ok", detail))

    def flags(*classes: Any) -> dict[str, bool] | str:
        try:
            return {c.name: bool(c.enabled_from_env(env)) for c in classes}
        except ValueError as e:
            return str(e)

    hn = flags(HNRanksConnector, HNFirebaseConnector, HNAlgoliaConnector)
    if isinstance(hn, str):
        out.append(Check("hn_sources", "fail", f"bad HN flag value: {hn}"))
    else:
        state = ", ".join(f"{k} {_onoff(v)}" for k, v in hn.items())
        person_on = hn["hn_firebase"] or hn["hn_algolia"]
        if person_on and not adr022:
            out.append(
                Check(
                    "hn_sources",
                    "fail",
                    f"{state}: person-level HN connectors are on ({HN_ENABLE_ENV}) without "
                    f"{ADR022_ENV}=1; they will refuse to start (ADR-022, ADR-031.2)",
                )
            )
        elif not hn["hn_ranks"]:
            out.append(
                Check(
                    "hn_sources",
                    "warn",
                    f"{state}: the rank poller is off; front-page rank history can't be "
                    "backfilled (ADR-031.1)",
                )
            )
        else:
            note = "; mention capture on (person-level)" if person_on else ""
            out.append(Check("hn_sources", "ok", f"{state}{note}"))

    ev = flags(GitHubRepoEventsConnector)
    if isinstance(ev, str):
        out.append(Check("github_events", "fail", f"bad GitHub events flag value: {ev}"))
    else:
        on = ev["github_events"]
        days = s.github_events_retention_days
        if on and not adr022:
            out.append(
                Check(
                    "github_events",
                    "fail",
                    f"github_events on ({EVENTS_ENABLE_ENV}) without {ADR022_ENV}=1; it will "
                    "refuse to start (ADR-022, ADR-036)",
                )
            )
        elif on and not (env.get(TOKEN_ENV) or "").strip():
            out.append(
                Check(
                    "github_events",
                    "warn",
                    f"github_events on but {TOKEN_ENV} is not set: no call is made (TM-02)",
                )
            )
        elif on:
            out.append(
                Check(
                    "github_events",
                    "ok",
                    f"github_events on (person-level; star/fork only, raw dropped at parse, "
                    f"rows kept {days} d; CB-22, CB-23, ADR-038). The scheduler job "
                    "`gh_repo_events` also needs `enabled = true` in the schedule",
                )
            )
        else:
            out.append(
                Check("github_events", "ok", f"github_events off ({EVENTS_ENABLE_ENV} not set)")
            )
    return out


def run_checks(
    s: Settings,
    *,
    s3_client: S3Client | None = None,
    db_check: bool = True,
    env: Mapping[str, str] | None = None,
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
        if (legacy := _legacy_optout_check(s.database_url)) is not None:
            out.append(legacy)
        if len(key) >= 16 and (fp := _key_fingerprint_check(s.database_url, key)) is not None:
            out.append(fp)
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
    e = os.environ if env is None else env
    out += source_flag_checks(s, e)
    out += backup_checks(e)
    return out


def _age(td: timedelta) -> str:
    hours = int(td.total_seconds() // 3600)
    return f"{hours // 24} d {hours % 24} h" if hours >= 24 else f"{hours} h"


def backup_checks(env: Mapping[str, str], now: datetime | None = None) -> list[Check]:
    """CB-17b: is an encryption recipient configured, and how old is the newest backup?"""
    from pigtail.privacy.backup import BACKUP_DIR_ENV, RECIPIENT_ENV, backup_time

    out: list[Check] = []
    if (env.get(RECIPIENT_ENV) or "").strip():
        out.append(Check("backup_recipient", "ok", f"{RECIPIENT_ENV} set (value not shown)"))
    else:
        out.append(
            Check(
                "backup_recipient",
                "warn",
                f"{RECIPIENT_ENV} is not set: `pigtail backup create` refuses to run (CB-17)",
            )
        )
    raw = (env.get(BACKUP_DIR_ENV) or "").strip()
    if not raw:
        out.append(
            Check(
                "backup_age",
                "warn",
                f"{BACKUP_DIR_ENV} is not set: the age of the newest backup is unknown (CB-17b)",
            )
        )
        return out
    directory = Path(raw)
    if not directory.is_dir():
        out.append(Check("backup_age", "fail", f"{BACKUP_DIR_ENV} is not a directory"))
        return out
    times = [
        t for p in directory.iterdir() if p.is_file() and (t := backup_time(p.name)) is not None
    ]
    if not times:
        out.append(Check("backup_age", "fail", f"no backup in {BACKUP_DIR_ENV} (CB-17)"))
        return out
    newest = max(times)
    age = (now or datetime.now(UTC)) - newest
    detail = f"newest backup {newest:%Y-%m-%d %H:%M} UTC ({_age(age)} old)"
    if age > BACKUP_FAIL_AGE:
        out.append(Check("backup_age", "fail", f"{detail}: more than 7 days (CB-17)"))
    elif age > BACKUP_WARN_AGE:
        out.append(Check("backup_age", "warn", f"{detail}: more than 2 days"))
    else:
        out.append(Check("backup_age", "ok", detail))
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


def _key_fingerprint_check(url: str, key: str) -> Check | None:
    """CB-25: is the running key the one this database's pseudonyms were made with?"""
    import psycopg

    from pigtail.privacy.key_fingerprint import RESET_COMMAND, RUNBOOK, status
    from pigtail.pseudonymize import Pseudonymizer

    name = "pseudonym_key_fingerprint"
    try:
        with psycopg.connect(url, connect_timeout=5) as c:
            st = status(c, Pseudonymizer(key))
    except psycopg.Error:
        return None  # not migrated yet / unreachable: reported by the `database` check
    if st == "ok":
        return Check(name, "ok", "PSEUDONYM_KEY matches the database's key fingerprint")
    if st == "unset":
        return Check(
            name,
            "warn",
            "no key fingerprint recorded yet; the first capture or privacy command records it",
        )
    return Check(
        name,
        "fail",
        "PSEUDONYM_KEY does not match the database's key fingerprint: opt-outs would stop "
        "matching, so collectors and privacy commands refuse to run. Restore the original key; "
        f"a rotation uses `pigtail privacy rekey` ({RUNBOOK} §4.1), and `{RESET_COMMAND}` only "
        "the §4.2 fallback",
    )


def _legacy_optout_check(url: str) -> Check | None:
    """CB-13b: repo-name opt-outs still stored as unkeyed hashes (before migration 0009)."""
    import psycopg

    try:
        with psycopg.connect(url, connect_timeout=5) as c:
            row = c.execute(
                "SELECT count(*) FROM privacy_suppression WHERE kind = 'repo_name_unkeyed'"
            ).fetchone()
    except psycopg.Error:
        return None  # not migrated yet / unreachable: reported by the `database` check
    n = int(row[0]) if row else 0
    if n == 0:
        return Check("optout_name_keys", "ok", "repo-name opt-outs use the keyed hash")
    return Check(
        "optout_name_keys",
        "warn",
        f"{n} repo-name opt-out(s) still use the unkeyed hash (CB-13b; still matched): run "
        f"`pigtail privacy optout rekey`, then re-add any left with `optout add --repo` ({GUIDE})",
    )


def exit_code(checks: list[Check], strict: bool = False) -> int:
    bad: set[str] = {"fail", "warn"} if strict else {"fail"}
    return 1 if any(c.status in bad for c in checks) else 0
