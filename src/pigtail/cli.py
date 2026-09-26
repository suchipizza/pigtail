"""pigtail CLI (PRD F14 / R14.1). Stages not yet built exit with code 2 and name their milestone."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel

from pigtail import __version__

PENDING_STAGES = {  # v2 milestones (WORK_ORDER v2.0)
    "score": "M13",
    "panel": "M13",
    "extract": "M15",
    "analyze": "M15",
    "plan": "M16",
}
# `pigtail report` subcommands: hn-frontpage (M1-T22), inventory (M11); neighbourhood report: M15.
REPORT_MILESTONE = "M15"


class SmokeOutput(BaseModel):
    """Structured-output smoke test schema (M0 acceptance)."""

    answer: int
    backend_echo: str


# the opt-out key (ADR-071.1); PSEUDONYM_KEY is its earlier name, still read
KEY_MISSING = "OPTOUT_KEY (or its alias PSEUDONYM_KEY) is not set (>= 16 chars; ADR-071.1)"
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
        "models": s.llm_models,  # R15.8, per stage (LLM_MODEL_<STAGE>, fallback LLM_MODEL)
        "batch": s.llm_batch,
        "budget_usd_month": s.budget_usd_month,
        "usage": store.summary(),
        "paused_until": {
            b: (p.isoformat() if (p := store.paused_until(b)) else None)
            for b in ("subscription", "api")
        },
    }
    print(json.dumps(out, indent=2))
    return 0


def cmd_llm_cache_clear(args: argparse.Namespace) -> int:
    """CB-28: delete cached LLM outputs (all with --all --yes, or older than N days). The usage
    ledger and pause state are kept."""
    from pigtail.config import Settings
    from pigtail.llm.store import LLMStore

    if args.all == (args.older_than is not None):
        print("give exactly one of --older-than DAYS or --all", file=sys.stderr)
        return 2
    if args.all and not (args.yes or args.dry_run):
        print("--all deletes every cached output; re-run with --yes", file=sys.stderr)
        return 2
    if args.older_than is not None and args.older_than < 0:
        print("--older-than must be >= 0", file=sys.stderr)
        return 2
    s = Settings.from_env()
    store = LLMStore(s.data_dir / "llm.sqlite3", retention_days=s.llm_cache_retention_days)
    older = None if args.all else timedelta(days=args.older_than)
    n = store.clear(older_than=older, dry_run=args.dry_run)
    out = {
        "scope": "all" if args.all else f"older_than_{args.older_than}d",
        "dry_run": args.dry_run,
        "cache_rows_deleted" if not args.dry_run else "cache_rows_matching": n,
        "cache_rows_left": store.cache_count(),
    }
    print(json.dumps(out))
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


def cmd_capture_purge_raw(args: argparse.Namespace) -> int:
    """DPIA CB-04: drop raw GH Archive dumps older than the retention (hash + URL are kept)."""
    from pigtail.capture.db import CaptureDB
    from pigtail.capture.retention import purge_raw
    from pigtail.capture.snapshots import build_store
    from pigtail.config import GHARCHIVE_RAW_MAX_DAYS, Settings

    s = Settings.from_env()
    if not s.database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    days = s.gharchive_raw_retention_days if args.retention_days is None else args.retention_days
    if not 0 <= days <= GHARCHIVE_RAW_MAX_DAYS:  # CB-32: retention-policy ceiling
        print(
            f"--retention-days must be between 0 and {GHARCHIVE_RAW_MAX_DAYS} (retention policy)",
            file=sys.stderr,
        )
        return 2
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
    from pigtail.capture.mentions import (
        NotShortlisted,
        RepoSuppressed,
        capture_hn_mentions,
        split_full_name,
    )
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
        print(KEY_MISSING, file=sys.stderr)
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
            except (ConnectorError, RepoSuppressed, NotShortlisted) as e:
                print(str(e), file=sys.stderr)
                return 2
    finally:
        db.close()
    print(json.dumps({"run_id": run.id, **res.to_dict()}, indent=2))
    return 0


def cmd_report_inventory(args: argparse.Namespace) -> int:
    """M11 (WORK_ORDER §4.4, ADR-047.6): data-cache inventory, counts only (read-only)."""
    import psycopg

    from pigtail.capture.inventory import inventory, render_text
    from pigtail.capture.runs import git_commit

    s, rc = _capture_env()
    if rc is not None:
        return rc
    try:  # read-only at the server: no migration, no run record
        with psycopg.connect(
            s.database_url, autocommit=True, options="-c default_transaction_read_only=on"
        ) as conn:
            inv = inventory(conn)
    except psycopg.Error as e:
        print(f"database unreachable: {type(e).__name__}", file=sys.stderr)
        return 2
    commit = git_commit()
    if args.json:
        print(json.dumps({"code_commit": commit, **inv.to_dict()}, indent=2))
    else:
        print(render_text(inv, commit))
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

    def __init__(self, need_key: bool = True, key_check: bool = True) -> None:
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
            raise _UsageError(KEY_MISSING)
        migrate(s.database_url)
        self.settings = s
        self.db = CaptureDB.connect(s.database_url)
        self.store = build_store(s)
        self.pz = Pseudonymizer(s.pseudonym_key) if s.pseudonym_key else None
        if need_key and key_check and self.pz is not None:
            from pigtail.privacy.key_fingerprint import verify

            try:  # CB-25: refuse under a changed key (recorded on first use)
                verify(self.db.conn, self.pz)
            except BaseException:
                self.db.close()
                raise
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
            ctx = _Ctx(
                need_key=getattr(args, "_need_key", True),
                key_check=getattr(args, "_key_check", True),
            )
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


def _retention_cfg(s: Any) -> Any:
    from pigtail.privacy.retention import RetentionConfig

    return RetentionConfig(
        person_level_days=s.person_level_retention_days,
        gharchive_raw_days=s.gharchive_raw_retention_days,
        log_days=s.log_retention_days,
        github_events_days=s.github_events_retention_days,
        after_report_days=s.snapshot_after_report_days,
    )


@_privacy
def cmd_retention_purge(args: argparse.Namespace, ctx: _Ctx) -> int:
    """R19.9 / CB-01 (+ CB-04, CB-05, CB-18): purge raw snapshots past report final + 12 months
    (or the PERSON_LEVEL_RETENTION_DAYS ceiling) and other data past its retention."""
    from pigtail.capture.runs import RunRecorder
    from pigtail.privacy.retention import purge

    s = ctx.settings
    cfg = _retention_cfg(s)
    config = {
        "dry_run": args.dry_run,
        "person_level_days": cfg.person_level_days,
        "gharchive_raw_days": cfg.gharchive_raw_days,
        "github_events_days": cfg.github_events_days,
        "after_report_days": cfg.after_report_days,
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
def cmd_retention_report_final(args: argparse.Namespace, ctx: _Ctx) -> int:
    """R19.9: record when a brief version's report became final (the snapshot retention anchor:
    snapshots its runs used are purged 12 months later)."""
    from pigtail.capture.runs import RunRecorder
    from pigtail.privacy.snapshot_retention import mark_report_final

    at = args.at or datetime.now(UTC)
    # ids and versions only: brief content never goes into run records (R18.9)
    config = {"brief_version": args.version, "at": at.isoformat()}
    with RunRecorder("retention.report_final", config, sink=ctx.db.upsert_run) as run:
        mark_report_final(
            ctx.db, args.brief, args.version, at=at, brief_run_id=args.brief_run, run_id=run.id
        )
    print(json.dumps({"run_id": run.id, "brief_version": args.version, "at": at.isoformat()}))
    return 0


@_privacy
def cmd_capture_shortlist_set(args: argparse.Namespace, ctx: _Ctx) -> int:
    """Directive §8.3: put repos in (or out of) mention scope for one brief version."""
    from pigtail.capture import scope

    try:
        n = scope.set_entries(ctx.db, args.brief, args.version, args.repo, args.status)
    except ValueError as e:
        raise _UsageError(str(e)) from e
    print(json.dumps({"entries_written": n, "status": args.status}))
    return 0


@_privacy
def cmd_capture_shortlist_list(_args: argparse.Namespace, ctx: _Ctx) -> int:
    from pigtail.capture import scope

    print(json.dumps(scope.entries(ctx.db), indent=2, default=str))
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
    """CB-13: add a person (as an opt-out fingerprint, ADR-071.1) or a repo to the refusal list,
    then purge."""
    from pigtail.capture.runs import RunRecorder
    from pigtail.privacy import requests

    assert ctx.pz is not None
    key = _repo_key(ctx, args)
    name = _repo_name(args) if args.repo else None
    kind = "repo" if key else "repo_name" if name else "person"
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
        p = suppression.subject_fingerprint(ctx.pz, args.platform, _read_handle(args.handle))
        ok = suppression.remove(ctx.db, "person", p)
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


def cmd_backup_create(args: argparse.Namespace) -> int:
    """CB-17: encrypted backup (pg_dump + snapshot manifest) to `--out`, outside any git tree."""
    import os
    from pathlib import Path

    from pigtail.capture.db import CaptureDB
    from pigtail.capture.runs import RunRecorder
    from pigtail.config import Settings
    from pigtail.privacy.backup import BACKUP_DIR_ENV, RECIPIENT_ENV, BackupError, create

    s = Settings.from_env()
    if not s.database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    out_dir = args.out or os.environ.get(BACKUP_DIR_ENV, "").strip()
    if not out_dir:
        print(f"give --out or set {BACKUP_DIR_ENV}", file=sys.stderr)
        return 2
    db = CaptureDB.connect(s.database_url)
    try:
        # the run config holds no output path and no recipient
        with RunRecorder(
            "backup.create", {"snapshot_backend": s.snapshot_backend}, sink=db.upsert_run
        ) as run:
            try:
                res = create(
                    db,
                    s.database_url,
                    Path(out_dir),
                    recipient=os.environ.get(RECIPIENT_ENV),
                    snapshot_backend=s.snapshot_backend,
                )
            except BackupError as e:
                run.incr("refused")
                print(str(e), file=sys.stderr)
                return 2
            run.incr("bytes", res.bytes)
            run.incr("snapshot_hashes", res.snapshot_hashes)
    finally:
        db.close()
    out = {"run_id": run.id, **res.to_dict()}
    rc = _backup_briefs(s, Path(res.path), out)  # R18.9 / ADR-071.3: briefs are backed up too
    print(json.dumps(out, indent=2))
    return rc


def _backup_briefs(s: Any, db_backup: Any, out: dict[str, Any]) -> int:
    """ADR-071.3: the encrypted briefs archive beside a database backup (same timestamp)."""
    import os

    from pigtail.briefs.backup import DB_NAME_RE, create_briefs_archive
    from pigtail.privacy.backup import RECIPIENT_ENV, BackupError

    m = DB_NAME_RE.match(db_backup.name)
    try:
        br = create_briefs_archive(
            s.briefs_dir,
            db_backup.parent,
            recipient=os.environ.get(RECIPIENT_ENV),
            stamp=m.group(1) if m else None,
        )
    except BackupError as e:
        out["briefs"] = {"error": str(e)}
        print(f"briefs archive failed (the database backup was written): {e}", file=sys.stderr)
        return 2
    out["briefs"] = br.to_dict()
    return 0


def _restore_briefs(args: argparse.Namespace, s: Any, backup: Any) -> dict[str, Any] | None:
    """ADR-071.3: restore the briefs archive taken with `backup` (or `--briefs-in`) into
    PIGTAIL_BRIEFS_DIR; existing versions are never overwritten."""
    import os
    from pathlib import Path

    from pigtail.briefs.backup import companion_of, restore_briefs_archive
    from pigtail.privacy.backup import IDENTITY_ENV, BackupError

    if args.no_briefs:
        return None
    path = Path(args.briefs_in) if args.briefs_in else companion_of(backup)
    if path is None or not path.is_file():
        print(
            "warning: no briefs archive found next to the backup; briefs were not restored"
            " (pass --briefs-in FILE)",
            file=sys.stderr,
        )
        return {"status": "not_found"}
    try:
        res = restore_briefs_archive(
            path, s.briefs_dir, identity=args.identity or os.environ.get(IDENTITY_ENV)
        )
    except BackupError as e:
        print(f"briefs not restored: {e}", file=sys.stderr)
        return {"status": "failed", "error": str(e)}
    d = res.to_dict()
    d.pop("conflict_paths")
    if res.conflicts:
        print(
            f"warning: {res.conflicts} brief version(s) in the archive differ from the ones in"
            f" {s.briefs_dir}; the existing files were kept",
            file=sys.stderr,
        )
    return {"status": "restored", "archive": path.name, **d}


def cmd_backup_restore(args: argparse.Namespace) -> int:
    """CB-17: restore a backup into DATABASE_URL, then re-apply every deletion."""
    import os
    from pathlib import Path

    from pigtail.capture.db import CaptureDB
    from pigtail.capture.runs import RunRecorder
    from pigtail.capture.snapshots import build_store
    from pigtail.config import Settings
    from pigtail.llm.store import LLMStore
    from pigtail.privacy.backup import IDENTITY_ENV, BackupError, restore
    from pigtail.privacy.requests import reapply_refusals
    from pigtail.privacy.retention import purge
    from pigtail.pseudonymize import Pseudonymizer

    s = Settings.from_env()
    if not s.database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    if not s.pseudonym_key:
        print(
            "OPTOUT_KEY (or PSEUDONYM_KEY) is not set: the opt-out list cannot be re-applied",
            file=sys.stderr,
        )
        return 2
    if not args.yes:
        print(
            "restore REPLACES the database at DATABASE_URL (stop the scheduler first);"
            " re-run with --yes",
            file=sys.stderr,
        )
        return 2
    pz = Pseudonymizer(s.pseudonym_key)
    if (rc := _restore_key_precheck(s.database_url, pz)) is not None:
        return rc
    store = build_store(s)
    llm = LLMStore(s.data_dir / "llm.sqlite3", retention_days=s.llm_cache_retention_days)
    runs: dict[str, str] = {}

    def reapply(db: CaptureDB) -> dict[str, int]:
        with RunRecorder("privacy.optout_purge", {"after": "restore"}, sink=db.upsert_run) as r:
            runs["optout_purge"] = r.id
            return reapply_refusals(db, store, pz, llm_store=llm, run=r)

    def retention(db: CaptureDB) -> dict[str, Any]:
        with RunRecorder("retention.purge", {"after": "restore"}, sink=db.upsert_run) as r:
            runs["retention_purge"] = r.id
            rep = purge(db, store, cfg=_retention_cfg(s), llm_store=llm, run=r).to_dict()
        rep["dropped_hashes"] = len(rep["dropped_hashes"])
        return rep

    try:
        res = restore(
            Path(args.input),
            s.database_url,
            store,
            reapply=reapply,
            retention=retention,
            identity=args.identity or os.environ.get(IDENTITY_ENV),
            llm_store=llm,
        )
    except BackupError as e:
        print(str(e), file=sys.stderr)
        return 1
    db = CaptureDB.connect(s.database_url)
    try:
        config = {"backup_created_at": res.manifest.get("created_at")}
        with RunRecorder("backup.restore", config, sink=db.upsert_run) as run:
            for k, v in {
                **res.replay,
                **{f"carried.{k}": v for k, v in res.carried.items()},
            }.items():
                run.incr(k, v)
    finally:
        db.close()
    out = {"run_id": run.id, "runs": runs, **res.to_dict()}
    out["briefs"] = _restore_briefs(args, s, Path(args.input))
    if res.carry_over_source != "live":
        print(
            f"warning: no live database to carry over from ({res.carry_over_source}): deletions"
            " and opt-outs recorded after this backup was taken are not in it. Re-run"
            " `pigtail privacy optout add` for any you know of.",
            file=sys.stderr,
        )
    print(json.dumps(out, indent=2, default=str))
    return 0


def _restore_key_precheck(database_url: str, pz: Any) -> int | None:
    """CB-25: before a restore replaces anything, the running key must match the live
    database's fingerprint (the carried-over opt-outs are keyed with it). Exit 2 on mismatch."""
    import psycopg

    from pigtail.privacy.key_fingerprint import KeyFingerprintMismatch, status

    try:
        with psycopg.connect(database_url, autocommit=True, connect_timeout=5) as c:
            st = status(c, pz)
    except psycopg.Error:
        return None  # no live database or not migrated: nothing to compare against
    if st == "mismatch":
        print(str(KeyFingerprintMismatch()), file=sys.stderr)
        return 2
    return None


