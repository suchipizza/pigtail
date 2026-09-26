"""Read-only queries behind the D1 preview (R14.2, R13.2).

Every number returned here carries the evidence id(s) it was computed from, so the UI can open
the evidence record and its snapshot in one click (R13.2). Free text that came from a source
(titles, URLs) passes the output guard (`guard_text`); person-level fields (HN authors) are not
returned at all. Everything runs inside the caller's read-only transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from pigtail.pseudonymize import scrub_identifiers

Conn = psycopg.Connection[Any]
Bucket = Literal["hour", "day"]
CaseSort = Literal["recency", "velocity"]
EvidenceSort = Literal["fetched_at", "source", "reliability", "deletion_state", "retention_class"]

# ADR-032.3, ADR-047.8: stars come from GitHub's star-history endpoint; every view of star counts
# carries this caveat.
COVERAGE_CAVEAT = (
    "Daily stars come from GitHub's star-history endpoint: net counts of the current stars "
    "by star date (un-stars are netted out; no identities). Day labels are the endpoint's own and "
    "are not UTC-aligned (inferred US Pacific). Days that were never fetched are unknown, not "
    "zero. Detection numbers on older cases were computed by the removed global detection "
    "(GH Archive, about 0.7% of public stars, ADR-028) and are shown as recorded."
)
UNCODED_NOTE = "Uncoded preview: events are raw captures, not coded (codebook not applied yet)."
MAX_HOURLY_SPAN = timedelta(days=60)
MAX_SPAN = timedelta(days=400)

_EVIDENCE_COLS = (
    "e.id, e.source, e.url, e.fetched_at, e.content_hash, e.content_type, e.http_status,"
    " e.reliability, e.terms_basis, e.retention_class, e.deletion_state, e.collector_version,"
    " e.case_id, e.repo_id, e.run_id"
)
_EVIDENCE_SORT: dict[str, str] = {
    "fetched_at": "e.fetched_at",
    "source": "e.source",
    "reliability": (
        "CASE e.reliability WHEN 'high' THEN 0 WHEN 'medium' THEN 1 WHEN 'low' THEN 2 ELSE 3 END"
    ),
    "deletion_state": "e.deletion_state",
    "retention_class": "e.retention_class",
}
# Roles an evidence record can play for a case (see `_case_refs`).
ROLES = (
    "case",
    "repo",
    "hn_story",
    "hn_mention",
    "hn_mention_item",
    "hn_rank_poll",
)
EVENT_ROLES = ("case", "repo", "hn_story", "hn_mention", "hn_mention_item")


def guard_text(value: str | None) -> str | None:
    """Output guard: no raw handles, e-mails or profile URLs in API text (keyless placeholders)."""
    return None if value is None else scrub_identifiers(value)


def _rows(
    conn: Conn, query: str | sql.SQL | sql.Composed, params: dict[str, Any]
) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        return cur.fetchall()


def _one(conn: Conn, query: str, params: dict[str, Any]) -> dict[str, Any] | None:
    rows = _rows(conn, query, params)
    return rows[0] if rows else None


def evidence_view(r: dict[str, Any]) -> dict[str, Any]:
    """Public shape of an evidence row (URL guarded; snapshot link only when bytes exist)."""
    present = r["deletion_state"] == "present"
    return {
        "id": r["id"],
        "source": r["source"],
        "url": guard_text(r["url"]),
        "fetched_at": r["fetched_at"],
        "content_hash": r["content_hash"],
        "content_type": r["content_type"],
        "http_status": r["http_status"],
        "reliability": r["reliability"],
        "terms_basis": r["terms_basis"],
        "retention_class": r["retention_class"],
        "deletion_state": r["deletion_state"],
        "collector_version": r["collector_version"],
        "case_id": r["case_id"],
        "repo_id": r["repo_id"],
        "run_id": r["run_id"],
        "snapshot": {
            "available": present,
            "href": f"/api/snapshots/{r['content_hash']}?evidence={r['id']}" if present else None,
            "state": r["deletion_state"],
        },
    }


# --- cases ----------------------------------------------------------------------------------------
def list_cases(
    conn: Conn,
    *,
    status: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
    sort: CaseSort,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    order = (
        "((c.detection ->> 'stars_48h')::integer) DESC NULLS LAST, c.opened_at DESC, c.id"
        if sort == "velocity"
        else "c.opened_at DESC, c.id"
    )
    rows = _rows(
        conn,
        sql.SQL(
            """
            SELECT c.id, c.repo_id, r.full_name AS repo_full_name, c.opened_at, c.closed_at,
                   c.trigger, c.status,
                   (c.detection ->> 'stars_48h')::integer AS stars_48h,
                   (c.detection ->> 'z_score')::float AS z_score,
                   (c.detection ->> 'detected_hour')::timestamptz AS detected_hour,
                   c.detection ->> 'baseline_quality' AS baseline_quality,
                   (c.detection -> 'coverage' ->> 'ratio')::float AS coverage_ratio,
                   (SELECT count(*) FROM evidence e
                     WHERE e.case_id = c.id OR e.repo_id = c.repo_id) AS evidence_linked,
                   count(*) OVER () AS total
            FROM cases c JOIN repos r ON r.id = c.repo_id
            WHERE (%(status)s::text IS NULL OR c.status = %(status)s)
              AND (%(from)s::timestamptz IS NULL OR c.opened_at >= %(from)s)
              AND (%(to)s::timestamptz IS NULL OR c.opened_at < %(to)s)
            ORDER BY {order}
            LIMIT %(limit)s OFFSET %(offset)s
            """
        ).format(order=sql.SQL(order)),
        {"status": status, "from": date_from, "to": date_to, "limit": limit, "offset": offset},
    )
    total = int(rows[0]["total"]) if rows else 0
    items = []
    for r in rows:
        r.pop("total")
        items.append(r)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def _case_row(conn: Conn, case_id: str) -> dict[str, Any] | None:
    return _one(
        conn,
        """
        SELECT c.id, c.repo_id, c.opened_at, c.closed_at, c.trigger, c.status, c.run_id,
               c.detection, c.created_at,
               r.host, r.host_id, r.full_name, r.first_seen_at, r.created_at AS repo_created_at
        FROM cases c JOIN repos r ON r.id = c.repo_id WHERE c.id = %(id)s
        """,
        {"id": case_id},
    )


def get_case(conn: Conn, case_id: str) -> dict[str, Any] | None:
    c = _case_row(conn, case_id)
    if c is None:
        return None
    det = c["detection"]
    counts = _rows(
        conn,
        _case_refs_sql("SELECT r.role, count(DISTINCT r.evidence_id) AS n FROM refs r GROUP BY 1"),
        {"cid": case_id},
    )
    return {
        "case": {
            "id": c["id"],
            "repo_id": c["repo_id"],
            "opened_at": c["opened_at"],
            "closed_at": c["closed_at"],
            "trigger": c["trigger"],
            "status": c["status"],
            "run_id": c["run_id"],
            "created_at": c["created_at"],
        },
        "repo": {
            "id": c["repo_id"],
            "host": c["host"],
            "host_id": c["host_id"],
            "full_name": c["full_name"],
            "first_seen_at": c["first_seen_at"],
            "created_at": c["repo_created_at"],
        },
        "detection": det,
        "coverage": det.get("coverage") if det else None,
        "evidence_counts": {r["role"]: r["n"] for r in counts},
        "caveats": [COVERAGE_CAVEAT, UNCODED_NOTE],
        "coded": False,
    }


# --- evidence belonging to a case ----------------------------------------------------------------
def _case_refs_sql(tail: str | sql.Composed) -> sql.Composed:
    """`refs(evidence_id, role)`: every evidence record behind case `%(cid)s`."""
    return sql.SQL(
        """
        WITH c AS (
            SELECT c.id, c.repo_id, r.host_id, lower(r.full_name) AS lname
            FROM cases c JOIN repos r ON r.id = c.repo_id WHERE c.id = %(cid)s
        ),
        stories AS (
            SELECT s.item_id, s.evidence_id FROM hn_story s, c
            WHERE s.repo_id = c.repo_id OR s.repo_full_name = c.lname
        ),
        mentions AS (
            SELECT m.evidence_id, m.item_evidence_id FROM hn_mention m, c
            WHERE m.repo_id = c.repo_id OR m.case_id = c.id
        ),
        refs(evidence_id, role) AS (
            SELECT e.id, 'case' FROM evidence e, c WHERE e.case_id = c.id
            UNION ALL
            SELECT e.id, 'repo' FROM evidence e, c
             WHERE e.repo_id = c.repo_id AND e.case_id IS DISTINCT FROM c.id
            UNION ALL
            SELECT evidence_id, 'hn_story' FROM stories WHERE evidence_id IS NOT NULL
            UNION ALL
            SELECT evidence_id, 'hn_mention' FROM mentions WHERE evidence_id IS NOT NULL
            UNION ALL
            SELECT item_evidence_id, 'hn_mention_item' FROM mentions
             WHERE item_evidence_id IS NOT NULL
            UNION ALL
            SELECT DISTINCT p.evidence_id, 'hn_rank_poll'
            FROM stories s
            JOIN hn_rank_observation o ON o.item_id = s.item_id
            JOIN hn_rank_poll p ON p.observed_at = o.observed_at
            WHERE p.evidence_id IS NOT NULL
        )
        """
    ) + (sql.SQL(tail) if isinstance(tail, str) else tail)


def case_exists(conn: Conn, case_id: str) -> bool:
    return _one(conn, "SELECT 1 AS ok FROM cases WHERE id = %(id)s", {"id": case_id}) is not None


def case_evidence(
    conn: Conn,
    case_id: str,
    *,
    sort: EvidenceSort,
    order: Literal["asc", "desc"],
    source: str | None,
    role: str | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    direction = "DESC" if order == "desc" else "ASC"
    tail = f"""
        SELECT {_EVIDENCE_COLS}, array_agg(DISTINCT r.role ORDER BY r.role) AS roles,
               count(*) OVER () AS total
        FROM refs r JOIN evidence e ON e.id = r.evidence_id
        WHERE (%(source)s::text IS NULL OR e.source = %(source)s)
          AND (%(role)s::text IS NULL OR r.role = %(role)s)
        GROUP BY e.id
        ORDER BY {_EVIDENCE_SORT[sort]} {direction}, e.fetched_at DESC, e.id
        LIMIT %(limit)s OFFSET %(offset)s
    """
    rows = _rows(
        conn,
        _case_refs_sql(tail),
        {"cid": case_id, "source": source, "role": role, "limit": limit, "offset": offset},
    )
    sources = _rows(
        conn,
        _case_refs_sql(
            "SELECT DISTINCT e.source FROM refs r JOIN evidence e ON e.id = r.evidence_id"
            " ORDER BY 1"
        ),
        {"cid": case_id},
    )
    items = [{**evidence_view(r), "roles": r["roles"]} for r in rows]
    return {
        "items": items,
        "total": int(rows[0]["total"]) if rows else 0,
        "limit": limit,
        "offset": offset,
        "sources": [s["source"] for s in sources],
        "roles": list(ROLES),
    }


# --- timeline ------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime
    bucket: Bucket


def _extent(conn: Conn, c: dict[str, Any]) -> tuple[datetime, datetime]:
    row = _one(
        conn,
        """
        SELECT least(
                 (SELECT min(day)::timestamp AT TIME ZONE 'UTC' FROM repo_star_daily
                   WHERE repo_host_id = %(hid)s),
                 (SELECT min(o.observed_at) FROM hn_rank_observation o
                   JOIN hn_story s ON s.item_id = o.item_id
                   WHERE s.repo_id = %(rid)s OR s.repo_full_name = %(lname)s),
                 %(opened)s::timestamptz - interval '1 day') AS lo,
               greatest(
                 (SELECT max(day)::timestamp AT TIME ZONE 'UTC' + interval '23 hours'
                   FROM repo_star_daily WHERE repo_host_id = %(hid)s),
                 (SELECT max(o.observed_at) FROM hn_rank_observation o
                   JOIN hn_story s ON s.item_id = o.item_id
                   WHERE s.repo_id = %(rid)s OR s.repo_full_name = %(lname)s),
                 %(opened)s::timestamptz + interval '1 day') AS hi
        """,
        {
            "hid": c["host_id"],
            "rid": c["repo_id"],
            "lname": c["full_name"].lower(),
            "opened": c["opened_at"],
        },
    )
    assert row is not None
    lo: datetime = row["lo"]
    hi: datetime = row["hi"]
    return lo.replace(minute=0, second=0, microsecond=0), hi + timedelta(hours=1)


def resolve_window(
    conn: Conn,
    c: dict[str, Any],
    start: datetime | None,
    end: datetime | None,
    bucket: Bucket | None,
) -> Window:
    lo, hi = _extent(conn, c)
    s = start or lo
    e = end or hi
    if e <= s:
        raise ValueError("`to` must be after `from`")
    if e - s > MAX_SPAN:
        if start is None:
            s = e - MAX_SPAN
        else:
            raise ValueError(f"range longer than {MAX_SPAN.days} days")
    b: Bucket = bucket or ("hour" if e - s <= timedelta(days=14) else "day")
    if b == "hour" and e - s > MAX_HOURLY_SPAN:
        raise ValueError(f"hourly buckets are limited to {MAX_HOURLY_SPAN.days} days; use day")
    return Window(s, e, b)


def timeline(
    conn: Conn,
    case_id: str,
    *,
    start: datetime | None,
    end: datetime | None,
    bucket: Bucket | None,
) -> dict[str, Any] | None:
    c = _case_row(conn, case_id)
    if c is None:
        return None
    w = resolve_window(conn, c, start, end, bucket)
    p = {
        "f": w.start,
        "t": w.end,
        "b": w.bucket,
        "hid": c["host_id"],
        "rid": c["repo_id"],
        "lname": c["full_name"].lower(),
        "cid": case_id,
    }
    # Star history is daily (endpoint day labels, not UTC-aligned): one point per day, whatever
    # the bucket; `t` is the day label at 00:00 UTC. Unfetched days are absent (unknown).
    github = _rows(
        conn,
        """
        SELECT day::timestamp AT TIME ZONE 'UTC' AS t, stars_net, is_partial,
               CASE WHEN evidence_id IS NULL THEN '{}'::text[] ELSE ARRAY[evidence_id] END
                   AS evidence_ids
        FROM repo_star_daily
        WHERE repo_host_id = %(hid)s
          AND day >= (%(f)s::timestamptz AT TIME ZONE 'UTC')::date
          AND day < (%(t)s::timestamptz AT TIME ZONE 'UTC')::date + 1
        ORDER BY day
        """,
        p,
    )

    stories = _rows(
        conn,
        """
        SELECT item_id, type, url, title, created_at, score, descendants, deleted, dead,
               best_rank, first_seen_at, last_seen_at, evidence_id
        FROM hn_story WHERE repo_id = %(rid)s OR repo_full_name = %(lname)s
        ORDER BY first_seen_at, item_id
        """,
        p,
    )
    for s in stories:
        gone = s["deleted"] or s["dead"]
        s["title"] = "[deleted upstream]" if gone else guard_text(s["title"])
        s["url"] = None if gone else guard_text(s["url"])
    items = [s["item_id"] for s in stories]
    ranks = (
        _rows(
            conn,
            """
            SELECT o.item_id, date_trunc(%(b)s, o.observed_at) AS t,
                   min(o.rank) AS best_rank, max(o.score) AS score, count(*) AS observations,
                   (array_agg(o.observed_at ORDER BY o.rank, o.observed_at))[1] AS observed_at,
                   (array_agg(p.evidence_id ORDER BY o.rank, o.observed_at))[1] AS evidence_id
            FROM hn_rank_observation o JOIN hn_rank_poll p ON p.observed_at = o.observed_at
            WHERE o.item_id = ANY(%(items)s) AND o.observed_at >= %(f)s AND o.observed_at < %(t)s
            GROUP BY 1, 2 ORDER BY 1, 2
            """,
            {**p, "items": items},
        )
        if items
        else []
    )
    mentions = _rows(
        conn,
        """
        SELECT item_id, item_type, created_at, story_id, points, num_comments, title,
               match_kind, front_page_tag, show_hn, ask_hn, evidence_id, item_evidence_id
        FROM hn_mention
        WHERE (repo_id = %(rid)s OR case_id = %(cid)s)
          AND coalesce(created_at, first_seen_at) >= %(f)s
          AND coalesce(created_at, first_seen_at) < %(t)s
        ORDER BY coalesce(created_at, first_seen_at), item_id
        """,
        p,
    )
    for m in mentions:
        m["title"] = guard_text(m["title"])
    roles = sql.SQL(", ").join(sql.Literal(r) for r in EVENT_ROLES)
    events = _rows(
        conn,
        _case_refs_sql(
            sql.SQL(
                f"""
                SELECT {_EVIDENCE_COLS}, array_agg(DISTINCT r.role ORDER BY r.role) AS roles
                FROM refs r JOIN evidence e ON e.id = r.evidence_id
                WHERE r.role IN ({{roles}}) AND e.fetched_at >= %(f)s AND e.fetched_at < %(t)s
                GROUP BY e.id ORDER BY e.fetched_at, e.id
                """
            ).format(roles=roles)
        ),
        p,
    )
    return {
        "case_id": case_id,
        "range": {"from": w.start, "to": w.end, "bucket": w.bucket},
        "github": github,
        "hn": {"stories": stories, "ranks": ranks, "mentions": mentions},
        "events": [
            {
                "t": e["fetched_at"],
                "kind": "evidence",
                "roles": e["roles"],
                "evidence": evidence_view(e),
            }
            for e in events
        ],
        "caveats": [COVERAGE_CAVEAT, UNCODED_NOTE],
        "coded": False,
    }


# --- single evidence record ----------------------------------------------------------------------
def get_evidence(conn: Conn, evidence_id: str) -> dict[str, Any] | None:
    r = _one(
        conn, f"SELECT {_EVIDENCE_COLS} FROM evidence e WHERE e.id = %(id)s", {"id": evidence_id}
    )
    if r is None:
        return None
    p = {"id": evidence_id}
    days = _rows(
        conn,
        "SELECT repo_host_id, day FROM repo_star_daily WHERE evidence_id = %(id)s"
        " ORDER BY repo_host_id, day LIMIT 400",
        p,
    )
    polls = _rows(conn, "SELECT observed_at FROM hn_rank_poll WHERE evidence_id = %(id)s", p)
    stories = _rows(
        conn, "SELECT item_id, repo_id FROM hn_story WHERE evidence_id = %(id)s ORDER BY 1", p
    )
    mentions = _rows(
        conn,
        "SELECT DISTINCT item_id, repo_id, case_id FROM hn_mention"
        " WHERE evidence_id = %(id)s OR item_evidence_id = %(id)s ORDER BY 1",
        p,
    )
    duplicates = _rows(
        conn,
        "SELECT id FROM evidence WHERE content_hash = %(h)s AND id <> %(id)s ORDER BY id LIMIT 50",
        {"h": r["content_hash"], "id": evidence_id},
    )
    return {
        "evidence": evidence_view(r),
        "links": {
            "case_id": r["case_id"],
            "repo_id": r["repo_id"],
            "star_history_days": days,
            "hn_rank_polls": [x["observed_at"] for x in polls],
            "hn_stories": stories,
            "hn_mentions": mentions,
            "same_bytes_evidence_ids": [d["id"] for d in duplicates],
        },
    }


def snapshot_states(conn: Conn, content_hash: str) -> list[dict[str, Any]]:
    """Evidence rows referencing a snapshot hash (with their deletion state)."""
    return _rows(
        conn,
        "SELECT id, deletion_state, content_type, source FROM evidence"
        " WHERE content_hash = %(h)s ORDER BY fetched_at",
        {"h": content_hash},
    )
