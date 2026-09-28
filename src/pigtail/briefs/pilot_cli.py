"""`pigtail brief pilot | pilot-summary | decay` (M23; WORK_ORDER §4.5; ADR-086).

    pigtail brief pilot ID [--version N] [--cases N=5] [--selection SEL] [--approve-paid]
                        [--dry-run] [--wait-minutes M] [--json]
        the pilot of a brief's first cases: case evidence, double coding, adjudication, alpha,
        cost report and full-brief projection; the estimate is shown first
    pigtail brief pilot-summary ID [--version N] [--label TEXT] [--json]
        counts-only lines for ops/COSTS.md and ops/STATUS.md (no case detail)
    pigtail brief decay [ID] [--version N] [--all] [--due] [--json]
        evidence decay at +1/+7/+30 days: with --due, run the checks that are due; then print
        the aggregation by source and age (and write it to the private report directory)

Exit codes as `pigtail run`: 0 ok; 1 failed or nothing to pilot; 2 usage; 3 paid steps not
approved; 4 a cap (H6), the GitHub budget, or a projection above the brief's cap (H6); 5 a
Message Batch still running (run again to collect it); 6 another pilot of the brief is running;
7 the coding failed for every case (the API errors are in the run and the private report; no
cost model or projection is written; run again to redo the failed coding, ADR-086 addendum 1).
Reports go to PIGTAIL_DATA_DIR/reports/<brief>/v<version>/ (private, never in git; ADR-073.1).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from typing import Any

from pigtail.briefs.model import BriefInvalid
from pigtail.briefs.store import BriefNotFound

EXIT_INVALID = 1
EXIT_USAGE = 2


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _brief(args: argparse.Namespace) -> Any:
    from pigtail.briefs.cli import _store

    return _store().get(args.brief_id, args.version).brief


def _pages(snaps: Any, db: Any, recorder: Any) -> Any:
    from pigtail.connectors.project_page import ProjectPageConnector

    if not ProjectPageConnector.enabled_from_env(os.environ):
        return None
    return ProjectPageConnector(
        store=snaps, pseudonymizer=None, run=recorder, evidence_sink=db.upsert_evidence
    )


def cmd_pilot(args: argparse.Namespace) -> int:
    import psycopg

    from pigtail.briefs.cli import _connectors, _llm_client, _settings
    from pigtail.capture.db import CaptureDB
    from pigtail.capture.runs import RunRecorder
    from pigtail.capture.snapshots import build_store
    from pigtail.forensics.pilot import PilotDeps, PilotOptions, plan, render_estimate, run_pilot

    s = _settings()
    try:
        brief = _brief(args)
    except (BriefNotFound, BriefInvalid) as e:
        print(str(e), file=sys.stderr)
        return EXIT_INVALID
    if not s.database_url:
        print(
            "DATABASE_URL is not set: the pilot keeps its checkpoints in Postgres", file=sys.stderr
        )
        return EXIT_USAGE
    if args.cases < 1 or args.cases > 200:
        print("--cases must be between 1 and 200", file=sys.stderr)
        return EXIT_USAGE
    try:
        client = _llm_client()
    except ValueError as e:
        print(f"LLM client not configured: {e}", file=sys.stderr)
        return EXIT_USAGE
    opts = PilotOptions(
        cases=args.cases,
        approve_paid=args.approve_paid,
        selection_id=args.selection,
        wait_seconds=args.wait_minutes * 60 if args.wait_minutes is not None else None,
    )
    out: dict[str, Any] = {}
    if args.dry_run:
        with psycopg.connect(s.database_url, autocommit=True) as conn:
            deps = PilotDeps(
                conn=conn,
                client=client,
                snapshots=None,
                data_dir=s.data_dir,
                month_cap_usd=s.budget_usd_month,
                synthesis_model=s.llm_models["synthesis"],
            )
            try:
                _sel, cases, est, err = plan(conn, brief, opts, deps)
            except psycopg.Error as e:
                print(
                    f"database not ready ({type(e).__name__}); run `pigtail db migrate`",
                    file=sys.stderr,
                )
                return EXIT_USAGE
        if err is not None or est is None:
            print(err or "nothing to pilot", file=sys.stderr)
            return EXIT_INVALID
        out = {
            "estimate": est,
            "cases": [
                {"position": c.position, "view": c.view, "role": c.role, "pair_id": c.pair_id}
                for c in cases
            ],
            "dry_run": {"network_calls": 0, "writes": 0},
        }
        if args.json:
            _print(out)
        else:
            print(render_estimate(est))
            print(
                "\nCases (pilot-cases-v1): "
                + ", ".join(
                    f"{c.position}. view {c.view} {c.role} (pair {c.pair_id})" for c in cases
                )
            )
            print("\nDRY RUN: nothing was started, fetched or stored.")
        return 0
    from pigtail.db.migrate import migrate

    migrate(s.database_url)
    conn = psycopg.connect(s.database_url, autocommit=True)
    try:
        db = CaptureDB(conn)
        snaps = build_store(s)
        config = {"brief_version": brief.version, "cases": args.cases}
        with RunRecorder("brief.pilot", config, sink=db.upsert_run) as rec:
            github, _hn, _gha = _connectors(s, db, rec, need_github=False)
            deps = PilotDeps(
                conn=conn,
                client=client,
                snapshots=snaps,
                data_dir=s.data_dir,
                github=github,
                pages=_pages(snaps, db, rec),
                month_cap_usd=s.budget_usd_month,
                run_record_id=rec.id,
                synthesis_model=s.llm_models["synthesis"],
            )
            _sel, _cases, est, err = plan(conn, brief, opts, deps)
            if est is not None and not args.json:
                print(render_estimate(est))
            outcome = run_pilot(brief, deps, opts)
    finally:
        conn.close()
    if args.json:
        _print(outcome.to_dict())
    else:
        print(f"\n{outcome.status}: {outcome.message}")
        for k, v in outcome.report_paths.items():
            print(f"private report ({k}): {v}")
    if outcome.exit_code != 0:
        print(outcome.message, file=sys.stderr)
    return outcome.exit_code


def cmd_pilot_summary(args: argparse.Namespace) -> int:
    import psycopg

    from pigtail.briefs.cli import _settings
    from pigtail.forensics.report import ops_lines
    from pigtail.forensics.store import latest_pilot

    s = _settings()
    if not s.database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return EXIT_USAGE
    with psycopg.connect(s.database_url, autocommit=True) as conn:
        row = latest_pilot(conn, args.brief_id, args.version)
    if row is None or not (row.get("summary") or {}).get("report_written"):
        print("no finished pilot for this brief", file=sys.stderr)
        return EXIT_INVALID
    month = (row.get("finished_at") or datetime.now(UTC)).strftime("%Y-%m")
    lines = ops_lines(row["summary"], label=args.label, month=month)
    if args.json:
        _print(lines)
    else:
        for k, v in lines.items():
            print(f"{k}:\n{v}\n")
    return 0


def cmd_decay(args: argparse.Namespace) -> int:
    import psycopg

    from pigtail.briefs.cli import _connectors, _settings
    from pigtail.capture.db import CaptureDB
    from pigtail.capture.runs import RunRecorder
    from pigtail.capture.snapshots import build_store
    from pigtail.forensics.decay import aggregate, run_due
    from pigtail.forensics.report import write_report

    s = _settings()
    if not s.database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return EXIT_USAGE
    if not args.all and not args.brief_id:
        print("give a brief id, or --all", file=sys.stderr)
        return EXIT_USAGE
    brief_id = None if args.all else args.brief_id
    now = datetime.now(UTC)
    out: dict[str, Any] = {}
    with psycopg.connect(s.database_url, autocommit=True) as conn:
        if args.due:
            db = CaptureDB(conn)
            snaps = build_store(s)
            with RunRecorder("brief.decay", {"all": args.all}, sink=db.upsert_run) as rec:
                github, _hn, _gha = _connectors(s, db, rec, need_github=False)
                res = run_due(
                    conn, now=now, github=github, pages=_pages(snaps, db, rec), brief_id=brief_id
                )
                for k, v in res.to_dict().items():
                    if isinstance(v, int):
                        rec.incr(f"decay.{k}", v)
            out["run"] = res.to_dict()
        ids = (
            [brief_id]
            if brief_id
            else [
                str(r[0])
                for r in conn.execute(
                    "SELECT DISTINCT brief_id FROM brief_evidence_decay ORDER BY 1"
                ).fetchall()
            ]
        )
        reports: dict[str, Any] = {}
        for bid in ids:
            agg = aggregate(conn, bid)
            reports[bid] = agg
            row = conn.execute(
                "SELECT max(brief_version) FROM brief_evidence_decay WHERE brief_id = %s", (bid,)
            ).fetchone()
            if row and row[0] is not None and agg["by_kind_and_age"]:
                md = _decay_md(agg)
                write_report(s.data_dir, bid, int(row[0]), "decay", agg, md, day=now.date())
    out["aggregate"] = reports if args.all else reports.get(str(brief_id), {})
    if args.json:
        _print(out)
    else:
        if "run" in out:
            print(f"decay checks: {out['run']}")
        for bid, agg in reports.items():
            label = "all briefs" if args.all else "this brief"
            print(f"\nEvidence decay ({label}; brief {bid if not args.all else '…'}):")
            print(_decay_md(agg))
    return 0


def _decay_md(agg: dict[str, Any]) -> str:
    lines = [
        "| kind | age | scheduled | checked | retrievable | changed | gone | errors | lost |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in agg["by_kind_and_age"]:
        lines.append(
            f"| {r['kind']} | +{r['offset_days']}d | {r['scheduled']} | {r['checked']} | "
            f"{r['retrievable']} | {r['changed']} | {r['gone']} | {r['errors']} | "
            f"{r['lost_share']} |"
        )
    r198 = agg["r19_8"]
    lines.append("")
    lines.append(
        f"R19.8: lost at 7 days {r198['lost_at_7d']} (complete: {r198['complete_at_7d']}); "
        + (
            "above 10 %: shorten the cadence through an ADR"
            if r198["cadence_adr_needed"]
            else "no cadence change indicated"
        )
        + f". {agg['note']}."
    )
    return "\n".join(lines) + "\n"


def add_commands(bs: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = bs.add_parser("pilot", help="pilot the first cases: evidence, double coding, alpha, cost")
    p.add_argument("brief_id")
    p.add_argument("--version", type=int, help="brief version (default: latest)")
    p.add_argument("--cases", type=int, default=5, help="number of cases (default 5)")
    p.add_argument("--selection", help="a stored selection id (default: the latest)")
    p.add_argument("--approve-paid", action="store_true", help="approve the paid steps shown")
    p.add_argument("--dry-run", action="store_true", help="show the estimate and cases only")
    p.add_argument("--wait-minutes", type=float, help="poll batches this long, then leave them")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_pilot)

    p = bs.add_parser("pilot-summary", help="counts-only lines for ops/COSTS.md and STATUS.md")
    p.add_argument("brief_id")
    p.add_argument("--version", type=int)
    p.add_argument("--label", default="brief", help="how the ops files name the brief")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_pilot_summary)

    p = bs.add_parser("decay", help="evidence decay at +1/+7/+30 days (R19.8)")
    p.add_argument("brief_id", nargs="?")
    p.add_argument("--version", type=int)
    p.add_argument("--all", action="store_true", help="every brief with decay checks")
    p.add_argument("--due", action="store_true", help="run the checks that are due first")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_decay)