@_privacy
def cmd_key_fingerprint(args: argparse.Namespace, ctx: _Ctx) -> int:
    """CB-25: show the pseudonym-key fingerprint status, or re-record it after a documented
    compromise rotation (`--reset --confirm-rotation`). Never prints the key."""
    from pigtail.capture.runs import RunRecorder
    from pigtail.privacy import key_fingerprint as kf

    assert ctx.pz is not None
    if args.reset:
        if not args.confirm_rotation:
            raise _UsageError(
                "--reset re-keys the database's key check: opt-outs made under the old key stop"
                " matching. Rotate with `pigtail privacy rekey` instead; reset only in the fallback"
                f" procedure in {kf.RUNBOOK} §4.2 (old key lost); re-run with --confirm-rotation"
            )
        config = {"reason": "compromise_rotation"}
        with RunRecorder("privacy.key_fingerprint_reset", config, sink=ctx.db.upsert_run) as run:
            old, new = kf.reset(ctx.db.conn, ctx.pz, run_id=run.id)
            run.incr("fingerprint_changed", int(old != new))
        out: dict[str, Any] = {
            "run_id": run.id,
            "status": "ok",
            "old_fingerprint": old,
            "fingerprint": new,
        }
        print(
            "fingerprint reset: `pigtail backup restore` now refuses backups taken before this"
            " reset (CB-35); take a fresh backup.",
            file=sys.stderr,
        )
    else:
        stored = kf.stored(ctx.db.conn)
        out = {
            "status": kf.status(ctx.db.conn, ctx.pz),
            "fingerprint": stored.fingerprint if stored else None,
            "running_key_fingerprint": ctx.pz.fingerprint(),
            "set_at": stored.set_at if stored else None,
            "set_by": stored.set_by if stored else None,
            "history": kf.history(ctx.db.conn),
        }
    print(json.dumps(out, indent=2, default=str))
    return 0 if out["status"] != "mismatch" else 1


