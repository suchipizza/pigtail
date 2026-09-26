"""Data-cache inventory (M11, WORK_ORDER §4.4; ADR-047.6, ADR-051.2): counts only.

`pigtail report inventory [--json]` lists every table the M11 re-scope kept, with its row count,
the number of distinct repos it references (where it references repos) and the date range of its
main time column (days, UTC). It never prints names, handles, URLs, ids or any row content, so its
output can go into `docs/reports/` (the repo is public). Read-only.

The cache is kept for briefs to reuse (ADR-047.6). After the first brief's shortlist is final,
data no brief references is deleted (purpose limitation, reason `purpose_limitation`).

`TABLES` must cover every table of the migrated schema: a table added by a later migration
without an entry here shows up under `unlisted` (counts only) and fails the schema test.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Literal

import psycopg
from psycopg import sql

Group = Literal["cache", "operational"]


@dataclass(frozen=True)
class TableSpec:
    table: str
    group: Group
    repo_expr: str | None  # SQL expression identifying the repo of a row (distinct count)
    date_column: str | None  # main time column (date range)


# `cache`: data briefs can reuse (0014 header). `operational`: runs, privacy, audit and UI state.
TABLES: tuple[TableSpec, ...] = (
    TableSpec("repos", "cache", "id", "first_seen_at"),
    TableSpec("cases", "cache", "repo_id", "opened_at"),
    TableSpec("evidence", "cache", "repo_id", "fetched_at"),
    TableSpec("evidence_upstream_items", "cache", None, None),
    TableSpec("repo_star_daily", "cache", "repo_host_id", "day"),
    TableSpec("star_history_fetch", "cache", "repo_host_id", "fetched_at"),
    TableSpec("repo_event_poll", "cache", "repo_host_id", "polled_at"),
    TableSpec("repo_event_hourly_agg", "cache", "repo_host_id", "hour"),
    TableSpec("repo_event_daily_agg", "cache", "repo_host_id", "day"),
    TableSpec("hn_story", "cache", "repo_id", "created_at"),
    TableSpec("hn_mention", "cache", "COALESCE(repo_id, lower(repo_full_name))", "created_at"),
    TableSpec("hn_rank_poll", "cache", None, "observed_at"),
    TableSpec("hn_rank_observation", "cache", None, "observed_at"),
    TableSpec("upstream_items", "cache", None, "first_seen_at"),
    TableSpec("github_http_cache", "cache", None, "fetched_at"),
    TableSpec("github_budget_ledger", "cache", None, "hour"),
    TableSpec("launch_mode_window", "cache", "repo_id", "starts_at"),
    TableSpec("brief_stage_cache", "cache", "repo_id", "created_at"),
    TableSpec("runs", "operational", None, "started_at"),
    TableSpec("brief_runs", "operational", None, "created_at"),
    TableSpec("shortlist_decision", "operational", "candidate_repo_id", "decided_at"),
    TableSpec("brief_shortlist_entry", "operational", "repo_full_name", "updated_at"),
    TableSpec("brief_candidate", "operational", "repo_full_name", "first_seen_at"),
    TableSpec("brief_shortlist", "operational", None, "created_at"),
    # M22 selection (0022): outcome sort, winners, matched losers, balance, sensitivity
    TableSpec("brief_selection", "operational", None, "created_at"),
    TableSpec("brief_selection_case", "operational", "repo_full_name", None),
    # pre-registration records (0023, R8.2): ids, versions and SHA-256 values only
    TableSpec("brief_preregistration", "operational", None, "recorded_at"),
    TableSpec("brief_report_final", "operational", None, "report_final_at"),
    TableSpec("brief_evidence", "operational", None, "linked_at"),
    # M21b (0020): Message Batches state and the actual-cost ledger (counts only)
    TableSpec("llm_batches", "operational", None, "submitted_at"),
    TableSpec("llm_batch_requests", "operational", None, None),
    TableSpec("llm_cost_ledger", "operational", None, "created_at"),
    TableSpec("deletion_log", "operational", None, "logged_at"),
    TableSpec("privacy_requests", "operational", None, "received_at"),
    TableSpec("privacy_suppression", "operational", None, "added_at"),
    TableSpec("pseudonym_key_fingerprint", "operational", None, "set_at"),
    TableSpec("pseudonym_key_fingerprint_log", "operational", None, "logged_at"),
    TableSpec("ui_sessions", "operational", None, "created_at"),
    TableSpec("ui_audit_log", "operational", None, "at"),
    TableSpec("llm_batches", "operational", None, "submitted_at"),
    TableSpec("llm_batch_requests", "operational", None, None),
    TableSpec("llm_cost_ledger", "operational", None, "created_at"),
    TableSpec("schema_migrations", "operational", None, "applied_at"),
)


@dataclass(frozen=True)
class TableCount:
    table: str
    group: str
    rows: int
    distinct_repos: int | None  # None: the table does not reference repos
    first_day: date | None  # None: no time column, or no rows
    last_day: date | None


@dataclass(frozen=True)
class Inventory:
    data_version: str | None  # latest applied migration
    tables: tuple[TableCount, ...]
    unlisted: tuple[TableCount, ...]  # tables in the schema but not in `TABLES` (rows only)
    missing: tuple[str, ...]  # tables in `TABLES` but not in the schema

    def to_dict(self) -> dict[str, Any]:
        def row(t: TableCount) -> dict[str, Any]:
            d = asdict(t)
            for k in ("first_day", "last_day"):
                d[k] = d[k].isoformat() if d[k] is not None else None
            return d

        return {
            "data_version": self.data_version,
            "tables": [row(t) for t in self.tables],
            "unlisted": [row(t) for t in self.unlisted],
            "missing": list(self.missing),
        }


def schema_tables(conn: psycopg.Connection[Any]) -> set[str]:
    return {
        str(r[0])
        for r in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
        ).fetchall()
    }


def _count(conn: psycopg.Connection[Any], spec: TableSpec) -> TableCount:
    t = sql.Identifier(spec.table)
    repos = (
        sql.SQL("count(DISTINCT {})").format(sql.SQL(spec.repo_expr))  # static, from TABLES
        if spec.repo_expr
        else sql.SQL("NULL::bigint")
    )
    days: sql.Composable
    if spec.date_column == "day":  # already a date
        days = sql.SQL("min({c}), max({c})").format(c=sql.Identifier(spec.date_column))
    elif spec.date_column:
        days = sql.SQL(
            "min(({c} AT TIME ZONE 'UTC')::date), max(({c} AT TIME ZONE 'UTC')::date)"
        ).format(c=sql.Identifier(spec.date_column))
    else:
        days = sql.SQL("NULL::date, NULL::date")
    q = sql.SQL("SELECT count(*), {repos}, {days} FROM {t}").format(repos=repos, days=days, t=t)
    row = conn.execute(q).fetchone()
    assert row is not None
    return TableCount(
        spec.table,
        spec.group,
        int(row[0]),
        None if row[1] is None else int(row[1]),
        row[2],
        row[3],
    )


def inventory(conn: psycopg.Connection[Any]) -> Inventory:
    """Counts only, for every kept table (read-only)."""
    present = schema_tables(conn)
    listed = {s.table for s in TABLES}
    counts = tuple(_count(conn, s) for s in TABLES if s.table in present)
    unlisted = tuple(
        _count(conn, TableSpec(t, "operational", None, None)) for t in sorted(present - listed)
    )
    missing = tuple(s.table for s in TABLES if s.table not in present)
    version = None
    if "schema_migrations" in present:
        r = conn.execute("SELECT max(version) FROM schema_migrations").fetchone()
        version = None if r is None or r[0] is None else str(r[0])
    return Inventory(version, counts, unlisted, missing)


def _fmt(v: Any) -> str:
    return "—" if v is None else str(v)


def render_text(inv: Inventory, code_commit: str | None = None) -> str:
    """Markdown table (also readable in a terminal)."""
    lines = [
        f"data version (latest migration): {_fmt(inv.data_version)}",
        f"code commit: {_fmt(code_commit)}",
        "",
        "| table | group | rows | distinct repos | first day (UTC) | last day (UTC) |",
        "|---|---|---:|---:|---|---|",
    ]
    for t in (*inv.tables, *inv.unlisted):
        lines.append(
            f"| {t.table} | {t.group} | {t.rows} | {_fmt(t.distinct_repos)} "
            f"| {_fmt(t.first_day)} | {_fmt(t.last_day)} |"
        )
    if inv.unlisted:
        lines += ["", "unlisted tables (not in the inventory spec): "
                  + ", ".join(t.table for t in inv.unlisted)]  # fmt: skip
    if inv.missing:
        lines += ["", "missing tables (not migrated?): " + ", ".join(inv.missing)]
    return "\n".join(lines)
