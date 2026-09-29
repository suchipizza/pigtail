"""`pigtail brief pilot | pilot-summary | decay` (M23; WORK_ORDER §4.5; ADR-086).

    pigtail brief pilot ID [--version N] [--cases N=5] [--selection SEL] [--approve-paid]
                        [--dry-run] [--wait-minutes M] [--json]
        the pilot of a brief's first cases: case evidence, double coding, adjudication, alpha,
        cost report and full-brief projection; the estimate is shown first
    pigtail brief code ID [--version N] [--selection SEL] [--approve-paid] [--dry-run]
                       [--wait-minutes M] [--max-usd USD] [--json]
        the full coding of a brief (M24, ADR-089): every winner, matched loser, exemplar and
        exemplar loser of every view (`full-cases-v1`), with the pilot's machinery (evidence,
        double coding, adjudication, alpha, batch, cache, budget stops, resumable checkpoints);
        cases a finished pilot already coded are copied, not paid again; report facts (launch
        events, assets, amplifiers, star trajectory and bursts) per case; the estimate (with
        the x1.25 contingency) is shown first; --max-usd is a hard stop on this run's spend
    pigtail brief pilot-summary ID [--version N] [--label TEXT] [--json]
        counts-only lines for ops/COSTS.md and ops/STATUS.md (no case detail)
    pigtail brief decay [ID] [--version N] [--all] [--due] [--json]
        evidence decay at +1/+7/+30 days: with --due, run the checks that are due; then print
        the aggregation by source and age, with the actual age at check and the late checks
        (and write it to the private report directory)
    pigtail brief pilot-cost ID [--version N] [--run RUN] [--rebuild] [--dry-run]
                             [--superseded-before ISO] [--json]
        the pilot's measured cost model and projection; with --rebuild, re-store the model from
        the pilot's cost-ledger rows under the current rules (case-cost-v3: per call, current
        settings only, diagnostic rows never; ADR-086 addendum 3). No call, no network.
    pigtail brief pilot-annotate ID --note TEXT [--version N] [--run RUN] [--commit SHA]
                                 [--step STEP] [--json]
        append a correction note to a pilot run's provenance (nothing recorded is changed)

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
    rule = getattr(args, "rule", "pilot")
    if rule == "pilot" and (args.cases < 1 or args.cases > 200):
        print("--cases must be between 1 and 200", file=sys.stderr)
        return EXIT_USAGE
    max_usd = getattr(args, "max_usd", None)
    if max_usd is not None and max_usd <= 0:
        print("--max-usd must be above 0", file=sys.stderr)
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
        rule=rule,
        max_usd=max_usd,
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
            if rule == "pilot":
                print(
                    "\nCases (pilot-cases-v1): "
                    + ", ".join(
                        f"{c.position}. view {c.view} {c.role} (pair {c.pair_id})" for c in cases
                    )
                )
            else:
                by: dict[str, int] = {}
                for c in cases:
                    k = f"view {c.view} {c.role}"
                    by[k] = by.get(k, 0) + 1
                print(
                    f"\nCases (full-cases-v1): {len(cases)}: "
                    + ", ".join(f"{k} {n}" for k, n in sorted(by.items()))
                )
            print("\nDRY RUN: nothing was started, fetched or stored.")
        return 0
    from pigtail.db.migrate import migrate

    migrate(s.database_url)
    conn = psycopg.connect(s.database_url, autocommit=True)
    try:
        db = CaptureDB(conn)
        snaps = build_store(s)
        config = {"brief_version": brief.version, "cases": args.cases, "rule": rule}
        name = "brief.code" if rule == "full" else "brief.pilot"
        with RunRecorder(name, config, sink=db.upsert_run) as rec:
            github, _hn, _gha = _connectors(s, db, rec, need_github=False)
            hn = _story_meta(snaps, db, rec) if rule == "full" else None
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
                hn=hn,
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


def _story_meta(snaps: Any, db: Any, recorder: Any) -> Any:
    """The HN story-metadata connector for the report facts (off with the Show HN connector)."""
    from pigtail.connectors.hn import HNShowDiscoveryConnector, HNStoryMetaConnector

    if not HNShowDiscoveryConnector.enabled_from_env(os.environ):
        return None
    return HNStoryMetaConnector(
        store=snaps, pseudonymizer=None, run=recorder, evidence_sink=db.upsert_evidence
    )


def cmd_code(args: argparse.Namespace) -> int:
    """`pigtail brief code`: the pilot command with the full case rule (M24, ADR-089)."""
    args.rule = "full"
    args.cases = 0
    return cmd_pilot(args)


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
        "| kind | age | scheduled | checked | retrievable | changed | gone | errors | lost |"
        " actual age at check (h: min / median / max) | late |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in agg["by_kind_and_age"]:
        a = r.get("age_hours_at_check") or {}
        lines.append(
            f"| {r['kind']} | +{r['offset_days']}d | {r['scheduled']} | {r['checked']} | "
            f"{r['retrievable']} | {r['changed']} | {r['gone']} | {r['errors']} | "
            f"{r['lost_share']} | {a.get('min')} / {a.get('median')} / {a.get('max')} | "
            f"{r.get('late_checks', 0)} |"
        )
    r198 = agg["r19_8"]
    lines.append("")
    for age, v in (agg.get("by_age") or {}).items():
        a = v.get("age_hours_at_check") or {}
        lines.append(
            f"- {age}: nominal {v.get('nominal_hours')} h, on time within "
            f"{v.get('on_time_within_hours')} h; actual {a.get('min')} / {a.get('median')} / "
            f"{a.get('max')} h; late checks {v.get('late_checks', 0)}"
            + (" (LATE: reported at the actual age)" if v.get("late_checks") else "")
        )
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


def _pilot_run(conn: Any, args: argparse.Namespace) -> dict[str, Any] | None:
    from pigtail.forensics.store import latest_pilot, pilot_row

    if args.run:
        row = pilot_row(conn, args.run)
        return row if row is not None and row["brief_id"] == args.brief_id else None
    return latest_pilot(conn, args.brief_id, args.version)


def cmd_pilot_cost(args: argparse.Namespace) -> int:
    import psycopg

    from pigtail.briefs.budget import month_spend
    from pigtail.briefs.cli import _settings
    from pigtail.capture.runs import git_commit
    from pigtail.forensics.cost import ADR087_AT
    from pigtail.forensics.pilot import rebuild_cost_model
    from pigtail.forensics.report import write_report
    from pigtail.forensics.store import latest_cost_model
    from pigtail.llm.batch import PgCostLedger
    from pigtail.llm.store import LLMStore

    s = _settings()
    if not s.database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return EXIT_USAGE
    before = ADR087_AT
    if args.superseded_before:
        try:
            before = datetime.fromisoformat(args.superseded_before)
        except ValueError:
            print("--superseded-before must be an ISO 8601 time", file=sys.stderr)
            return EXIT_USAGE
        if before.tzinfo is None:
            before = before.replace(tzinfo=UTC)
    now = datetime.now(UTC)
    with psycopg.connect(s.database_url, autocommit=True) as conn:
        pilot = _pilot_run(conn, args)
        if pilot is None:
            print("no pilot run for this brief", file=sys.stderr)
            return EXIT_INVALID
        if not args.rebuild:
            row = latest_cost_model(conn, args.brief_id)
            if args.json:
                _print({"brief_run_id": pilot["brief_run_id"], "cost_model": row})
            elif row is None:
                print("no current (case-cost-v3) cost model stored; run with --rebuild")
            else:
                pr = row["projection"]
                print(
                    f"cost model of run {row['brief_run_id']} ({row['model_version']}, "
                    f"{row['created_at']}): per case USD "
                    f"{(pr.get('per_case_usd') or {}).get('total')}; projection USD "
                    f"{pr.get('projected_total_usd')} (with contingency USD "
                    f"{pr.get('projected_total_with_contingency_usd')}) of cap USD "
                    f"{pr.get('cap_usd')}"
                )
            return 0
        try:
            brief = _brief(
                argparse.Namespace(brief_id=args.brief_id, version=pilot["brief_version"])
            )
        except (BriefNotFound, BriefInvalid) as e:
            print(str(e), file=sys.stderr)
            return EXIT_INVALID
        local = LLMStore(s.data_dir / "llm.sqlite3")
        try:
            month = float(
                month_spend(local, lambda since: PgCostLedger(conn).month_total(since), now)["usd"]
            )
        finally:
            local.close()
        backend = brief.budget.llm_backend
        out = rebuild_cost_model(
            conn,
            brief,
            str(pilot["brief_run_id"]),
            synthesis_model=s.llm_models["synthesis"],
            batch=bool(s.llm_batch) and backend == "api",
            backend=backend,
            month_spent_usd=month,
            month_cap_usd=s.budget_usd_month,
            commit=git_commit(),
            at=now,
            superseded_before=before,
            dry_run=args.dry_run,
        )
    md = _cost_md(out)
    if not args.dry_run and out["stored"]:
        paths = write_report(
            s.data_dir, args.brief_id, int(pilot["brief_version"]), "pilot-cost", out, md,
            day=now.date(),
        )  # fmt: skip
        out["report_paths"] = paths
    if args.json:
        _print(out)
    else:
        print(md)
        for k, v in (out.get("report_paths") or {}).items():
            print(f"private report ({k}): {v}")
    return 0 if out["cost_model"] is not None else EXIT_INVALID


def _cost_md(out: dict[str, Any]) -> str:
    pr = out["projection"]
    rows = (out.get("cost_model") or {}).get("ledger_rows") or pr.get("ledger_rows") or {}
    used = rows.get("used") or {}
    lines = [
        f"Pilot cost model rebuilt from the ledger (run {out['brief_run_id']}; "
        + ("dry run, nothing stored" if out["dry_run"] else "stored" if out["stored"] else "")
        + ")",
        f"- ledger rows used: {used.get('rows')} rows, {used.get('requests')} requests, "
        f"USD {used.get('usd')}",
        *[
            f"- left out ({why}): {v['rows']} rows, {v['requests']} requests, USD {v['usd']}"
            for why, v in (rows.get("excluded") or {}).items()
        ],
    ]
    if pr.get("skipped"):
        lines.append(f"- no projection: {pr['skipped']}")
    else:
        per = pr.get("per_case_usd") or {}
        lines += [
            f"- per case USD {per.get('total')} = coder A {per.get('coder_a')} + coder B "
            f"{per.get('coder_b')} + adjudication {per.get('adjudication')}",
            f"- projection ({pr.get('full_brief_cases')} cases, {pr.get('cases_remaining')} "
            f"remaining): USD {pr.get('projected_total_usd')}; with x{pr.get('contingency_factor')}"
            f" contingency USD {pr.get('projected_total_with_contingency_usd')}; cap USD "
            f"{pr.get('cap_usd')}" + (" — H6" if pr.get("h6") else " (within)"),
        ]
    c = out.get("cost") or {}
    by = c.get("per_case_usd_by_stage") or {}
    lines.append(
        f"- actual spend of the pilot's cases: USD {c.get('total_usd')}, per case USD "
        f"{c.get('per_case_usd')} = " + " + ".join(f"{k} {v}" for k, v in by.items())
    )
    return "\n".join(lines) + "\n"


def cmd_pilot_annotate(args: argparse.Namespace) -> int:
    import psycopg

    from pigtail.briefs.cli import _settings
    from pigtail.capture.runs import git_commit
    from pigtail.forensics.store import add_annotation, pilot_row

    s = _settings()
    if not s.database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return EXIT_USAGE
    with psycopg.connect(s.database_url, autocommit=True) as conn:
        pilot = _pilot_run(conn, args)
        if pilot is None:
            print("no pilot run for this brief", file=sys.stderr)
            return EXIT_INVALID
        rid = str(pilot["brief_run_id"])
        try:
            entry = add_annotation(
                conn,
                rid,
                note=args.note,
                at=datetime.now(UTC),
                commit=args.commit,
                step=args.step,
                annotated_by_commit=git_commit(),
            )
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return EXIT_USAGE
        row = pilot_row(conn, rid) or {}
    out = {
        "brief_run_id": rid,
        "annotation": entry,
        "annotations": row.get("annotations") or [],
        "recorded_code_commit": row.get("code_commit"),
        "invocations": row.get("invocations") or [],
    }
    if args.json:
        _print(out)
    else:
        print(f"annotation {entry['n']} added to pilot run {rid} (nothing recorded was changed)")
    return 0


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

    p = bs.add_parser(
        "code", help="full coding of every selected case (M24): evidence, facts, coding, alpha"
    )
    p.add_argument("brief_id")
    p.add_argument("--version", type=int, help="brief version (default: latest)")
    p.add_argument("--selection", help="a stored selection id (default: the latest)")
    p.add_argument("--approve-paid", action="store_true", help="approve the paid steps shown")
    p.add_argument("--dry-run", action="store_true", help="show the estimate and cases only")
    p.add_argument("--wait-minutes", type=float, help="poll batches this long, then leave them")
    p.add_argument(
        "--max-usd", type=float, help="hard stop on this run's API spend (e.g. 25, ADR-088)"
    )
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_code)

    p = bs.add_parser("pilot-summary", help="counts-only lines for ops/COSTS.md and STATUS.md")
    p.add_argument("brief_id")
    p.add_argument("--version", type=int)
    p.add_argument("--label", default="brief", help="how the ops files name the brief")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_pilot_summary)

    p = bs.add_parser(
        "pilot-cost", help="the pilot's cost model; --rebuild re-stores it from the ledger"
    )
    p.add_argument("brief_id")
    p.add_argument("--version", type=int, help="brief version (default: the latest pilot's)")
    p.add_argument("--run", help="the pilot run id (default: the latest pilot of the brief)")
    p.add_argument(
        "--rebuild", action="store_true", help="re-store the model under the current rules"
    )
    p.add_argument("--dry-run", action="store_true", help="with --rebuild: show, store nothing")
    p.add_argument(
        "--superseded-before",
        help="back-compat cut-off for ledger rows without a thinking label (ISO 8601; default: "
        "the ADR-087 commit time)",
    )
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_pilot_cost)

    p = bs.add_parser("pilot-annotate", help="add a correction note to a pilot's provenance")
    p.add_argument("brief_id")
    p.add_argument("--note", required=True, help="the note (no names or brief content)")
    p.add_argument("--version", type=int)
    p.add_argument("--run", help="the pilot run id (default: the latest pilot of the brief)")
    p.add_argument("--commit", help="the code commit the note says a step was actually run at")
    p.add_argument("--step", help="the step the note is about (e.g. double_coding)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_pilot_annotate)

    p = bs.add_parser("decay", help="evidence decay at +1/+7/+30 days (R19.8)")
    p.add_argument("brief_id", nargs="?")
    p.add_argument("--version", type=int)
    p.add_argument("--all", action="store_true", help="every brief with decay checks")
    p.add_argument("--due", action="store_true", help="run the checks that are due first")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_decay)
