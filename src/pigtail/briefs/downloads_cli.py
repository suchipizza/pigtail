"""`pigtail brief downloads` (M23b; ADR-090): fill the exploratory download outcomes of a stored
selection without re-running it.

    pigtail brief downloads ID [--version N] [--selection SEL] [--all-roles] [--as-of DATE]
                           [--dry-run] [--json]

Free official APIs only (npm TM-08, pypistats.org TM-35, GitHub TM-02 for the manifests); no
model call. The request estimate is printed first; `--dry-run` stops there (no request, no
write). Cases: view A's winners, matched losers, exemplars and exemplar losers by default;
`--all-roles` takes every view-A row. Values are written per case (resumable; an observed value
is never overwritten) and read back by `pigtail.briefs.downloads.secondary_outcomes`.

Exit codes: 0 ok; 1 no such brief or selection; 2 usage (no DATABASE_URL, no GITHUB_TOKEN, the
database not migrated); 4 the GitHub request budget stopped the run (run again later: it
resumes).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, date, datetime
from typing import Any

EXIT_INVALID = 1
EXIT_USAGE = 2
EXIT_BUDGET = 4


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _render_plan(p: dict[str, Any]) -> str:
    r = p["requests_upper_bound"]
    return "\n".join(
        [
            f"Downloads (exploratory secondary outcome, ADR-088.3) for selection "
            f"{p['selection_id']}, view A, roles: {p['roles']}",
            f"  cases: {p['cases']} ({p['cases_without_anchor']} without an anchor); to fetch: "
            f"npm {p['cases_to_fetch']['npm']}, PyPI {p['cases_to_fetch']['pypi']}",
            f"  requests (upper bound): GitHub core {r['github_core']} (+ up to "
            f"{r['github_core_per_npm_workspace_root']} per npm workspace root), npm {r['npm']}, "
            f"pypistats {r['pypistats']} (at most ~{p['pypistats_minutes_at_most']} min at one "
            "request per 20 s)",
            f"  cost: USD {p['cost_usd']:.2f}; model calls: {p['llm_calls']}",
        ]
    )


def cmd_downloads(args: argparse.Namespace) -> int:
    import psycopg

    from pigtail.briefs import downloads as dl
    from pigtail.briefs.cli import _settings

    s = _settings()
    if not s.database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return EXIT_USAGE
    try:
        as_of = date.fromisoformat(args.as_of) if args.as_of else datetime.now(UTC).date()
    except ValueError:
        print("--as-of must be YYYY-MM-DD", file=sys.stderr)
        return EXIT_USAGE
    roles = None if args.all_roles else list(dl.DEFAULT_ROLES)
    with psycopg.connect(s.database_url, autocommit=True) as conn:
        try:
            sel = dl.resolve_selection(conn, args.brief_id, args.version, args.selection)
            if sel is None:
                print("no stored selection for this brief (version)", file=sys.stderr)
                return EXIT_INVALID
            p = dl.plan(conn, sel["id"], roles)
        except psycopg.Error as e:
            print(
                f"database not ready ({type(e).__name__}); run `pigtail db migrate`",
                file=sys.stderr,
            )
            return EXIT_USAGE
        if p["cases"] == 0:
            print("the selection has no view-A (follow_through) cases in scope", file=sys.stderr)
            return EXIT_INVALID
        if args.dry_run:
            if args.json:
                _print({"estimate": p, "dry_run": {"network_calls": 0, "writes": 0}})
            else:
                print(_render_plan(p))
                print("\nDRY RUN: nothing was fetched or stored.")
            return 0
        if not args.json:
            print(_render_plan(p))
        out = _fill(conn, s, sel, roles, as_of, args)
        if isinstance(out, int):
            return out
        out["estimate"] = p
        if args.json:
            _print(out)
        else:
            print(_render_result(out))
    return 0


def _fill(
    conn: Any, s: Any, sel: dict[str, Any], roles: list[str] | None, as_of: date, a: Any
) -> dict[str, Any] | int:
    from pigtail.briefs import downloads as dl
    from pigtail.briefs.cli import _connectors
    from pigtail.capture.db import CaptureDB
    from pigtail.capture.runs import RunRecorder
    from pigtail.capture.snapshots import build_store
    from pigtail.connectors.downloads import NpmDownloadsConnector, PypiStatsConnector
    from pigtail.connectors.github_budget import BudgetExhausted
    from pigtail.privacy.deletion import DeletionLog, drop_after_parse

    db = CaptureDB(conn)
    snaps = build_store(s)
    cfg = {
        "selection_id": sel["id"],
        "roles": roles or "all",
        "as_of": as_of.isoformat(),
        "rule": dl.RULE_VERSION,
    }
    with RunRecorder("brief.downloads", cfg, sink=db.upsert_run) as rec:
        try:
            github, _hn, _gha = _connectors(s, db, rec, need_github=True)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return EXIT_USAGE
        dlog = DeletionLog(db, "retention", run_id=rec.id)

        def drop(f: Any) -> None:
            drop_after_parse(db, snaps, f.evidence.id, f.content_hash, dlog)

        npm = pypi = None
        if NpmDownloadsConnector.enabled_from_env(os.environ):
            npm = NpmDownloadsConnector(store=snaps, run=rec, evidence_sink=db.upsert_evidence)
        if PypiStatsConnector.enabled_from_env(os.environ):
            pypi = PypiStatsConnector(store=snaps, run=rec, evidence_sink=db.upsert_evidence)
        deps = dl.FillDeps(
            conn=conn,
            files=dl.RepoFiles(github, drop),
            npm=npm,
            pypi=pypi,
            drop_raw=drop,
            reuse=dl.same_day_reuse(conn, snaps, datetime.now(UTC).date()),
            run_id=rec.id,
        )
        try:
            res = dl.fill(deps, sel["id"], roles=roles, as_of=as_of, now=datetime.now(UTC))
        except BudgetExhausted as e:
            print(
                f"GitHub request budget reached ({e}); run again later to resume", file=sys.stderr
            )
            return EXIT_BUDGET
        for k, v in res.to_dict().items():
            if isinstance(v, int):
                rec.incr(f"downloads.{k}", v)
    return {
        "selection_id": sel["id"],
        "as_of": as_of.isoformat(),
        "run_id": rec.id,
        "result": res.to_dict(),
    }


def _render_result(out: dict[str, Any]) -> str:
    r = out["result"]
    lines = [
        f"\nFilled for selection {out['selection_id']} (as of {out['as_of']}; "
        f"{r['rows_written']} rows written; exploratory, not used by the sort):"
    ]
    for m, counts in sorted(r["by_metric_status"].items()):
        lines.append(f"  {m}: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    for eco, counts in sorted(r["mapping"].items()):
        lines.append(
            f"  mapping {eco}: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
        )
    lines.append("Read them with pigtail.briefs.downloads.secondary_outcomes(conn, selection_id).")
    return "\n".join(lines)


def add_commands(bs: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = bs.add_parser(
        "downloads", help="fill npm/PyPI download outcomes of a stored selection (exploratory)"
    )
    p.add_argument("brief_id")
    p.add_argument("--version", type=int, help="brief version (default: the latest selection's)")
    p.add_argument("--selection", help="a stored selection id (default: the latest)")
    p.add_argument("--all-roles", action="store_true", help="every view-A case, not only pairs")
    p.add_argument("--as-of", help="judge `pending` against this UTC date (default: today)")
    p.add_argument("--dry-run", action="store_true", help="show the request estimate only")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_downloads)
