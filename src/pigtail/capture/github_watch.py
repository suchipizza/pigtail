"""GitHub watch list `U` and hourly count snapshots (M1-T24; ADR-032.1; replan §6.1 layer 2).

**Watch list (`watchlist`).** One row per repo, identified by its GitHub `databaseId` once known
(screens that only know `owner/name`, like HN, are resolved by the next count batch). Each row
records the source that first nominated it (`search | hn | gharchive | manual | case`), every
source that ever nominated it (`sources`), and `active`. Rows are deactivated, never deleted, so
history stays explainable.

**Selection policy** (`WatchPolicy`, applied by `enforce_policy()` before every count run):

1. *Inclusion*: any screen nomination; open cases are nominated with `source = case` and pinned;
   `pigtail capture github watch-add` pins `manual` entries. A nomination re-activates an
   inactive row (except `opted_out`).
2. *Exclusion at once*: repos on the refusal list (CB-13, `github:<id>`), repos GitHub reports as
   missing (`not_found`) or private (`private`).
3. *Staleness*: an unpinned row whose last nomination is older than `stale_days` (30, replan
   §6.1: "every repo any screen produced in the last 30 days") is deactivated (`stale`) unless its
   public star count grew by ≥ `keep_growth_7d` (30, the replan's pre-threshold) over the last 7
   days of snapshots.
4. *Cap* (`cap`, default 50,000 = 500 GraphQL points/h): if more rows are active, the lowest
   priority rows are deactivated (`over_cap`). Priority: pinned first, then 7-day star growth
   (descending), then most recent nomination, then id.

**Hourly counts** (`snapshot_counts`). Active rows are queried 100 per GraphQL request as aliased
fields (`r0 … r99`): `node(id:)` for resolved rows (rename-proof) and `repository(owner:,name:)`
for new ones, each returning `databaseId nameWithOwner stargazerCount forkCount pushedAt createdAt
isPrivate owner{__typename}`. The raw answer is snapshotted first (project-level), then parsed into
`repo_count_snapshot`; each batch's `rateLimit` cost, remaining points and reset time go to
`github_graphql_batch` (replan §8 M3). A batch that fails (e.g. a 502 on a heavy query) is split
in half once. A budget stop ends the run cleanly (resumable: the next run starts again).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from pigtail.capture.db import CaptureDB
from pigtail.capture.runs import RunRecorder
from pigtail.connectors.base import FetchError
from pigtail.connectors.github import GitHubConnector, GraphQLResult
from pigtail.connectors.github_budget import BudgetExhausted
from pigtail.privacy.suppression import Suppressions

log = logging.getLogger("pigtail.capture.github")

WatchSource = Literal["search", "hn", "gharchive", "manual", "case"]
WATCH_SOURCES: tuple[WatchSource, ...] = ("search", "hn", "gharchive", "manual", "case")
PINNED_SOURCES = frozenset({"manual", "case"})
BATCH_SIZE = 100  # aliases per GraphQL query (replan §2.2: 100 lookups = 1 point, measured)

REPO_FIELDS = (
    "fragment R on Repository { id databaseId nameWithOwner stargazerCount forkCount pushedAt "
    "createdAt isPrivate owner { __typename } }"
)


@dataclass(frozen=True)
class WatchPolicy:
    cap: int = 50_000
    stale_days: int = 30
    keep_growth_7d: int = 30
    batch_size: int = BATCH_SIZE

    def __post_init__(self) -> None:
        if not 1 <= self.batch_size <= 100:
            raise ValueError("batch_size must be 1..100 (GraphQL aliases per query)")
        if self.cap < 0:
            raise ValueError("cap must be >= 0")


@dataclass(frozen=True)
class WatchRow:
    id: int
    repo_host_id: int | None
    node_id: str | None
    full_name: str


def _now() -> datetime:
    return datetime.now(UTC)


class Watchlist:
    def __init__(self, db: CaptureDB, suppression: Suppressions | None = None) -> None:
        self.db = db
        self.suppression = suppression or Suppressions()

    def _opted_out(self, repo_host_id: int | None, full_name: str | None = None) -> bool:
        """Opted out by repo id, or by name (M1-T23: covers repos not in `repos`)."""
        if repo_host_id is not None and f"github:{repo_host_id}" in self.suppression.repos:
            return True
        return self.suppression.name_suppressed(full_name)

    def nominate(
        self,
        source: WatchSource,
        full_name: str,
        *,
        repo_host_id: int | None = None,
        node_id: str | None = None,
        owner_type: str | None = None,
        created_at: datetime | None = None,
        source_ref: str | None = None,
        at: datetime | None = None,
    ) -> Literal["added", "refreshed", "reactivated", "skipped"]:
        """Add or refresh a repo. Idempotent; opted-out repos are skipped (CB-13)."""
        if source not in WATCH_SOURCES:
            raise ValueError(f"unknown watch source {source!r}")
        name = full_name.strip()
        if name.count("/") != 1 or not all(name.split("/")):
            raise ValueError(f"not an owner/name: {full_name!r}")
        if self._opted_out(repo_host_id, name):
            return "skipped"
        at = at or _now()
        pinned = source in PINNED_SOURCES
        conn = self.db.conn
        with conn.transaction():
            row = None
            if repo_host_id is not None:
                row = conn.execute(
                    "SELECT id, active, deactivated_reason FROM watchlist WHERE repo_host_id = %s"
                    " FOR UPDATE",
                    (repo_host_id,),
                ).fetchone()
            by_name = conn.execute(
                "SELECT id, active, deactivated_reason, repo_host_id FROM watchlist"
                " WHERE lower(full_name) = lower(%s) FOR UPDATE",
                (name,),
            ).fetchone()
            same = by_name is not None and (
                repo_host_id is None or by_name[3] in (None, repo_host_id)
            )
            if row is None and same:
                assert by_name is not None
                row = by_name[:3]
            elif by_name is not None and (row is None or by_name[0] != row[0]):
                # The name now belongs to another repo id (a rename): free it on the old row;
                # a resolved row gets its current name back from the next count batch.
                conn.execute(
                    "UPDATE watchlist SET full_name = full_name || '#renamed-' || id::text"
                    " WHERE id = %s",
                    (by_name[0],),
                )
            if row is None:
                conn.execute(
                    """
                    INSERT INTO watchlist (repo_host_id, node_id, full_name, source, sources,
                        source_ref, owner_type, added_at, last_nominated_at, pinned, created_at_gh)
                    VALUES (%s, %s, %s, %s, ARRAY[%s], %s, %s, %s, %s, %s, %s)
                    """,
                    (repo_host_id, node_id, name, source, source, source_ref, owner_type, at, at,
                     pinned, created_at),
                )  # fmt: skip
                return "added"
            wid, active, reason = row
            if reason == "opted_out":
                return "skipped"
            conn.execute(
                """
                UPDATE watchlist SET
                    repo_host_id = COALESCE(repo_host_id, %(rid)s),
                    node_id = COALESCE(%(node)s, node_id),
                    full_name = CASE WHEN %(rid)s::bigint IS NOT NULL THEN %(name)s
                                     ELSE full_name END,
                    sources = CASE WHEN %(src)s = ANY(sources) THEN sources
                                   ELSE sources || %(src)s END,
                    source_ref = COALESCE(%(ref)s, source_ref),
                    owner_type = COALESCE(%(owner)s, owner_type),
                    created_at_gh = COALESCE(created_at_gh, %(created)s),
                    last_nominated_at = GREATEST(last_nominated_at, %(at)s),
                    pinned = pinned OR %(pinned)s,
                    active = true, deactivated_at = NULL, deactivated_reason = NULL
                WHERE id = %(id)s
                """,
                {
                    "rid": repo_host_id, "node": node_id, "name": name, "src": source,
                    "ref": source_ref, "owner": owner_type, "created": created_at, "at": at,
                    "pinned": pinned, "id": wid,
                },
            )  # fmt: skip
            return "refreshed" if active else "reactivated"

    def deactivate(self, wid: int, reason: str, at: datetime | None = None) -> None:
        self.db.conn.execute(
            "UPDATE watchlist SET active = false, deactivated_at = %s, deactivated_reason = %s"
            " WHERE id = %s",
            (at or _now(), reason, wid),
        )

    def active_rows(self, limit: int | None = None) -> list[WatchRow]:
        rows = self.db.conn.execute(
            "SELECT id, repo_host_id, node_id, full_name FROM watchlist WHERE active"
            " ORDER BY pinned DESC, id LIMIT %s",
            (limit,),
        ).fetchall()
        return [WatchRow(r[0], r[1], r[2], r[3]) for r in rows]

    def nominate_open_cases(self, at: datetime | None = None) -> int:
        """Open cases are always watched (pinned, `source = case`)."""
        rows = self.db.conn.execute(
            "SELECT DISTINCT r.host_id, r.full_name FROM cases c JOIN repos r ON r.id = c.repo_id"
            " WHERE c.status IN ('live', 'pre_launch') AND r.host = 'github'"
        ).fetchall()
        n = 0
        for host_id, full_name in rows:
            if self.nominate("case", full_name, repo_host_id=host_id, at=at) != "skipped":
                n += 1
        return n

    def enforce_policy(self, policy: WatchPolicy, now: datetime | None = None) -> dict[str, int]:
        """Opt-outs, staleness and the cap (module docstring). Returns counts per reason."""
        now = now or _now()
        conn = self.db.conn
        out = {"opted_out": 0, "stale": 0, "over_cap": 0}
        if self.suppression.repos:
            ids = [
                int(k.split(":", 1)[1])
                for k in self.suppression.repos
                if k.startswith("github:") and k.split(":", 1)[1].isdigit()
            ]
            out["opted_out"] = conn.execute(
                "UPDATE watchlist SET active = false, deactivated_at = %s,"
                " deactivated_reason = 'opted_out' WHERE repo_host_id = ANY(%s)"
                " AND (active OR deactivated_reason IS DISTINCT FROM 'opted_out')",
                (now, ids),
            ).rowcount
        growth = """
            COALESCE((SELECT max(s.stars) - min(s.stars) FROM repo_count_snapshot s
                      WHERE s.repo_host_id = w.repo_host_id
                        AND s.observed_at > %(now)s - interval '7 days'), 0)
        """
        out["stale"] = conn.execute(
            f"""
            UPDATE watchlist w SET active = false, deactivated_at = %(now)s,
                deactivated_reason = 'stale'
            WHERE w.active AND NOT w.pinned
              AND w.last_nominated_at < %(now)s - make_interval(days => %(days)s)
              AND {growth} < %(keep)s
            """,
            {"now": now, "days": policy.stale_days, "keep": policy.keep_growth_7d},
        ).rowcount
        out["over_cap"] = conn.execute(
            f"""
            WITH ranked AS (
                SELECT w.id, row_number() OVER (
                    ORDER BY w.pinned DESC, {growth} DESC, w.last_nominated_at DESC, w.id
                ) AS rn
                FROM watchlist w WHERE w.active
            )
            UPDATE watchlist w SET active = false, deactivated_at = %(now)s,
                deactivated_reason = 'over_cap'
            FROM ranked WHERE ranked.id = w.id AND ranked.rn > %(cap)s
            """,
            {"now": now, "cap": policy.cap},
        ).rowcount
        return out

    def counts(self) -> dict[str, int]:
        rows = self.db.conn.execute(
            "SELECT active, count(*) FROM watchlist GROUP BY active"
        ).fetchall()
        d = {bool(a): int(n) for a, n in rows}
        return {"active": d.get(True, 0), "inactive": d.get(False, 0)}


# --- hourly GraphQL count snapshots ------------------------------------------------------------
def build_batch_query(rows: Sequence[WatchRow]) -> tuple[str, dict[str, Any]]:
    """One query with an alias per row; all values travel as variables (no string splicing)."""
    if not 1 <= len(rows) <= 100:
        raise ValueError("a batch holds 1..100 repos")
    decls: list[str] = []
    fields: list[str] = []
    variables: dict[str, Any] = {}
    for i, r in enumerate(rows):
        if r.node_id:
            decls.append(f"$i{i}: ID!")
            fields.append(f"r{i}: node(id: $i{i}) {{ ...R }}")
            variables[f"i{i}"] = r.node_id
        else:
            owner, name = r.full_name.split("/", 1)
            decls.append(f"$o{i}: String!, $n{i}: String!")
            fields.append(f"r{i}: repository(owner: $o{i}, name: $n{i}) {{ ...R }}")
            variables[f"o{i}"] = owner
            variables[f"n{i}"] = name
    query = (
        f"query({', '.join(decls)}) {{ rateLimit {{ cost limit remaining resetAt used }} "
        f"{' '.join(fields)} }} {REPO_FIELDS}"
    )
    return query, variables


@dataclass
class CountResult:
    batches: int = 0
    batches_failed: int = 0
    repos_counted: int = 0
    repos_missing: int = 0
    repos_private: int = 0
    repos_opted_out: int = 0
    merged: int = 0
    points: int = 0
    budget_stop: str | None = None
    policy: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _ts(v: Any) -> datetime | None:
    if not isinstance(v, str):
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None


class CountSnapshotter:
    def __init__(
        self,
        conn: GitHubConnector,
        db: CaptureDB,
        *,
        policy: WatchPolicy | None = None,
        run: RunRecorder | None = None,
        suppression: Suppressions | None = None,
    ) -> None:
        self.conn = conn
        self.db = db
        self.policy = policy or WatchPolicy()
        self.run = run
        self.watch = Watchlist(db, suppression or conn.suppression)
        if conn.evidence_sink is None:
            conn.evidence_sink = db.upsert_evidence

    @property
    def _run_id(self) -> str | None:
        return self.run.id if self.run else None

    def snapshot_counts(self, *, limit: int | None = None) -> CountResult:
        res = CountResult()
        self.watch.nominate_open_cases()
        res.policy = self.watch.enforce_policy(self.policy, self.conn.clock())
        rows = self.watch.active_rows(limit if limit is not None else self.policy.cap)
        bs = self.policy.batch_size
        try:
            for i in range(0, len(rows), bs):
                self._batch(rows[i : i + bs], res, split=True)
        except BudgetExhausted as e:
            res.budget_stop = e.reason
            log.warning("count snapshots stopped by budget: %s", e.reason)
        if self.run is not None:
            for k, v in res.to_dict().items():
                if isinstance(v, int):
                    self.run.incr(f"counts.{k}", v)
            if res.budget_stop:
                self.run.incr(f"budget_stop.{res.budget_stop}")
        return res

    def _batch(self, rows: Sequence[WatchRow], res: CountResult, *, split: bool) -> None:
        query, variables = build_batch_query(rows)
        t0 = time.perf_counter()
        try:
            gq = self.conn.graphql(query, variables)
        except FetchError as e:
            if split and len(rows) > 1 and e.status in (None, 502, 503, 504):
                mid = len(rows) // 2
                log.warning("GraphQL batch of %d failed (%s); splitting", len(rows), e.status)
                self._batch(rows[:mid], res, split=False)
                self._batch(rows[mid:], res, split=False)
                return
            res.batches_failed += 1
            log.warning("GraphQL batch of %d failed (%s)", len(rows), e.status)
            return
        self._store(rows, gq, res, time.perf_counter() - t0)

    def _store(
        self, rows: Sequence[WatchRow], gq: GraphQLResult, res: CountResult, seconds: float
    ) -> None:
        at = gq.fetched.meta.fetched_at
        ev = gq.fetched.evidence.id
        conn = self.db.conn
        found = missing = 0
        with conn.transaction():
            for i, r in enumerate(rows):
                node = gq.data.get(f"r{i}")
                if not isinstance(node, dict) or node.get("databaseId") is None:
                    missing += 1
                    self.watch.deactivate(r.id, "not_found", at)
                    continue
                host_id = int(node["databaseId"])
                if self.watch._opted_out(host_id):
                    res.repos_opted_out += 1
                    self.watch.deactivate(r.id, "opted_out", at)
                    continue
                if node.get("isPrivate"):
                    res.repos_private += 1
                    self.watch.deactivate(r.id, "private", at)
                    continue
                if self._resolve(r, host_id, node, at):
                    res.merged += 1
                found += 1
                stars = int(node.get("stargazerCount") or 0)
                conn.execute(
                    "INSERT INTO repo_count_snapshot (repo_host_id, observed_at, stars, forks,"
                    " pushed_at, evidence_id) VALUES (%s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT DO NOTHING",
                    (host_id, at, stars, int(node.get("forkCount") or 0),
                     _ts(node.get("pushedAt")), ev),
                )  # fmt: skip
                conn.execute(
                    "UPDATE watchlist SET last_stars = %s, last_counted_at = %s"
                    " WHERE repo_host_id = %s",
                    (stars, at, host_id),
                )
            conn.execute(
                """
                INSERT INTO github_graphql_batch (evidence_id, observed_at, run_id, n_aliases,
                    n_found, n_missing, n_errors, cost, remaining, rate_limit, reset_at, seconds)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (evidence_id) DO NOTHING
                """,
                (ev, at, self._run_id, len(rows), found, missing, len(gq.errors), gq.cost,
                 gq.remaining, gq.limit, gq.reset_at, round(seconds, 3)),
            )  # fmt: skip
        res.batches += 1
        res.repos_counted += found
        res.repos_missing += missing
        res.points += gq.cost or 0

    def _resolve(self, r: WatchRow, host_id: int, node: dict[str, Any], at: datetime) -> bool:
        """Fill id / node id / current name; merge a duplicate row. True if rows were merged."""
        conn = self.db.conn
        other = conn.execute(
            "SELECT id FROM watchlist WHERE repo_host_id = %s AND id <> %s", (host_id, r.id)
        ).fetchone()
        merged = False
        if other is not None:
            # Two rows for one repo (name-only nomination of a known repo): keep the older one.
            keep, drop = sorted((int(other[0]), r.id))
            conn.execute(
                """
                UPDATE watchlist k SET
                    sources = ARRAY(SELECT DISTINCT unnest(k.sources || d.sources)),
                    pinned = k.pinned OR d.pinned,
                    last_nominated_at = GREATEST(k.last_nominated_at, d.last_nominated_at),
                    active = true, deactivated_at = NULL, deactivated_reason = NULL
                FROM watchlist d WHERE k.id = %s AND d.id = %s
                """,
                (keep, drop),
            )
            conn.execute("DELETE FROM watchlist WHERE id = %s", (drop,))
            merged = True
            target = keep
        else:
            target = r.id
        name = str(node.get("nameWithOwner") or r.full_name)
        clash = conn.execute(
            "SELECT id FROM watchlist WHERE lower(full_name) = lower(%s) AND id <> %s",
            (name, target),
        ).fetchone()
        if clash is not None:  # an unresolved row holds the current name: rename it out of the way
            conn.execute(
                "UPDATE watchlist SET full_name = full_name || '#renamed-' || id::text"
                " WHERE id = %s",
                (clash[0],),
            )
        owner = node.get("owner") if isinstance(node.get("owner"), dict) else {}
        conn.execute(
            """
            UPDATE watchlist SET repo_host_id = %s, node_id = COALESCE(%s, node_id),
                full_name = %s, owner_type = COALESCE(%s, owner_type),
                created_at_gh = COALESCE(created_at_gh, %s)
            WHERE id = %s
            """,
            (host_id, node.get("id"), name, (owner or {}).get("__typename"),
             _ts(node.get("createdAt")), target),
        )  # fmt: skip
        return merged