@_privacy
def cmd_privacy_rekey(args: argparse.Namespace, ctx: _Ctx) -> int:
    """CB-26: rotate PSEUDONYM_KEY by re-deriving every stored pseudonym in one transaction.

    The new key is `PSEUDONYM_KEY`; the old one is read from the environment variable named by
    `--old-key-env` (never from an argument or a file). See `pigtail.privacy.rekey`."""
    from pathlib import Path

    from pigtail.capture.runs import RunRecorder
    from pigtail.privacy import rekey as rk

    assert ctx.pz is not None
    if not (args.dry_run or args.confirm_rotation):
        raise _UsageError(
            "rekey rewrites every stored pseudonym and switches the database to the new key."
            " Stop every writer, take a backup, run it with --dry-run first, then re-run with"
            " --confirm-rotation (key-rotation runbook §4.1)"
        )
    try:
        old = rk.old_key_from_env(args.old_key_env)
        handles = rk.read_handles_file(Path(args.handles_file)) if args.handles_file else []
    except ValueError as e:  # HandlesFileError too; messages never contain a key or a handle
        raise _UsageError(str(e)) from None
    config = {
        "dry_run": args.dry_run,
        "handles_file": bool(args.handles_file),  # never the path or its content
        "purge_person_level": args.purge_person_level,
        "drop_unmapped": args.drop_unmapped,
        "snapshot_scan": not args.no_snapshot_scan,
    }
    refused: rk.RekeyRefused | None = None
    with RunRecorder("privacy.rekey", config, sink=ctx.db.upsert_run) as run:
        try:
            rep = rk.rekey(
                ctx.db,
                ctx.store,
                old,
                ctx.pz,
                handles=handles,
                purge_person_level=args.purge_person_level,
                drop_unmapped=args.drop_unmapped,
                scan_snapshots=not args.no_snapshot_scan,
                dry_run=args.dry_run,
                llm_store=ctx.llm_store,
                run=run,
            )
        except rk.RekeyRefused as e:
            refused = e
            run.incr("refused")
    if refused is not None:
        print(str(refused), file=sys.stderr)
        if refused.report is not None:
            print(json.dumps({"run_id": run.id, **refused.report.to_dict()}, indent=2))
        return 2
    print(json.dumps({"run_id": run.id, **rep.to_dict()}, indent=2))
    if rep.committed:
        print(
            f"rotation committed. Unset {args.old_key_env}; keep the old key sealed until backups"
            " taken before now have been pruned (35 days), then destroy it. Delete person-level"
            " JSONL exports made before the rotation.",
            file=sys.stderr,
        )
    return 0


