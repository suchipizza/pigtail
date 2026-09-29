"""`pigtail plan <brief_id>` and `pigtail plan lock <brief_id>` (M25; DELIVERABLES D3; PRD F10,
F11; ADR-091). Deterministic, no LLM, no network; the plan is written privately beside the
report (ADR-073.1)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EXIT_OK, EXIT_FAILED, EXIT_USAGE = 0, 1, 2


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_plan(args: argparse.Namespace) -> int:
    from pydantic import ValidationError

    from pigtail.briefs.cli import _settings, _store
    from pigtail.briefs.model import BriefInvalid
    from pigtail.briefs.store import BriefNotFound
    from pigtail.forensics.plan import load_inputs

    s = _settings()
    lock = args.brief_id == "lock"
    brief_id = args.target if lock else args.brief_id
    if not brief_id or (args.target and not lock):
        print("usage: pigtail plan <brief_id> | pigtail plan lock <brief_id>", file=sys.stderr)
        return EXIT_USAGE
    try:
        brief = _store().get(brief_id, args.version).brief
    except (BriefNotFound, BriefInvalid) as e:
        print(str(e), file=sys.stderr)
        return EXIT_FAILED
    if lock:
        return _lock(args, s, brief)
    try:
        inputs = load_inputs(Path(args.inputs) if args.inputs else None)
    except (OSError, ValidationError, ValueError) as e:
        print(f"invalid --inputs: {e}", file=sys.stderr)
        return EXIT_USAGE
    report = None if args.report in (None, "latest") else Path(args.report)
    append = Path(args.append) if args.append else None
    if append is not None and not append.is_file():
        print(f"--append: no such file: {append}", file=sys.stderr)
        return EXIT_USAGE
    outcome = _run(brief, s, report, inputs, append)
    if args.json:
        _print(outcome.to_dict())
    else:
        print(f"{outcome.status}: {outcome.message}")
        for k, v in outcome.paths.items():
            print(f"private plan ({k}): {v}")
        if outcome.summary:
            _print(outcome.summary)
    if outcome.exit_code:
        print(outcome.message, file=sys.stderr)
    return int(outcome.exit_code)


def _run(brief: Any, s: Any, report: Path | None, inputs: Any, append: Path | None) -> Any:
    from pigtail.forensics.plan import run_plan

    day = datetime.now(UTC).date()
    if not s.database_url:
        return run_plan(
            brief, s.data_dir, report_path=report, inputs=inputs, append_path=append, day=day
        )
    import psycopg

    with psycopg.connect(s.database_url, autocommit=True) as conn:
        return run_plan(
            brief,
            s.data_dir,
            report_path=report,
            inputs=inputs,
            append_path=append,
            conn=conn,
            day=day,
        )


def _lock(args: argparse.Namespace, s: Any, brief: Any) -> int:
    from pigtail.forensics.plan import latest_plan, lock_predictions

    path = (
        Path(args.plan)
        if args.plan not in (None, "latest")
        else latest_plan(s.data_dir, brief.brief_id, brief.version)
    )
    if path is None or not path.is_file():
        print("no plan of this brief version: run `pigtail plan <brief_id>` first", file=sys.stderr)
        return EXIT_FAILED
    res = lock_predictions(path, now=datetime.now(UTC))
    if res.status == "locked" and s.database_url:
        # the lock's hash and time also go to the run log (a timestamp outside the file)
        import psycopg

        from pigtail.capture.db import CaptureDB
        from pigtail.capture.runs import RunRecorder

        with psycopg.connect(s.database_url, autocommit=True) as conn:
            cfg = {"predictions_sha256": res.predictions_sha256, "locked_at": res.locked_at}
            with RunRecorder("plan.lock", cfg, sink=CaptureDB(conn).upsert_run):
                pass
    if args.json:
        _print(res.to_dict())
    else:
        print(f"{res.status}: {res.message}")
        if res.path:
            print(f"lock file (private): {res.path}\nsha256: {res.predictions_sha256}")
    return EXIT_OK if res.status in ("locked", "already_locked") else EXIT_FAILED


def add_plan_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser(
        "plan",
        help="the D3 plan from a brief's stored report (M25; deterministic); "
        "`plan lock <id>` pre-registers its predictions",
    )
    p.add_argument("brief_id", help="brief id, or `lock` followed by the brief id")
    p.add_argument("target", nargs="?", help=argparse.SUPPRESS)
    p.add_argument("--version", type=int, help="brief version (default: latest)")
    p.add_argument("--report", help="report JSON path, or `latest` (default)")
    p.add_argument("--inputs", help="plan inputs YAML (available_assets, time budget, window)")
    p.add_argument("--append", help="a file shown verbatim as the fast-path verdict section")
    p.add_argument("--plan", help="`plan lock`: the plan JSON (default: the latest)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_plan)
