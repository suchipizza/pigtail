"""`pigtail capture github …` commands (M1-T24; ADR-032). Each prints one JSON object with its
`run_id`; each run writes a `runs` record (`capture.github.<command>`).

| command            | GitHub bucket | schedule (infra/schedule.toml) |
|--------------------|---------------|--------------------------------|
| `watch-add`        | none          | manual                         |
| `watchlist-counts` | graphql       | hourly                         |
| `search-sweep`     | search        | every 6 h                      |
| `hn-screen`        | none (HN)     | hourly                         |
| `star-history`     | core          | hourly (`--candidates`)        |
| `detect-v1`        | core (refresh)| hourly                         |
| `repo-events`      | core          | every 15 min, off by default   |
| `settle-lag`       | core          | hourly (K2 re-fetches, M4-T4)  |
| `budget`           | none          | manual                         |

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
    "watchlist-counts": {"graphql": 700},
    "search-sweep": {"search": 400},
    "star-history": {"core": 400},
    "detect-v1": {"core": 200},
    "repo-events": {"core": 1600},
    "settle-lag": {"core": 200},
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
def cmd_watch_add(args: argparse.Namespace) -> int:
    def body(s: Any, db: Any, run: Any) -> dict[str, Any]:
        from pigtail.capture.github_watch import Watchlist
        from pigtail.privacy import suppression

        try:
            got = Watchlist(db, suppression.load(db)).nominate(args.source, args.repo)
        except ValueError as e:
            raise _Exit(2, str(e)) from e
        return {"repo_added": got}

    return _run("watch-add", args, body, token=False)


def cmd_watchlist_counts(args: argparse.Namespace) -> int:
    def body(s: Any, db: Any, run: Any) -> dict[str, Any]:
        from pigtail.capture.github_watch import CountSnapshotter, WatchPolicy

        conn = _github(s, db, run, _budget(db, "watchlist-counts", {"graphql": args.max_points}))
        policy = WatchPolicy(cap=args.cap)
        res = CountSnapshotter(conn, db, policy=policy, run=run).snapshot_counts(limit=args.limit)
        from pigtail.capture.github_watch import Watchlist

        return {**res.to_dict(), "watchlist": Watchlist(db).counts()}

    return _run("watchlist-counts", args, body)


def _band(v: str) -> tuple[int, int]:
    lo, sep, hi = v.partition("..")
    if not sep or not lo.isdigit() or not hi.isdigit() or int(lo) > int(hi):
        raise argparse.ArgumentTypeError(f"bad star band {v!r}; use LO..HI")
    return int(lo), int(hi)


def cmd_search_sweep(args: argparse.Namespace) -> int:
    def body(s: Any, db: Any, run: Any) -> dict[str, Any]:
        from pigtail.capture.github_screens import SearchSweeper, SweepConfig

        conn = _github(s, db, run, _budget(db, "search-sweep", {"search": args.max_requests}))
        cfg = SweepConfig(
            new_days=args.new_days,
            new_min_stars=args.new_min_stars,
            active_days=args.active_days,
            active_bands=tuple(args.active_band or [(50, 5000)]),
        )
        res = SearchSweeper(conn, db, cfg=cfg, run=run).sweep(args.kind)
        # queries hold only qualifiers (dates, star ranges): no repo or owner names
        return {**res.to_dict(), "slices_detail": res.queries[:50]}

    return _run("search-sweep", args, body)


def cmd_hn_screen(args: argparse.Namespace) -> int:
    def body(s: Any, db: Any, run: Any) -> dict[str, Any]:
        from pigtail.capture.github_screens import gharchive_screen, hn_screen
        from pigtail.capture.github_watch import Watchlist
        from pigtail.capture.snapshots import build_store
        from pigtail.connectors.hn_ranks import HNRanksConnector
        from pigtail.privacy import suppression

        sup = suppression.load(db)
        watch = Watchlist(db, sup)
        hn = None
        if not args.no_show:
            hn = HNRanksConnector(
                store=build_store(s),
                pseudonymizer=None,
                run=run,
                evidence_sink=db.upsert_evidence,
                suppression=sup,
            )
        res = hn_screen(
            db, watch, window=timedelta(hours=args.window_hours), hn=hn,
            max_show_items=args.max_show_items, run=run,
        )  # fmt: skip
        gh = 0
        if args.gharchive_min_stars > 0:
            gh = gharchive_screen(db, watch, min_stars=args.gharchive_min_stars, run=run)
        return {**res.to_dict(), "gharchive_nominated": gh, "watchlist": watch.counts()}

    return _run("hn-screen", args, body, token=False)


def _host_id(db: Any, full_name: str) -> int:
    row = db.conn.execute(
        "SELECT repo_host_id FROM watchlist WHERE lower(full_name) = lower(%s)"
        " AND repo_host_id IS NOT NULL UNION ALL SELECT host_id FROM repos"
        " WHERE lower(full_name) = lower(%s) LIMIT 1",
        (full_name, full_name),
    ).fetchone()
    if row is None:
        raise _Exit(
            2,
            "unknown repo id: add it with `capture github watch-add` and run `watchlist-counts` "
            "first (the id is resolved through GraphQL)",
        )
    return int(row[0])


def cmd_star_history(args: argparse.Namespace) -> int:
    def body(s: Any, db: Any, run: Any) -> dict[str, Any]:
        from pigtail.capture.star_history import fetch_star_history
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
        rows = db.conn.execute(
            """
            SELECT w.repo_host_id, w.full_name FROM watchlist w
            WHERE w.active AND w.repo_host_id IS NOT NULL AND (
                w.baseline_fetched_at IS NULL
                OR (w.baseline_fetched_at < now() - interval '20 hours' AND (
                    w.pinned OR (SELECT max(s.stars) - min(s.stars) FROM repo_count_snapshot s
                                 WHERE s.repo_host_id = w.repo_host_id
                                   AND s.observed_at > now() - interval '24 hours') >= %s)))
            ORDER BY w.pinned DESC, w.baseline_fetched_at NULLS FIRST, w.last_stars DESC NULLS LAST
            """,
            (args.prethreshold,),
        ).fetchall()
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


def cmd_detect_v1(args: argparse.Namespace) -> int:
    def body(s: Any, db: Any, run: Any) -> dict[str, Any]:
        from pigtail.capture.detection_v1 import DetectionV1Config, DetectorV1, agreement_stats
        from pigtail.capture.star_history import fetch_star_history
        from pigtail.connectors.base import ADR022_ENV
        from pigtail.connectors.github import GitHubRepoEventsConnector

        fetch = None
        if not args.no_fetch:
            conn = _github(s, db, run, _budget(db, "detect-v1", {"core": args.max_requests}))

            def fetch(host_id: int, name: str) -> None:
                fetch_star_history(conn, db, host_id, name, run=run)

        events_on = (
            GitHubRepoEventsConnector.enabled_from_env(os.environ)
            and os.environ.get(ADR022_ENV, "").strip() == "1"
        )
        cfg = DetectionV1Config(min_stars_48h=args.min_stars, sigma=args.sigma)
        det = DetectorV1(db, cfg=cfg, fetch_history=fetch, events_enabled=events_on, run=run)
        res = det.detect(args.at)
        since = (args.at or datetime.now(UTC)) - timedelta(days=30)
        return {**res.to_dict(), "agreement_30d": agreement_stats(db, since)}

    return _run("detect-v1", args, body, token=not args.no_fetch)


def cmd_repo_events(args: argparse.Namespace) -> int:
    def body(s: Any, db: Any, run: Any) -> dict[str, Any]:
        from pigtail.capture.repo_events import EventsConfig, RepoEventsPoller
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
        cfg = EventsConfig(include_prethreshold=args.prethreshold)
        return RepoEventsPoller(conn, db, cfg=cfg, run=run).poll_due().to_dict()

    return _run("repo-events", args, body)


def cmd_settle_lag(args: argparse.Namespace) -> int:
    """K2 settle_lag collection (M4-T4): star-history re-fetches at +1/3/7/14/21 days."""

    def body(s: Any, db: Any, run: Any) -> dict[str, Any]:
        from pigtail.capture.settle_lag import SettleLagConfig, collect
        from pigtail.privacy import suppression

        conn = _github(s, db, run, _budget(db, "settle-lag", {"core": args.max_requests}))
        cfg = SettleLagConfig(max_repos=args.max_repos)
        return collect(conn, db, suppression.load(db), cfg=cfg, run=run).to_dict()

    return _run("settle-lag", args, body)


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
        gq = db.conn.execute(
            "SELECT count(*), sum(cost), sum(n_aliases), max(seconds) FROM github_graphql_batch"
            " WHERE observed_at >= %s",
            (datetime.now(UTC) - timedelta(hours=args.hours),),
        ).fetchone()
        return {
            "caps_per_hour": dict(cfg.per_hour),
            "reserve_fraction": cfg.reserve_fraction,
            "ledger": [dict(zip(keys, r, strict=True)) for r in rows],
            "graphql_batches": {
                "batches": gq[0] if gq else 0,
                "points": gq[1] if gq else None,
                "aliases": gq[2] if gq else None,
                "max_seconds": gq[3] if gq else None,
            },
        }

    return _run("budget", args, body, token=False)


def _parse_at(v: str) -> datetime:
    dt = datetime.fromisoformat(v)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def add_commands(cap_sub: Any) -> None:
    gh = cap_sub.add_parser("github", help="GitHub watch list, screens, detection v1 (ADR-032)")
    sub = gh.add_subparsers(dest="github_command", required=True)

    p = sub.add_parser("watch-add", help="add a repo to the watch list (pinned)")
    p.add_argument("--repo", required=True, help="owner/name")
    p.add_argument("--source", choices=("manual", "case"), default="manual")
    p.set_defaults(func=cmd_watch_add)

    p = sub.add_parser("watchlist-counts", help="hourly GraphQL star/fork counts (100 per query)")
    p.add_argument("--max-points", type=int, help="GraphQL points for this run (default 700)")
    p.add_argument("--limit", type=int, help="count at most N active repos")
    p.add_argument("--cap", type=int, default=50_000, help="watch-list cap (default 50,000)")
    p.set_defaults(func=cmd_watchlist_counts)

    p = sub.add_parser("search-sweep", help="GitHub Search sweeps into the watch list")
    p.add_argument("--kind", choices=("new", "active", "all"), default="all")
    p.add_argument("--max-requests", type=int, help="search requests for this run (default 400)")
    p.add_argument("--new-days", type=int, default=7)
    p.add_argument("--new-min-stars", type=int, default=20)
    p.add_argument("--active-days", type=int, default=1)
    p.add_argument("--active-band", type=_band, action="append", help="LO..HI (repeatable)")
    p.set_defaults(func=cmd_search_sweep)

    p = sub.add_parser("hn-screen", help="GitHub URLs from HN (incl. Show HN) and GH Archive")
    p.add_argument("--window-hours", type=int, default=48)
    p.add_argument("--no-show", action="store_true", help="skip the showstories list")
    p.add_argument("--max-show-items", type=int, default=200)
    p.add_argument("--gharchive-min-stars", type=int, default=10, help="0 disables")
    p.set_defaults(func=cmd_hn_screen)

    p = sub.add_parser("star-history", help="star-history daily series (core bucket)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--repo", help="owner/name (known id)")
    g.add_argument("--candidates", action="store_true", help="new, pre-threshold and case repos")
    p.add_argument("--full", action="store_true", help="--repo: page back to the creation week")
    p.add_argument("--prethreshold", type=int, default=30, help="24 h star growth for refresh")
    p.add_argument("--max-requests", type=int, help="core requests for this run (default 400)")
    p.set_defaults(func=cmd_star_history)

    p = sub.add_parser("detect-v1", help="detection v1 on watch-list counts + star history")
    p.add_argument("--at", type=_parse_at, help="evaluate as of this time (default now)")
    p.add_argument("--no-fetch", action="store_true", help="use stored star history only")
    p.add_argument("--min-stars", type=int, default=100)
    p.add_argument("--sigma", type=float, default=3.0)
    p.add_argument("--max-requests", type=int, help="core requests for this run (default 200)")
    p.set_defaults(func=cmd_detect_v1)

    p = sub.add_parser("repo-events", help="per-repo events for open cases (person-level; off)")
    p.add_argument("--prethreshold", action="store_true", help="also repos above 30 stars/24 h")
    p.add_argument("--max-requests", type=int, help="core requests for this run (default 1600)")
    p.set_defaults(func=cmd_repo_events)

    p = sub.add_parser(
        "settle-lag", help="star-history re-fetches at +1/3/7/14/21 days (K2, M4-T4)"
    )
    p.add_argument("--max-repos", type=int, default=100, help="repos enrolled at a time")
    p.add_argument("--max-requests", type=int, help="core requests for this run (default 200)")
    p.set_defaults(func=cmd_settle_lag)

    p = sub.add_parser("budget", help="GitHub budget ledger (replan §8 M7)")
    p.add_argument("--hours", type=int, default=24)
    p.set_defaults(func=cmd_budget)