def cmd_backup_prune(args: argparse.Namespace) -> int:
    """CB-17: delete backups older than 35 days (retention-policy §2)."""
    import os
    from pathlib import Path

    from pigtail.privacy.backup import BACKUP_DIR_ENV, BackupError, prune

    directory = args.dir or os.environ.get(BACKUP_DIR_ENV, "").strip()
    if not directory:
        print(f"give --dir or set {BACKUP_DIR_ENV}", file=sys.stderr)
        return 2
    from pigtail.briefs.backup import prune_briefs_archives

    try:
        res = prune(Path(directory), days=args.days, dry_run=args.dry_run)
        briefs = prune_briefs_archives(Path(directory), days=args.days, dry_run=args.dry_run)
    except BackupError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(json.dumps({**res.to_dict(), "briefs": briefs.to_dict()}, indent=2))
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
    cache = llm_sub.add_parser("cache", help="LLM result cache (CB-05, CB-28)")
    cache_sub = cache.add_subparsers(dest="cache_command", required=True)
    cc = cache_sub.add_parser("clear", help="delete cached outputs; the usage ledger is kept")
    cc.add_argument("--older-than", type=int, metavar="DAYS", help="only rows older than DAYS")
    cc.add_argument("--all", action="store_true", help="every cached output")
    cc.add_argument("--yes", action="store_true", help="confirm --all")
    cc.add_argument("--dry-run", action="store_true", help="count only; delete nothing")
    cc.set_defaults(func=cmd_llm_cache_clear)

    db = sub.add_parser("db", help="database utilities")
    db_sub = db.add_subparsers(dest="db_command", required=True)
    db_sub.add_parser("migrate", help="apply pending migrations").set_defaults(func=cmd_db_migrate)

    cap = sub.add_parser("capture", help="capture layer (PRD F1)")
    cap_sub = cap.add_subparsers(dest="capture_command", required=True)
    purge = cap_sub.add_parser("purge-raw", help="drop raw GH Archive dumps past retention")
    purge.add_argument("--retention-days", type=int, help="default GHARCHIVE_RAW_RETENTION_DAYS")
    purge.set_defaults(func=cmd_capture_purge_raw)
    ranks = cap_sub.add_parser(
        "hn-ranks", help="poll HN topstories ranks (M1-T14; project-level, no handles)"
    )
    mode = ranks.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="one poll, then exit")
    mode.add_argument(
        "--loop", action="store_true", help="poll every --interval-minutes (manual; ADR-049.1)"
    )
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
    sl = cap_sub.add_parser(
        "shortlist", help="mention scope: shortlisted projects only (Directive §8.3)"
    )
    sl_sub = sl.add_subparsers(dest="shortlist_command", required=True)
    sls = sl_sub.add_parser("set", help="put repos of a brief version's shortlist in scope")
    sls.add_argument("--brief", required=True, help="brief id")
    sls.add_argument("--version", type=int, required=True, help="brief version")
    sls.add_argument(
        "--status", required=True, choices=["in_review", "final", "removed"],
        help="in_review and final are in mention scope; removed is not",
    )  # fmt: skip
    sls.add_argument("--repo", action="append", required=True, help="owner/name (repeatable)")
    sls.set_defaults(func=cmd_capture_shortlist_set, _need_key=False)
    sl_sub.add_parser("list", help="shortlist entries (private terminal output)").set_defaults(
        func=cmd_capture_shortlist_list, _need_key=False
    )
    from pigtail.capture.github_cli import add_commands as add_github_commands

    add_github_commands(cap_sub)  # `pigtail capture github …` (M1-T24, ADR-032)

    ret = sub.add_parser("retention", help="retention purge (DPIA CB-01)")
    ret_sub = ret.add_subparsers(dest="retention_command", required=True)
    rp = ret_sub.add_parser("purge", help="purge person-level data past its retention")
    rp.add_argument("--dry-run", action="store_true", help="report only; change nothing")
    rp.set_defaults(func=cmd_retention_purge, _need_key=False)
    rf = ret_sub.add_parser(
        "report-final",
        help="record that a brief version's report is final (R19.9 snapshot retention anchor)",
    )
    rf.add_argument("--brief", required=True, help="brief id")
    rf.add_argument("--version", type=int, required=True, help="brief version")
    rf.add_argument("--at", type=_parse_hour, help="when it became final (UTC; default now)")
    rf.add_argument("--brief-run", help="the brief run the final report came from")
    rf.set_defaults(func=cmd_retention_report_final, _need_key=False)

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
    kfp = priv_sub.add_parser(
        "key-fingerprint",
        help="pseudonym-key fingerprint status; --reset after a compromise rotation (CB-25)",
    )
    kfp.add_argument(
        "--reset", action="store_true", help="record the running key as the database's key"
    )
    kfp.add_argument(
        "--confirm-rotation",
        action="store_true",
        help="confirm the fallback rotation procedure (key-rotation runbook §4.2) was followed",
    )
    kfp.set_defaults(func=cmd_key_fingerprint, _need_key=True, _key_check=False)
    rkp = priv_sub.add_parser(
        "rekey",
        help="rotate PSEUDONYM_KEY: re-derive every stored pseudonym in one transaction (CB-26)",
    )
    rkp.add_argument(
        "--old-key-env",
        required=True,
        metavar="NAME",
        help="NAME of the environment variable holding the old key (never the key itself)",
    )
    rkp.add_argument(
        "--handles-file",
        metavar="PATH",
        help="private file outside any git tree (mode 0600): '<platform> <handle>' and"
        " 'repo <owner/name>' lines from the original opt-out requests",
    )
    rkp.add_argument(
        "--purge-person-level",
        action="store_true",
        help="delete every person-level row instead of mapping it",
    )
    rkp.add_argument(
        "--drop-unmapped",
        action="store_true",
        help="delete person-level rows whose pseudonym cannot be mapped (else: refuse)",
    )
    rkp.add_argument(
        "--no-snapshot-scan",
        action="store_true",
        help="do not re-parse retained snapshots to map person-level rows",
    )
    rkp.add_argument("--dry-run", action="store_true", help="report only; roll everything back")
    rkp.add_argument(
        "--confirm-rotation",
        action="store_true",
        help="confirm the rotation procedure (key-rotation runbook §4.1) is being followed",
    )
    # the running key is the NEW key: it cannot match the stored fingerprint yet
    rkp.set_defaults(func=cmd_privacy_rekey, _need_key=True, _key_check=False)
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

    bk = sub.add_parser("backup", help="encrypted backups and restore (DPIA CB-17)")
    bk_sub = bk.add_subparsers(dest="backup_command", required=True)
    bc = bk_sub.add_parser(
        "create", help="pg_dump + snapshot manifest, encrypted to BACKUP_RECIPIENT (age or gpg)"
    )
    bc.add_argument("--out", help="output directory (never inside a git tree; default $BACKUP_DIR)")
    bc.set_defaults(func=cmd_backup_create)
    br = bk_sub.add_parser(
        "restore", help="replace the database with a backup, then re-apply all deletions"
    )
    br.add_argument("--in", dest="input", required=True, help="backup file (.age or .gpg)")
    br.add_argument("--identity", help="age identity file (default: $BACKUP_IDENTITY)")
    br.add_argument("--yes", action="store_true", help="confirm: the database is replaced")
    br.add_argument(
        "--briefs-in", help="briefs archive (default: the one taken with the backup, ADR-071.3)"
    )
    br.add_argument("--no-briefs", action="store_true", help="don't restore the briefs archive")
    br.set_defaults(func=cmd_backup_restore)
    bp = bk_sub.add_parser("prune", help="delete backups older than 35 days")
    bp.add_argument("--dir", help="backup directory (default $BACKUP_DIR)")
    bp.add_argument("--days", type=int, default=35, help="keep this many days (max 35)")
    bp.add_argument("--dry-run", action="store_true", help="list only; delete nothing")
    bp.set_defaults(func=cmd_backup_prune)

    from pigtail.scheduler.cli import add_commands as add_scheduler_commands

    add_scheduler_commands(sub)  # `pigtail scheduler|health|alerts` (M1-T21)

    from pigtail.briefs.cli import add_commands as add_brief_commands

    add_brief_commands(sub)  # `pigtail brief ...` (M12, PRD F18)

    from pigtail.api.cli import add_parser as add_ui_parser

    add_ui_parser(sub)  # `pigtail ui hash-password|serve` (M1-T12, D1 preview)

    rep = sub.add_parser("report", help="reports (hn-frontpage: M1-T22; inventory: M11)")
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
    inv = rep_sub.add_parser(
        "inventory", help="data-cache inventory: counts per kept table, no names (read-only)"
    )
    inv.add_argument("--json", action="store_true", help="JSON instead of a Markdown table")
    inv.set_defaults(func=cmd_report_inventory)

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
    from pigtail.privacy.key_fingerprint import KeyFingerprintMismatch

    args = build_parser().parse_args(argv)
    try:
        rc: int = args.func(args)
    except KeyFingerprintMismatch as e:  # CB-25: fail closed with a clear message, exit 2
        print(str(e), file=sys.stderr)
        return 2
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
