"""pigtail CLI (PRD F14 / R14.1). Stages not yet built exit with code 2 and name their milestone."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime

from pydantic import BaseModel

from pigtail import __version__

PENDING_STAGES = {
    "score": "M5",
    "panel": "M5",
    "extract": "M5",
    "analyze": "M5",
    "plan": "M8",
    "report": "M5",
}


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
    store = LLMStore(s.data_dir / "llm.sqlite3")
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
    from pigtail.pseudonymize import Pseudonymizer

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    s = Settings.from_env()
    if not s.database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    if not s.pseudonym_key:
        print("PSEUDONYM_KEY is not set (>= 16 chars; PRD §10)", file=sys.stderr)
        return 2
    migrate(s.database_url)
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
    purge = cap_sub.add_parser("purge-raw", help="drop raw GH Archive dumps past retention")
    purge.add_argument("--retention-days", type=int, help="default GHARCHIVE_RAW_RETENTION_DAYS")
    purge.set_defaults(func=cmd_capture_purge_raw)

    for stage, milestone in PENDING_STAGES.items():
        sp = sub.add_parser(stage, help=f"(not yet implemented; {milestone})")
        sp.set_defaults(func=lambda _a, s=stage, m=milestone: _pending(s, m))
    return p


def _pending(stage: str, milestone: str) -> int:
    print(f"pigtail {stage}: not implemented yet (scheduled for {milestone})", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    rc: int = args.func(args)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
