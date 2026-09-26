"""`pigtail capture github …` commands (M1-T24; ADR-032; per-repo only since M11, ADR-047.6).
Each prints one JSON object with its `run_id`; each run writes a `runs` record
(`capture.github.<command>`).

| command            | GitHub bucket | schedule (infra/schedule.toml)          |
|--------------------|---------------|-----------------------------------------|
| `star-history`     | core          | daily (`--cases`), or `--repo` manually |
| `repo-events`      | core          | per run, off by default                 |
| `budget`           | none          | manual                                  |

The watch list, the all-GitHub search sweeps, the HN/GH Archive screens, detection v1 and the
settle-lag collection were removed in M11 (ADR-047.6, ADR-049.5); they are in the git tag
`archive/global-collection`.

Every command that calls GitHub exits 2 without `GITHUB_TOKEN`. `repo-events` also exits 2 unless
`PIGTAIL_ENABLE_GITHUB_EVENTS=1` and `PIGTAIL_ADR022_PERSON_SOURCES_OK=1` (ADR-022, ADR-036).
Budget stops end a run early with `budget_stop` set; the run still succeeds (resumable work).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

JOB_CAP_DEFAULTS = {
    # per run; replan §6.2 steady state per hour, with room for catch-up
    "star-history": {"core": 400},
    "repo-events": {"core": 1600},
}


class _Exit(Exception):
    def __init__(self, code: int, msg: str) -> None:
        super().__init__(msg)
        self.code = code


def _settings() -> Any:
    from pigtail.config import Settings

    s = Settings.from_env()
    if not s.database_url:
        raise _Exit(2, "DATABASE_URL is not set")
    return s


def _need_token() -> None:
    from pigtail.connectors.github import TOKEN_ENV

    if not (os.environ.get(TOKEN_ENV) or "").strip():
        raise _Exit(
            2,
            f"{TOKEN_ENV} is not set: no live GitHub call without the operator's own token "
            "(docs/guides/operator.md, 'GitHub token and budgets')",
        )


def _budget(db: Any, command: str, override: dict[str, int | None]) -> Any:
    from pigtail.connectors.github_budget import Budget, BudgetConfig, JobCaps, PostgresLedger

    caps: dict[str, int | None] = dict(JOB_CAP_DEFAULTS.get(command, {}))
    caps.update({k: v for k, v in override.items() if v is not None})
    return Budget(BudgetConfig.from_env(os.environ), PostgresLedger(db.conn), job=JobCaps(caps))


def _github(s: Any, db: Any, run: Any, budget: Any) -> Any:
    from pigtail.capture.snapshots import build_store
    from pigtail.connectors.github import GitHubConnector, PostgresCache
    from pigtail.privacy import suppression

    return GitHubConnector(
        store=build_store(s),
        pseudonymizer=None,
        run=run,
        evidence_sink=db.upsert_evidence,
        suppression=suppression.load(db),
        budget=budget,
        cache=PostgresCache(db.conn),
    )


def _run(
    command: str,
    args: argparse.Namespace,
    body: Callable[[Any, Any, Any], dict[str, Any]],
    *,
    token: bool = True,
) -> int:
    from pigtail.capture.db import CaptureDB
    from pigtail.capture.runs import RunRecorder
    from pigtail.connectors.base import ConnectorError
    from pigtail.db.migrate import migrate
    from pigtail.logsafe import configure_logging

    configure_logging()
    try:
        s = _settings()
        if token:
            _need_token()
    except _Exit as e:
        print(str(e), file=sys.stderr)
        return e.code
    migrate(s.database_url)
    db = CaptureDB.connect(s.database_url)
    config = {k: _jsonable(v) for k, v in vars(args).items() if k != "func"}
    try:
        with RunRecorder(f"capture.github.{command.replace('-', '_')}", config,
                         sink=db.upsert_run) as run:  # fmt: skip
            try:
                out = body(s, db, run)
            except _Exit as e:
                print(str(e), file=sys.stderr)
                return e.code
            except ConnectorError as e:
                print(str(e), file=sys.stderr)
                return 2
    finally:
        db.close()
    print(json.dumps({"run_id": run.id, **out}, indent=2, default=str))
    return 0


def _jsonable(v: Any) -> Any:
    if isinstance(v, datetime | timedelta):
        return str(v)
    return v


# --- commands ------------------------------------------------------------------------------------
def _host_id(db: Any, full_name: str) -> int:
    row = db.conn.execute(
        "SELECT host_id FROM repos WHERE host = 'github' AND lower(full_name) = lower(%s)"
        " ORDER BY first_seen_at LIMIT 1",
        (full_name,),
    ).fetchone()
    if row is None:
        raise _Exit(
            2,
            "unknown repo id: the repo must be in the `repos` table first (a case or, from M13,"
            " a brief's discovery adds it)",
        )
    return int(row[0])


def cmd_star_history(args: argparse.Namespace) -> int:
    def body(s: Any, db: Any, run: Any) -> dict[str, Any]:
        from pigtail.capture.star_history import due_case_repos, fetch_star_history
        from pigtail.connectors.base import FetchError
        from pigtail.connectors.github_budget import BudgetExhausted

        conn = _github(s, db, run, _budget(db, "star-history", {"core": args.max_requests}))
        if args.repo:
            rid = _host_id(db, args.repo)
            pages = 100 if args.full else 1
            per_page = 30 if args.full else 6
            r = fetch_star_history(conn, db, rid, args.repo, per_page=per_page,
                                   max_pages=pages, run=run)  # fmt: skip
            out = r.to_dict()
            out.pop("evidence_ids")
            return {"repos": 1, **out}
        rows = due_case_repos(db, refresh=timedelta(hours=args.refresh_hours))
        done = failed = 0
        stop = None
        for host_id, name in rows:
            try:
                fetch_star_history(conn, db, int(host_id), name, run=run)
                done += 1
            except BudgetExhausted as e:
                stop = e.reason
                break
            except FetchError:  # one bad repo (404, 5xx) must not stop the queue
                failed += 1
                run.incr("star_history.failed")
        return {"due": len(rows), "fetched": done, "failed": failed, "budget_stop": stop}

    return _run("star-history", args, body)


def cmd_repo_events(args: argparse.Namespace) -> int:
    def body(s: Any, db: Any, run: Any) -> dict[str, Any]:
        from pigtail.capture.repo_events import RepoEventsPoller
        from pigtail.capture.snapshots import build_store
        from pigtail.connectors.github import GitHubRepoEventsConnector, PostgresCache
        from pigtail.privacy import suppression
        from pigtail.pseudonymize import Pseudonymizer

        if not s.pseudonym_key:
            raise _Exit(2, "PSEUDONYM_KEY is not set (>= 16 chars; PRD §10)")
        conn = GitHubRepoEventsConnector(  # raises PersonSourceHold without the ADR-022 flag
            store=build_store(s),
            pseudonymizer=Pseudonymizer(s.pseudonym_key),
            run=run,
            evidence_sink=db.upsert_evidence,
            suppression=suppression.load(db),
            budget=_budget(db, "repo-events", {"core": args.max_requests}),
            cache=PostgresCache(db.conn),
        )
        if not conn.enabled:
            raise _Exit(
                2,
                "per-repo events are off (PIGTAIL_ENABLE_GITHUB_EVENTS=0, the default). They are "
                "person-level data (TM-33, ADR-022, ADR-036): see docs/guides/operator.md.",
            )
        return RepoEventsPoller(conn, db, run=run).poll_due().to_dict()

    return _run("repo-events", args, body)


def cmd_budget(args: argparse.Namespace) -> int:
    def body(s: Any, db: Any, run: Any) -> dict[str, Any]:
        from pigtail.connectors.github_budget import BudgetConfig

        cfg = BudgetConfig.from_env(os.environ)
        rows = db.conn.execute(
            "SELECT hour, resource, units, requests, not_modified, rate_limited, last_limit,"
            " last_remaining, last_reset FROM github_budget_ledger WHERE hour >= %s"
            " ORDER BY hour, resource",
            (datetime.now(UTC) - timedelta(hours=args.hours),),
        ).fetchall()
        keys = ("hour", "resource", "units", "requests", "not_modified", "rate_limited",
                "last_limit", "last_remaining", "last_reset")  # fmt: skip
        return {
            "caps_per_hour": dict(cfg.per_hour),
            "reserve_fraction": cfg.reserve_fraction,
            "ledger": [dict(zip(keys, r, strict=True)) for r in rows],
        }

    return _run("budget", args, body, token=False)


def add_commands(cap_sub: Any) -> None:
    gh = cap_sub.add_parser("github", help="per-repo GitHub collectors: star history, events")
    sub = gh.add_subparsers(dest="github_command", required=True)

    p = sub.add_parser("star-history", help="star-history daily series (core bucket)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--repo", help="owner/name (a repo already in the `repos` table)")
    g.add_argument("--cases", action="store_true", help="repos of open cases, refreshed daily")
    p.add_argument("--full", action="store_true", help="--repo: page back to the creation week")
    p.add_argument(
        "--refresh-hours", type=int, default=20, help="--cases: re-fetch after N hours (default 20)"
    )
    p.add_argument("--max-requests", type=int, help="core requests for this run (default 400)")
    p.set_defaults(func=cmd_star_history)

    p = sub.add_parser("repo-events", help="per-repo events for open cases (person-level; off)")
    p.add_argument("--max-requests", type=int, help="core requests for this run (default 1600)")
    p.set_defaults(func=cmd_repo_events)

    p = sub.add_parser("budget", help="GitHub budget ledger (replan §8 M7)")
    p.add_argument("--hours", type=int, default=24)
    p.set_defaults(func=cmd_budget)
