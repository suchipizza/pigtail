"""pigtail CLI (PRD F14 / R14.1). Stages not yet built exit with code 2 and name their milestone."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel

from pigtail import __version__

PENDING_STAGES = {
    "score": "M5",
    "panel": "M5",
    "extract": "M5",
    "analyze": "M5",
    "plan": "M8",
}
# `pigtail report` has its first subcommand (hn-frontpage, M1-T22); the rest is still M5.
REPORT_MILESTONE = "M5"


class SmokeOutput(BaseModel):
    """Structured-output smoke test schema (M0 acceptance)."""

    answer: int
    backend_echo: str


SMOKE_SYSTEM = "You are a test fixture. Reply only through the requested JSON schema."
SMOKE_TEMPLATE = "Compute 17 + 25 and put it in `answer`. Put the word {input} in `backend_echo`."


def cmd_llm_smoke(args: argparse.Namespace) -> int:
    from pigtail.llm import PromptSpec, build_client

    client = build_client()
    if args.backend:
        client.default_backend = args.backend
    prompt = PromptSpec(id="smoke", version="1", system=SMOKE_SYSTEM, template=SMOKE_TEMPLATE)
    res = client.complete(prompt, "pigtail", SmokeOutput, job="smoke", use_cache=False)
    ok = res.output.answer == 42
    print(json.dumps({"ok": ok, **res.provenance(), "output": res.output.model_dump()}, indent=2))
    return 0 if ok else 1


def cmd_llm_status(_: argparse.Namespace) -> int:
    from pigtail.config import Settings
    from pigtail.llm.store import LLMStore

    s = Settings.from_env()
    store = LLMStore(s.data_dir / "llm.sqlite3", retention_days=s.llm_cache_retention_days)
    out = {
        "llm_backend": s.llm_backend,
        "overrides": s.llm_backend_overrides,
        "model": s.llm_model,
        "usage": store.summary(),
        "paused_until": {
            b: (p.isoformat() if (p := store.paused_until(b)) else None)
            for b in ("subscription", "api")
        },
    }
    print(json.dumps(out, indent=2))
    return 0


def _parse_hour(value: str) -> datetime:
    """`2026-09-20T00`, `2026-09-20T00:00`, or full ISO 8601; naive means UTC."""
    for fmt in ("%Y-%m-%dT%H", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=UTC)
        except ValueError:
            pass
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"bad hour {value!r}; use YYYY-MM-DDTHH") from e
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def cmd_db_migrate(_: argparse.Namespace) -> int:
    from pigtail.config import Settings
    from pigtail.db.migrate import migrate

    s = Settings.from_env()
    if not s.database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    applied = migrate(s.database_url)
    print(f"applied {len(applied)} migration(s): {', '.join(applied) or '-'}")
    return 0


def cmd_capture_scan(args: argparse.Namespace) -> int:
    """R1.1: scan GH Archive hours [start, end) for star/fork velocity and open cases."""
    import logging

    from pigtail.capture.db import CaptureDB
    from pigtail.capture.retention import purge_raw
    from pigtail.capture.runs import RunRecorder
    from pigtail.capture.snapshots import build_store
    from pigtail.capture.velocity import VelocityConfig, VelocityScanner, scan_config_dict
    from pigtail.config import Settings
    from pigtail.connectors.gharchive import GHArchiveConnector
    from pigtail.db.migrate import migrate
    from pigtail.logsafe import configure_logging
    from pigtail.privacy import suppression
    from pigtail.pseudonymize import Pseudonymizer

    configure_logging()  # CB-18: handles, e-mails and payloads never reach the log
    s = Settings.from_env()
    if not s.database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    if not s.pseudonym_key:
        print("PSEUDONYM_KEY is not set (>= 16 chars; PRD §10)", file=sys.stderr)
        return 2
    migrate(s.database_url)
    _warn_unencrypted(s, logging.getLogger("pigtail.capture"))
    cfg = VelocityConfig(min_stars_48h=args.min_stars, sigma=args.sigma)
    db = CaptureDB.connect(s.database_url)
    config = scan_config_dict(
        cfg,
        start=args.start.isoformat(),
        end=args.end.isoformat(),
        force=args.force,
        snapshot_backend=s.snapshot_backend,
    )
    try:
        with RunRecorder("capture.scan", config, sink=db.upsert_run) as run:
            conn = GHArchiveConnector(
                store=build_store(s),
                pseudonymizer=Pseudonymizer(s.pseudonym_key),
                run=run,
                evidence_sink=db.upsert_evidence,
                suppression=suppression.load(db),  # CB-13: refusals dropped at ingest
            )
            res = VelocityScanner(connector=conn, db=db, cfg=cfg, run=run).scan(
                args.start, args.end, force=args.force
            )
            purged = purge_raw(
                db,
                conn.store,
                source=conn.name,
                retention_days=s.gharchive_raw_retention_days,
            )
            run.incr("raw_snapshots_purged", purged)
    finally:
        db.close()
    out = {
        "run_id": run.id,
        "hours_ok": res.hours_ok,
        "hours_missing": res.hours_missing,
        "hours_skipped": res.hours_skipped,
        "events": res.events,
        "cases_opened": [c.id for c in res.cases],
        "raw_snapshots_purged": purged,
    }
    print(json.dumps(out, indent=2))
    return 0


def cmd_capture_backfill_gharchive(args: argparse.Namespace) -> int:
    """M1-T19: retry missing GH Archive hours (with backoff) up to --days back."""
    from pigtail.capture.db import CaptureDB
    from pigtail.capture.gharchive_backfill import BackfillConfig, backfill_missing
    from pigtail.capture.runs import RunRecorder
    from pigtail.capture.snapshots import build_store
    from pigtail.capture.velocity import VelocityConfig, VelocityScanner, scan_config_dict
    from pigtail.config import Settings
    from pigtail.connectors.gharchive import GHArchiveConnector
    from pigtail.db.migrate import migrate
    from pigtail.privacy import suppression
    from pigtail.pseudonymize import Pseudonymizer

    s = Settings.from_env()
    if not s.database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    if not s.pseudonym_key:
        print("PSEUDONYM_KEY is not set (>= 16 chars; PRD §10)", file=sys.stderr)
        return 2
    try:
        bcfg = BackfillConfig(max_days=args.days, max_hours=args.max_hours)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    migrate(s.database_url)
    cfg = VelocityConfig(min_stars_48h=args.min_stars, sigma=args.sigma)
    db = CaptureDB.connect(s.database_url)
    config = scan_config_dict(
        cfg,
        backfill_days=bcfg.max_days,
        max_hours=bcfg.max_hours,
        snapshot_backend=s.snapshot_backend,
    )
    try:
        with RunRecorder("capture.backfill_gharchive", config, sink=db.upsert_run) as run:
            conn = GHArchiveConnector(
                store=build_store(s),
                pseudonymizer=Pseudonymizer(s.pseudonym_key),
                run=run,
                evidence_sink=db.upsert_evidence,
                suppression=suppression.load(db),
            )
            res = backfill_missing(
                VelocityScanner(connector=conn, db=db, cfg=cfg, run=run), cfg=bcfg
            )
    finally:
        db.close()
    print(json.dumps({"run_id": run.id, **res.to_dict()}, indent=2))
    return 0


def cmd_capture_purge_raw(args: argparse.Namespace) -> int:
    """DPIA CB-04: drop raw GH Archive dumps older than the retention (hash + URL are kept)."""
    from pigtail.capture.db import CaptureDB
    from pigtail.capture.retention import purge_raw
    from pigtail.capture.snapshots import build_store
    from pigtail.config import Settings

    s = Settings.from_env()
    if not s.database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    days = s.gharchive_raw_retention_days if args.retention_days is None else args.retention_days
    db = CaptureDB.connect(s.database_url)
    try:
        n = purge_raw(db, build_store(s), source="gharchive", retention_days=days)
    finally:
        db.close()
    print(json.dumps({"source": "gharchive", "retention_days": days, "purged": n}))
    return 0


def _capture_env() -> tuple[Any, int | None]:
    """Settings for capture commands, or an exit code 2 with the reason on stderr."""
    from pigtail.config import Settings

    s = Settings.from_env()
    if not s.database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return s, 2
    return s, None


def cmd_capture_hn_ranks(args: argparse.Namespace) -> int:
    """M1-T14: poll HN topstories (project-level only; no ADR-022 flag needed)."""
    import logging

    from pigtail.capture.db import CaptureDB
    from pigtail.capture.hn_ranks import RankPoller, check_interval, run_loop
    from pigtail.capture.runs import RunRecorder
    from pigtail.capture.snapshots import build_store
    from pigtail.connectors.base import ConnectorError
    from pigtail.connectors.hn_ranks import HNRanksConnector
    from pigtail.db.migrate import migrate
    from pigtail.logsafe import configure_logging
    from pigtail.privacy import suppression

    configure_logging()
    logger = logging.getLogger("pigtail.capture.hn_ranks")
    s, rc = _capture_env()
    if rc is not None:
        return rc
    try:
        interval = check_interval(args.interval_minutes * 60)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    migrate(s.database_url)
    db = CaptureDB.connect(s.database_url)
    store = build_store(s)
    results: list[dict[str, Any]] = []
    config = {"items": args.items, "interval_s": interval, "once": args.once}

    def poll() -> None:
        with RunRecorder("capture.hn_ranks", config, sink=db.upsert_run) as run:
            conn = HNRanksConnector(
                store=store,
                pseudonymizer=None,
                run=run,
                evidence_sink=db.upsert_evidence,
                suppression=suppression.load(db),
            )
            res = RankPoller(conn, db, run=run, items=args.items).poll_once()
        out = {"run_id": run.id, **res.to_dict()}
        results.append(out)
        if not args.once:
            print(json.dumps(out), flush=True)

    try:
        if args.once:
            try:
                poll()
            except ConnectorError as e:
                print(str(e), file=sys.stderr)
                return 1
            print(json.dumps(results[0], indent=2))
            return 0
        polls, failures = run_loop(
            poll,
            interval=interval,
            max_polls=args.max_polls,
            on_error=lambda e: logger.warning("hn rank poll failed: %s: %s", type(e).__name__, e),
        )
    finally:
        db.close()
    print(json.dumps({"polls": polls, "failures": failures}))
    return 0 if failures < polls or polls == 0 else 1


def cmd_capture_mentions(args: argparse.Namespace) -> int:
    """M1-T4 / R1.2: search HN (Algolia) for mentions of a repo; snapshot and store them."""
    from pigtail.capture.db import CaptureDB
    from pigtail.capture.mentions import RepoSuppressed, capture_hn_mentions, split_full_name
    from pigtail.capture.runs import RunRecorder
    from pigtail.capture.snapshots import build_store
    from pigtail.connectors.base import ConnectorError
    from pigtail.connectors.hn import HNAlgoliaConnector, HNFirebaseConnector
    from pigtail.db.migrate import migrate
    from pigtail.logsafe import configure_logging
    from pigtail.privacy import suppression
    from pigtail.pseudonymize import Pseudonymizer

    configure_logging()
    s, rc = _capture_env()
    if rc is not None:
        return rc
    if not s.pseudonym_key:
        print("PSEUDONYM_KEY is not set (>= 16 chars; PRD §10)", file=sys.stderr)
        return 2
    try:
        split_full_name(args.repo)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    migrate(s.database_url)
    db = CaptureDB.connect(s.database_url)
    config = {
        "repo": args.repo.lower(),
        "since": args.since.isoformat() if args.since else None,
        "until": args.until.isoformat() if args.until else None,
        "loose": args.loose,
        "items": not args.no_items,
    }
    try:
        with RunRecorder("capture.mentions", config, sink=db.upsert_run) as run:
            common: dict[str, Any] = {
                "store": build_store(s),
                "pseudonymizer": Pseudonymizer(s.pseudonym_key),
                "run": run,
                "evidence_sink": db.upsert_evidence,
                "suppression": suppression.load(db),
            }
            try:
                algolia = HNAlgoliaConnector(**common)
                firebase = None if args.no_items else HNFirebaseConnector(**common)
                if not algolia.enabled:
                    raise ConnectorError(
                        "HN connectors are disabled (PIGTAIL_ENABLE_HN=0, the default). They "
                        "collect person-level data: see docs/guides/operator.md, ADR-022."
                    )
                res = capture_hn_mentions(
                    algolia,
                    db,
                    args.repo,
                    firebase=firebase if firebase and firebase.enabled else None,
                    since=args.since,
                    until=args.until,
                    loose=args.loose,
                    run=run,
                )
            except (ConnectorError, RepoSuppressed) as e:
                print(str(e), file=sys.stderr)
                return 2
    finally:
        db.close()
    print(json.dumps({"run_id": run.id, **res.to_dict()}, indent=2))
    return 0


def cmd_report_hn_frontpage(args: argparse.Namespace) -> int:
    """M1-T22: HN front-page minutes for a repo from the rank history (read-only)."""
    import psycopg

    from pigtail.capture.db import CaptureDB
    from pigtail.capture.hn_frontpage import FrontpageConfig, repo_frontpage_minutes
    from pigtail.privacy import suppression

    s, rc = _capture_env()
    if rc is not None:
        return rc
    try:
        name = suppression.normalize_repo_name(args.repo)
        cfg = FrontpageConfig(
            interval=timedelta(minutes=args.interval_minutes),
            max_rank=args.max_rank,
            gap_factor=args.gap_factor,
        )
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    try:  # read-only at the server: this command never writes (no migration, no run record)
        conn = psycopg.connect(
            s.database_url, autocommit=True, options="-c default_transaction_read_only=on"
        )
    except psycopg.Error as e:
        print(f"database unreachable: {type(e).__name__}", file=sys.stderr)
        return 2
    db = CaptureDB(conn)
    try:
        try:
            refused = suppression.load(db)
        except suppression.MissingNameKey as e:
            print(str(e), file=sys.stderr)
            return 2
        row = db.conn.execute(
            "SELECT id FROM repos WHERE host = 'github' AND lower(full_name) = %s", (name,)
        ).fetchone()
        if refused.name_suppressed(name) or (row and str(row[0]) in refused.repos):
            print("the repo is on the refusal list (CB-13)", file=sys.stderr)
            return 2
        rep = repo_frontpage_minutes(db, name, since=args.since, until=args.until, cfg=cfg)
    except psycopg.errors.UndefinedTable:
        print("database not migrated (pigtail db migrate)", file=sys.stderr)
        return 2
    finally:
        db.close()
    print(json.dumps(rep.to_dict(), indent=2))
    return 0


def _warn_unencrypted(s: Any, log: Any) -> None:
    """CB-03: warn at startup when the snapshot store is not known to be encrypted."""
    from pigtail.privacy.doctor import run_checks

    try:
        checks = run_checks(s, db_check=False)
    except Exception as e:  # the check must never block a scan
        log.warning("encryption check failed: %s", type(e).__name__)
        return
    for c in checks:
        if c.name == "snapshot_bucket_encryption" and c.status != "ok":
            log.warning("CB-03 %s: %s", c.status, c.detail)


def cmd_doctor(args: argparse.Namespace) -> int:
    """CB-03: report encryption at rest and other privacy preconditions."""
    from pigtail.config import Settings
    from pigtail.privacy.doctor import exit_code, run_checks

    try:
        s = Settings.from_env()
    except ValueError as e:
        print(f"[FAIL] settings: {e}", file=sys.stderr)
        return 1
    checks = run_checks(s)
    if args.json:
        print(json.dumps([c.to_dict() for c in checks], indent=2))
    else:
        for c in checks:
            print(f"[{c.status.upper():>6}] {c.name}: {c.detail}")
    return exit_code(checks, strict=args.strict)


class _Ctx:
    """Settings plus open handles for the privacy commands."""

    def __init__(self, need_key: bool = True) -> None:
        from pigtail.capture.db import CaptureDB
        from pigtail.capture.snapshots import build_store
        from pigtail.config import Settings
        from pigtail.db.migrate import migrate
        from pigtail.llm.store import LLMStore
        from pigtail.pseudonymize import Pseudonymizer

        s = Settings.from_env()
        if not s.database_url:
            raise _UsageError("DATABASE_URL is not set")
        if need_key and not s.pseudonym_key:
            raise _UsageError("PSEUDONYM_KEY is not set (>= 16 chars; PRD §10)")
        migrate(s.database_url)
        self.settings = s
        self.db = CaptureDB.connect(s.database_url)
        self.store = build_store(s)
        self.pz = Pseudonymizer(s.pseudonym_key) if s.pseudonym_key else None
        self.llm_store = LLMStore(
            s.data_dir / "llm.sqlite3", retention_days=s.llm_cache_retention_days
        )

    def close(self) -> None:
        self.db.close()


class _UsageError(Exception):
    pass


def _read_handle(value: str | None) -> str:
    """`--handle X`, or `--handle -` / no flag to read it from stdin (keeps it out of history)."""
    if value and value != "-":
        return value
    if sys.stdin.isatty():
        print("handle: ", end="", file=sys.stderr, flush=True)
    handle = sys.stdin.readline().strip()
    if not handle:
        raise _UsageError("no handle given")
    return handle


def _privacy(fn: Any) -> Any:
    """Run a privacy command with a context; usage errors exit 2."""

    def wrapped(args: argparse.Namespace) -> int:
        try:
            ctx = _Ctx(need_key=getattr(args, "_need_key", True))
        except (_UsageError, ValueError) as e:
            print(str(e), file=sys.stderr)
            return 2
        try:
            rc: int = fn(args, ctx)
            return rc
        except _UsageError as e:
            print(str(e), file=sys.stderr)
            return 2
        finally:
            ctx.close()

    return wrapped


@_privacy
def cmd_retention_purge(args: argparse.Namespace, ctx: _Ctx) -> int:
    """CB-01 (+ CB-04, CB-05, CB-18): purge person-level data past its retention."""
    from pigtail.capture.runs import RunRecorder
    from pigtail.privacy.retention import RetentionConfig, purge

    s = ctx.settings
    cfg = RetentionConfig(
        person_level_days=s.person_level_retention_days,
        gharchive_raw_days=s.gharchive_raw_retention_days,
        log_days=s.log_retention_days,
        github_events_days=s.github_events_retention_days,
    )
    config = {
        "dry_run": args.dry_run,
        "person_level_days": cfg.person_level_days,
        "gharchive_raw_days": cfg.gharchive_raw_days,
        "github_events_days": cfg.github_events_days,
        "log_days": cfg.log_days,
        "llm_cache_days": s.llm_cache_retention_days,
        "snapshot_backend": s.snapshot_backend,
    }
    with RunRecorder("retention.purge", config, sink=ctx.db.upsert_run) as run:
        rep = purge(
            ctx.db, ctx.store, cfg=cfg, llm_store=ctx.llm_store, run=run, dry_run=args.dry_run
        )
    out = {"run_id": run.id, **rep.to_dict()}
    if len(rep.dropped_hashes) > 20:
        out["dropped_hashes"] = [*rep.dropped_hashes[:20], f"... {len(rep.dropped_hashes)} total"]
    print(json.dumps(out, indent=2))
    return 0


@_privacy
def cmd_deletion_sync(args: argparse.Namespace, ctx: _Ctx) -> int:
    """CB-02 / R1.5: re-check tracked person-level items upstream; drop what was deleted."""
    import httpx

    from pigtail.capture.runs import RunRecorder, utcnow
    from pigtail.connectors.hn import HNFirebaseConnector
    from pigtail.privacy.deletion_sync import DeletionSource, HNDeletionSource, sync

    def hn(run: RunRecorder) -> DeletionSource:
        # enabled=False: checks store nothing and must run even while HN collection is off
        conn = HNFirebaseConnector(
            store=ctx.store, pseudonymizer=ctx.pz, http=httpx.Client(), enabled=False, run=run
        )
        return HNDeletionSource(conn)

    factories = {"hn": hn}
    names = args.source or sorted(factories)
    config = {"sources": names, "dry_run": args.dry_run, "limit": args.limit}
    reports = []
    with RunRecorder("privacy.deletion_sync", config, sink=ctx.db.upsert_run) as run:
        for name in names:
            rep = sync(
                ctx.db,
                ctx.store,
                factories[name](run),
                now=utcnow(),
                llm_store=ctx.llm_store,
                run=run,
                dry_run=args.dry_run,
                limit=args.limit,
            )
            reports.append(rep.to_dict())
    print(json.dumps({"run_id": run.id, "sources": reports}, indent=2))
    return 0


def _repo_key(ctx: _Ctx, args: argparse.Namespace) -> str | None:
    """`<host>:<id>` for `--repo-id`, or for a `--repo owner/name` that is in `repos`."""
    if args.repo_id is not None:
        return f"{args.platform}:{args.repo_id}"
    if args.repo:
        row = ctx.db.conn.execute(
            "SELECT id FROM repos WHERE host = %s AND lower(full_name) = lower(%s)",
            (args.platform, _repo_name(args)),
        ).fetchone()
        return str(row[0]) if row else None
    return None


def _repo_name(args: argparse.Namespace) -> str:
    from pigtail.privacy.suppression import normalize_repo_name

    try:
        return normalize_repo_name(args.repo)
    except ValueError as e:
        raise _UsageError(str(e)) from e


@_privacy
def cmd_optout_add(args: argparse.Namespace, ctx: _Ctx) -> int:
    """CB-13: add a person (pseudonymized at once) or a repo to the refusal list, then purge."""
    from pigtail.capture.runs import RunRecorder
    from pigtail.privacy import requests

    assert ctx.pz is not None
    key = _repo_key(ctx, args)
    name = _repo_name(args) if args.repo else None
    kind = "repo" if key else "repo_name" if name else "pseudonym"
    # the run config never holds the repo name: it may contain a personal account name
    config = {"platform": args.platform, "kind": kind}
    with RunRecorder("privacy.optout", config, sink=ctx.db.upsert_run) as run:
        if key is not None:
            res = requests.optout_repo(
                ctx.db,
                ctx.store,
                platform=args.platform,
                repo_key=key,
                pz=ctx.pz,
                full_name=name,
                llm_store=ctx.llm_store,
                run=run,
                purge=not args.no_purge,
            )
        elif name is not None:  # M1-T23: not in `repos` yet; matched by name from now on
            res = requests.optout_repo_name(
                ctx.db,
                ctx.store,
                platform=args.platform,
                full_name=name,
                pz=ctx.pz,
                llm_store=ctx.llm_store,
                run=run,
                purge=not args.no_purge,
            )
        else:
            res = requests.erasure(
                ctx.db,
                ctx.store,
                ctx.pz,
                platform=args.platform,
                handle=_read_handle(args.handle),
                llm_store=ctx.llm_store,
                run=run,
                reason="objection",
                purge=not args.no_purge,
            )
    print(json.dumps({"request_id": res.request_id, "outcome": res.outcome, **res.counts}))
    return 0


@_privacy
def cmd_optout_remove(args: argparse.Namespace, ctx: _Ctx) -> int:
    from pigtail.privacy import suppression

    assert ctx.pz is not None
    key = _repo_key(ctx, args)
    if key is not None or args.repo:
        ok = False
        if key is not None:
            ok = suppression.remove(ctx.db, "repo", key)
        if args.repo:
            nk = suppression.repo_name_key(_repo_name(args), ctx.pz, args.platform)
            ok = suppression.remove(ctx.db, "repo_name", nk) or ok
            ok = suppression.remove_legacy_name(ctx.db, _repo_name(args), args.platform) or ok
    else:
        p = suppression.subject_pseudonym(ctx.pz, args.platform, _read_handle(args.handle))
        ok = suppression.remove(ctx.db, "pseudonym", p)
    print(json.dumps({"removed": ok}))
    return 0


@_privacy
def cmd_optout_list(_args: argparse.Namespace, ctx: _Ctx) -> int:
    from pigtail.privacy import suppression

    print(json.dumps(suppression.entries(ctx.db), indent=2, default=str))
    return 0


@_privacy
def cmd_optout_rekey(_args: argparse.Namespace, ctx: _Ctx) -> int:
    """CB-13b: convert legacy unkeyed repo-name entries to keyed ones where the name is known."""
    from pigtail.capture.runs import RunRecorder
    from pigtail.privacy.requests import rekey_unkeyed_names

    assert ctx.pz is not None
    with RunRecorder("privacy.optout_rekey", {}, sink=ctx.db.upsert_run) as run:
        counts = rekey_unkeyed_names(ctx.db, ctx.pz)
        for k, v in counts.items():
            run.incr(k, v)
    if counts["remaining"]:
        print(
            f"{counts['remaining']} legacy name opt-out(s) could not be converted: their names are"
            " not in local data. They stay matched; re-add each one with"
            " `pigtail privacy optout add --repo owner/name` (CB-13b).",
            file=sys.stderr,
        )
    print(json.dumps({"run_id": run.id, **counts}))
    return 0


@_privacy
def cmd_optout_purge(_args: argparse.Namespace, ctx: _Ctx) -> int:
    """CB-13: re-apply the whole refusal list to existing data (e.g. after a restore)."""
    from pigtail.capture.runs import RunRecorder
    from pigtail.privacy.requests import reapply_refusals

    assert ctx.pz is not None
    with RunRecorder("privacy.optout_purge", {}, sink=ctx.db.upsert_run) as run:
        totals = reapply_refusals(ctx.db, ctx.store, ctx.pz, llm_store=ctx.llm_store, run=run)
    print(json.dumps({"run_id": run.id, **totals}, indent=2))
    return 0


@_privacy
def cmd_privacy_request(args: argparse.Namespace, ctx: _Ctx) -> int:
    """CB-08: access (export to a local JSON file) or erasure for one platform handle."""
    from pathlib import Path

    from pigtail.capture.runs import RunRecorder
    from pigtail.privacy import requests

    assert ctx.pz is not None
    handle = _read_handle(args.handle)
    config = {"type": args.type, "platform": args.platform}
    with RunRecorder(f"privacy.{args.type}", config, sink=ctx.db.upsert_run) as run:
        if args.type == "access":
            out_dir = Path(args.out) if args.out else ctx.settings.data_dir / "requests"
            res = requests.access(
                ctx.db,
                ctx.store,
                ctx.pz,
                platform=args.platform,
                handle=handle,
                out_dir=out_dir,
                llm_store=ctx.llm_store,
                run=run,
            )
        else:
            res = requests.erasure(
                ctx.db,
                ctx.store,
                ctx.pz,
                platform=args.platform,
                handle=handle,
                llm_store=ctx.llm_store,
                run=run,
            )
    out: dict[str, Any] = {"request_id": res.request_id, "type": res.type, "outcome": res.outcome}
    out |= res.counts
    if res.export_path is not None:
        out["export_path"] = str(res.export_path)
    print(json.dumps(out, indent=2))
    return 0


@_privacy
def cmd_privacy_requests(_args: argparse.Namespace, ctx: _Ctx) -> int:
    from pigtail.privacy.requests import requests_log

    print(json.dumps(requests_log(ctx.db), indent=2, default=str))
    return 0


def cmd_export_jsonl(args: argparse.Namespace) -> int:
    """M1-T20 (PRD §7): JSONL export of the database, one file per entity."""
    from pathlib import Path

    from pigtail.capture.db import CaptureDB
    from pigtail.capture.runs import RunRecorder, git_commit
    from pigtail.config import Settings
    from pigtail.export.jsonl import ExportError, export_jsonl

    s = Settings.from_env()
    if not s.database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    tables = [t for group in (args.tables or []) for t in group.split(",") if t]
    # the run config holds no output path (it can name the operator's home directory)
    config = {"tables": tables or "all", "include_person_level": args.include_person_level}
    db = CaptureDB.connect(s.database_url)
    try:
        with RunRecorder("export.jsonl", config, sink=db.upsert_run) as run:
            try:
                res = export_jsonl(
                    s.database_url,
                    Path(args.out),
                    tables=tables or None,
                    include_person_level=args.include_person_level,
                    code_commit=git_commit(),
                )
            except ExportError as e:
                run.incr("refused")
                print(str(e), file=sys.stderr)
                return 2
            for t, info in res.tables.items():
                run.incr(f"rows.{t}", int(info["rows"]))
    finally:
        db.close()
    print(json.dumps({"run_id": run.id, "out": str(res.out_dir), **res.to_dict()}, indent=2))
    return 0


def _add_subject_args(p: argparse.ArgumentParser, repos: bool) -> None:
    from pigtail.pseudonymize import PLATFORM_NAMESPACES

    p.add_argument("--platform", required=True, choices=sorted(PLATFORM_NAMESPACES))
    p.add_argument(
        "--handle",
        help="account handle; pseudonymized at once, never stored. Omit or '-' to read stdin",
    )
    if repos:
        g = p.add_mutually_exclusive_group()
        g.add_argument("--repo-id", type=int, help="numeric repo id of an opted-out project")
        g.add_argument(
            "--repo",
            help="owner/name of an opted-out project (also if not in the DB yet: matched by name)",
        )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pigtail", description=__doc__)
    p.add_argument("--version", action="version", version=f"pigtail {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    llm = sub.add_parser("llm", help="LLM backend utilities (PRD F15)")
    llm_sub = llm.add_subparsers(dest="llm_command", required=True)
    smoke = llm_sub.add_parser("smoke", help="structured-output smoke test on a real backend")
    smoke.add_argument("--backend", choices=("subscription", "api"))
    smoke.set_defaults(func=cmd_llm_smoke)
    status = llm_sub.add_parser("status", help="backend, usage ledger and pause state")
    status.set_defaults(func=cmd_llm_status)

    db = sub.add_parser("db", help="database utilities")
    db_sub = db.add_subparsers(dest="db_command", required=True)
    db_sub.add_parser("migrate", help="apply pending migrations").set_defaults(func=cmd_db_migrate)

    cap = sub.add_parser("capture", help="capture layer (PRD F1)")
    cap_sub = cap.add_subparsers(dest="capture_command", required=True)
    scan = cap_sub.add_parser("scan", help="GH Archive velocity scan + case opening (R1.1)")
    scan.add_argument("--start", type=_parse_hour, required=True, help="first hour, UTC")
    scan.add_argument("--end", type=_parse_hour, required=True, help="end hour (exclusive), UTC")
    scan.add_argument("--min-stars", type=int, default=100, help="48 h star threshold")
    scan.add_argument("--sigma", type=float, default=3.0, help="z threshold vs 30-day baseline")
    scan.add_argument("--force", action="store_true", help="re-aggregate already scanned hours")
    scan.set_defaults(func=cmd_capture_scan)
    bf = cap_sub.add_parser(
        "backfill-gharchive", help="retry missing GH Archive hours with backoff (M1-T19)"
    )
    bf.add_argument("--days", type=int, default=7, help="retry hours up to N days back (1-30)")
    bf.add_argument("--max-hours", type=int, default=48, help="downloads per run")
    bf.add_argument("--min-stars", type=int, default=100, help="48 h star threshold")
    bf.add_argument("--sigma", type=float, default=3.0, help="z threshold vs 30-day baseline")
    bf.set_defaults(func=cmd_capture_backfill_gharchive)
    purge = cap_sub.add_parser("purge-raw", help="drop raw GH Archive dumps past retention")
    purge.add_argument("--retention-days", type=int, help="default GHARCHIVE_RAW_RETENTION_DAYS")
    purge.set_defaults(func=cmd_capture_purge_raw)
    ranks = cap_sub.add_parser(
        "hn-ranks", help="poll HN topstories ranks (M1-T14; project-level, no handles)"
    )
    mode = ranks.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="one poll, then exit")
    mode.add_argument("--loop", action="store_true", help="poll every --interval-minutes")
    ranks.add_argument("--interval-minutes", type=float, default=5.0, help="loop interval (>= 1)")
    ranks.add_argument("--max-polls", type=int, help="loop: stop after N polls")
    ranks.add_argument("--items", type=int, default=30, help="item metadata for ranks 1..N")
    ranks.set_defaults(func=cmd_capture_hn_ranks)
    men = cap_sub.add_parser("mentions", help="search HN for mentions of a repo (M1-T4, R1.2)")
    men.add_argument("--repo", required=True, help="owner/name")
    men.add_argument("--since", type=_parse_hour, help="earliest item time, UTC")
    men.add_argument("--until", type=_parse_hour, help="latest item time, UTC (default now)")
    men.add_argument("--loose", action="store_true", help="also keep repo-name-only matches")
    men.add_argument("--no-items", action="store_true", help="skip per-item Firebase snapshots")
    men.set_defaults(func=cmd_capture_mentions)
    from pigtail.capture.github_cli import add_commands as add_github_commands

    add_github_commands(cap_sub)  # `pigtail capture github …` (M1-T24, ADR-032)

    ret = sub.add_parser("retention", help="retention purge (DPIA CB-01)")
    ret_sub = ret.add_subparsers(dest="retention_command", required=True)
    rp = ret_sub.add_parser("purge", help="purge person-level data past its retention")
    rp.add_argument("--dry-run", action="store_true", help="report only; change nothing")
    rp.set_defaults(func=cmd_retention_purge, _need_key=False)

    priv = sub.add_parser("privacy", help="opt-outs and data-subject requests (CB-08, CB-13)")
    priv_sub = priv.add_subparsers(dest="privacy_command", required=True)
    opt = priv_sub.add_parser("optout", help="refusal list (CB-13)")
    opt_sub = opt.add_subparsers(dest="optout_command", required=True)
    oa = opt_sub.add_parser("add", help="add a person or repo and purge their existing data")
    _add_subject_args(oa, repos=True)
    oa.add_argument("--no-purge", action="store_true", help="only add to the list")
    oa.set_defaults(func=cmd_optout_add)
    orm = opt_sub.add_parser("remove", help="remove a person or repo from the list")
    _add_subject_args(orm, repos=True)
    orm.set_defaults(func=cmd_optout_remove)
    opt_sub.add_parser(
        "list", help="list entries (pseudonyms, repo ids, repo name hashes)"
    ).set_defaults(func=cmd_optout_list, _need_key=False)
    opt_sub.add_parser("purge", help="re-apply the whole list to existing data").set_defaults(
        func=cmd_optout_purge
    )
    opt_sub.add_parser(
        "rekey", help="convert legacy unkeyed repo-name entries to keyed hashes (CB-13b)"
    ).set_defaults(func=cmd_optout_rekey)
    req = priv_sub.add_parser("request", help="data-subject request (CB-08)")
    req.add_argument("type", choices=("access", "erasure"))
    _add_subject_args(req, repos=False)
    req.add_argument("--out", help="access: export directory (default PIGTAIL_DATA_DIR/requests)")
    req.set_defaults(func=cmd_privacy_request)
    priv_sub.add_parser("requests", help="request log (no handles)").set_defaults(
        func=cmd_privacy_requests, _need_key=False
    )
    ds = priv_sub.add_parser("deletion-sync", help="re-check upstream deletions (CB-02, R1.5)")
    ds.add_argument("--source", action="append", choices=("hn",), help="default: all")
    ds.add_argument("--dry-run", action="store_true", help="report only; change nothing")
    ds.add_argument("--limit", type=int, help="max items to re-check per source")
    # needs the key: the HN connector requires a pseudonymizer even for store-nothing checks
    ds.set_defaults(func=cmd_deletion_sync, _need_key=True)

    doc = sub.add_parser("doctor", help="privacy/encryption preconditions (DPIA CB-03)")
    doc.add_argument("--strict", action="store_true", help="exit 1 on warnings too")
    doc.add_argument("--json", action="store_true")
    doc.set_defaults(func=cmd_doctor)

    exp = sub.add_parser("export", help="exports of the database (M1-T20, PRD §7)")
    exp_sub = exp.add_subparsers(dest="export_command", required=True)
    ej = exp_sub.add_parser("jsonl", help="one JSONL file per entity, deterministic order")
    ej.add_argument("--out", required=True, help="output directory (never inside the repo)")
    ej.add_argument(
        "--tables", nargs="+", help="tables to export (space or comma separated; default: all)"
    )
    ej.add_argument(
        "--include-person-level",
        action="store_true",
        help="also export pseudonymous person-level tables (refused inside any git work tree)",
    )
    ej.set_defaults(func=cmd_export_jsonl)

    from pigtail.scheduler.cli import add_commands as add_scheduler_commands

    add_scheduler_commands(sub)  # `pigtail scheduler|health|alerts` (M1-T21)

    from pigtail.api.cli import add_parser as add_ui_parser

    add_ui_parser(sub)  # `pigtail ui hash-password|serve` (M1-T12, D1 preview)

    rep = sub.add_parser("report", help="reports (hn-frontpage: M1-T22; the rest: M5)")
    rep.set_defaults(func=lambda _a: _pending("report", REPORT_MILESTONE))
    rep_sub = rep.add_subparsers(dest="report_command")
    hfp = rep_sub.add_parser(
        "hn-frontpage", help="HN front-page minutes for a repo (rank <= 30; read-only)"
    )
    hfp.add_argument("--repo", required=True, help="owner/name (matched by story URL)")
    hfp.add_argument("--since", type=_parse_hour, help="window start, UTC (default: first poll)")
    hfp.add_argument("--until", type=_parse_hour, help="window end, UTC (default: now)")
    hfp.add_argument("--interval-minutes", type=float, default=5.0, help="poller interval")
    hfp.add_argument("--max-rank", type=int, default=30, help="front page = ranks 1..N")
    hfp.add_argument("--gap-factor", type=float, default=2.0, help="gap > factor x interval")
    hfp.set_defaults(func=cmd_report_hn_frontpage)

    for stage, milestone in PENDING_STAGES.items():
        sp = sub.add_parser(stage, help=f"(not yet implemented; {milestone})")
        sp.set_defaults(func=lambda _a, s=stage, m=milestone: _pending(s, m))
    return p


def _pending(stage: str, milestone: str) -> int:
    print(f"pigtail {stage}: not implemented yet (scheduled for {milestone})", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    from pigtail.logsafe import configure_logging, install_excepthook

    # CB-18b: every command, run by hand or by the scheduler, logs through RedactingFilter, and
    # an uncaught exception's traceback is scrubbed before it reaches stderr.
    configure_logging()
    install_excepthook()
    args = build_parser().parse_args(argv)
    rc: int = args.func(args)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
