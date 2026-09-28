"""Evidence decay (PRD R19.8; Directive §5.4; ADR-048.3, ADR-063, ADR-086): the share of the
pilot's evidence still retrievable 1, 7 and 30 days after capture.

Every pilot item with a project-level URL (`brief_case_evidence.decay_url`: the repo's API
record, the README now and at its commit, the releases page, the homepage) gets three checks
when it is captured, due at +1, +7 and +30 days (`brief_evidence_decay`). `run_due` performs
the checks that are due: a conditional GET (`If-None-Match` / `If-Modified-Since` where a
validator is known), nothing stored or parsed; the body's SHA-256 is compared with the upstream
hash recorded at capture. Results: `not_modified` (304), `unchanged`, `changed`,
`retrievable` (200, nothing to compare), `gone` (404 or 410, or robots now refusing the page),
`error` (5xx, transport error, rate limit). Derived items (launch events) have no URL and are not
checked; the launch events' own sources are person-level and are not re-fetched here.

GitHub checks use the connector's budget and rate limits; without `GITHUB_TOKEN` they stay due
(counted as `skipped_no_token`, retried next time). Scheduled by `infra/schedule.toml` job
`evidence_decay` (`pigtail brief decay --all --due`, every 6 h) and runnable by hand.

`aggregate` reports per source kind and age: due, checked, retrievable, changed, gone, errors,
and the lost share. R19.8: more than 10 % lost at 7 days means the cadence for new breakouts is
shortened through an ADR (flag `cadence_adr_needed`). The +1 and +7 day results are available a
week after the pilot; the +30 day results complete a month after it.

**Lateness** (ADR-086 addendum 3, verifier M23 round 1): a check runs when it is due or later
(the scheduler runs every 6 h; a machine that is off runs it when it is back). Each check stores
its **actual age**: the hours between the evidence's capture (`brief_case_evidence.captured_at`)
and the check (`age_hours`), and whether it ran **on time**: no later than the offset plus its
tolerance (`ON_TIME_TOLERANCE_HOURS`: 12 h for +1 d, 24 h for +7 d, 48 h for +30 d; `on_time`).
The aggregation reports the actual ages per source and offset (minimum, median, maximum) and
counts the late checks; a late check is still a result, but it measures decay at its actual
age, not the nominal one, and the report says so (`late_checks`, per offset and in R19.8).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

DECAY_VERSION = "decay-v2"  # v2: actual age at check and on-time flag (addendum 3)
# How late a check may run and still count as on time for its offset (hours after due).
ON_TIME_TOLERANCE_HOURS: dict[int, float] = {1: 12.0, 7: 24.0, 30: 48.0}
LOST_AT_7D_ADR_ABOVE = 0.10  # R19.8
GONE_STATUSES = (404, 410)


@dataclass
class DecayRun:
    checked: int = 0
    skipped_no_token: int = 0
    skipped_no_connector: int = 0
    results: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "checked": self.checked,
            "skipped_no_token": self.skipped_no_token,
            "skipped_no_connector": self.skipped_no_connector,
            "results": dict(sorted(self.results.items())),
        }


def classify(probe: Any, upstream_hash: str | None) -> tuple[str, bool | None, bool]:
    """(result, hash_changed, gone) of one conditional GET."""
    st = probe.status
    if st is None or (isinstance(st, int) and st >= 500) or st in (403, 429):
        return "error", None, False
    if st in GONE_STATUSES:
        return "gone", None, True
    if st == 304:
        return "not_modified", False, False
    if 200 <= st < 300:
        if upstream_hash is None or probe.body_hash is None:
            return "retrievable", None, False
        changed = probe.body_hash != upstream_hash
        return ("changed" if changed else "unchanged"), changed, False
    return "error", None, False


def run_due(
    conn: psycopg.Connection[Any],
    *,
    now: datetime,
    github: Any = None,
    pages: Any = None,
    brief_id: str | None = None,
    limit: int = 500,
) -> DecayRun:
    """Check every due, unchecked observation (of `brief_id`, else of every brief)."""
    rows = conn.execute(
        "SELECT d.id, d.kind, e.decay_url, e.upstream_hash, e.etag, e.last_modified,"
        " d.offset_days, e.captured_at"
        " FROM brief_evidence_decay d JOIN brief_case_evidence e ON e.id = d.case_evidence_id"
        " WHERE d.checked_at IS NULL AND d.due_at <= %s"
        " AND (%s::text IS NULL OR d.brief_id = %s)"
        " ORDER BY d.due_at, d.id LIMIT %s",
        (now, brief_id, brief_id, limit),
    ).fetchall()
    out = DecayRun()
    for did, kind, url, upstream, etag, lastmod, offset, captured in rows:
        checker: Callable[..., Any] | None
        if kind == "homepage":
            checker = pages.probe if pages is not None else None
        else:
            checker = github.probe if github is not None else None
        if checker is None:
            if kind == "homepage":
                out.skipped_no_connector += 1
            else:
                out.skipped_no_token += 1
            continue
        probe = checker(url, etag=etag, last_modified=lastmod)
        if getattr(probe, "note", None) == "robots_disallowed":
            result, changed, gone = "gone", None, True  # no longer retrievable for pigtail
        else:
            result, changed, gone = classify(probe, upstream)
        age, on_time = age_at_check(captured, now, int(offset))
        conn.execute(
            "UPDATE brief_evidence_decay SET checked_at = %s, http_status = %s, result = %s,"
            " hash_changed = %s, gone = %s, detail = %s, age_hours = %s, on_time = %s"
            " WHERE id = %s",
            (
                now,
                probe.status,
                result,
                changed,
                gone,
                Jsonb(
                    {
                        "conditional": bool(etag or lastmod),
                        "version": DECAY_VERSION,
                        "note": getattr(probe, "note", None),
                    }
                ),
                age,
                on_time,
                did,
            ),
        )
        out.checked += 1
        out.results[result] = out.results.get(result, 0) + 1
    return out


def age_at_check(
    captured: datetime | None, checked: datetime, offset_days: int
) -> tuple[float | None, bool | None]:
    """(hours between capture and check, on time) of one check (module docstring)."""
    if captured is None:
        return None, None
    hours = (checked - captured).total_seconds() / 3600.0
    limit = offset_days * 24.0 + ON_TIME_TOLERANCE_HOURS.get(offset_days, 24.0)
    return round(hours, 2), hours <= limit


def _ages(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "median": None, "max": None}
    v = sorted(values)
    mid = len(v) // 2
    med = v[mid] if len(v) % 2 else (v[mid - 1] + v[mid]) / 2
    return {"min": round(v[0], 1), "median": round(med, 1), "max": round(v[-1], 1)}


def aggregate(conn: psycopg.Connection[Any], brief_id: str | None = None) -> dict[str, Any]:
    """Counts per source kind and age (offset), the actual ages at check and the late checks,
    and the R19.8 check at 7 days. The age is recomputed from the capture and check times, so
    checks made before `age_hours` was stored are reported too."""
    rows = conn.execute(
        "SELECT kind, offset_days, count(*), count(checked_at),"
        " count(*) FILTER (WHERE result IN ('unchanged', 'changed', 'not_modified',"
        " 'retrievable')), count(*) FILTER (WHERE result = 'changed'),"
        " count(*) FILTER (WHERE gone), count(*) FILTER (WHERE result = 'error'),"
        " count(*) FILTER (WHERE result IN ('unchanged', 'not_modified'))"
        " FROM brief_evidence_decay WHERE (%s::text IS NULL OR brief_id = %s)"
        " GROUP BY kind, offset_days ORDER BY kind, offset_days",
        (brief_id, brief_id),
    ).fetchall()
    checked_rows = conn.execute(
        "SELECT d.kind, d.offset_days, e.captured_at, d.checked_at"
        " FROM brief_evidence_decay d JOIN brief_case_evidence e ON e.id = d.case_evidence_id"
        " WHERE d.checked_at IS NOT NULL AND (%s::text IS NULL OR d.brief_id = %s)",
        (brief_id, brief_id),
    ).fetchall()
    ages: dict[tuple[str, int], list[float]] = {}
    late: dict[tuple[str, int], int] = {}
    for kind, off, captured, checked in checked_rows:
        hours, on_time = age_at_check(captured, checked, int(off))
        if hours is None:
            continue
        ages.setdefault((str(kind), int(off)), []).append(hours)
        if on_time is False:
            late[(str(kind), int(off))] = late.get((str(kind), int(off)), 0) + 1
    by: list[dict[str, Any]] = []
    per_age: dict[int, dict[str, int]] = {}
    age_values: dict[int, list[float]] = {}
    for kind, off, n, checked, ok, changed, gone, err, same in rows:
        key = (str(kind), int(off))
        age_values.setdefault(int(off), []).extend(ages.get(key, []))
        by.append(
            {
                "kind": kind,
                "offset_days": off,
                "scheduled": n,
                "checked": checked,
                "retrievable": ok,
                "unchanged": same,
                "changed": changed,
                "gone": gone,
                "errors": err,
                "lost_share": round(gone / checked, 4) if checked else None,
                "age_hours_at_check": _ages(ages.get(key, [])),
                "late_checks": late.get(key, 0),
            }
        )
        a = per_age.setdefault(
            int(off),
            {
                "scheduled": 0,
                "checked": 0,
                "gone": 0,
                "changed": 0,
                "retrievable": 0,
                "late_checks": 0,
            },
        )
        a["late_checks"] += late.get(key, 0)
        a["scheduled"] += n
        a["checked"] += checked
        a["gone"] += gone
        a["changed"] += changed
        a["retrievable"] += ok
    by_age: dict[str, dict[str, Any]] = {
        f"+{d}d": {
            **v,
            "lost_share": round(v["gone"] / v["checked"], 4) if v["checked"] else None,
            "complete": v["checked"] == v["scheduled"],
            "nominal_hours": d * 24,
            "on_time_within_hours": d * 24 + ON_TIME_TOLERANCE_HOURS.get(d, 24.0),
            "age_hours_at_check": _ages(age_values.get(d, [])),
        }
        for d, v in sorted(per_age.items())
    }
    seven = by_age.get("+7d") or {}
    lost7 = seven.get("lost_share")
    return {
        "version": DECAY_VERSION,
        "by_kind_and_age": by,
        "by_age": by_age,
        "tolerance_hours": {f"+{d}d": h for d, h in ON_TIME_TOLERANCE_HOURS.items()},
        "late_checks": sum(v["late_checks"] for v in per_age.values()),
        "r19_8": {
            "lost_at_7d": lost7,
            "threshold": LOST_AT_7D_ADR_ABOVE,
            "cadence_adr_needed": bool(lost7 is not None and lost7 > LOST_AT_7D_ADR_ABOVE),
            "complete_at_7d": bool(seven.get("complete")),
            "late_checks_at_7d": int(seven.get("late_checks") or 0),
            "age_hours_at_7d": seven.get("age_hours_at_check"),
        },
        "note": (
            "the +30-day checks complete a month after capture; a late check measures decay "
            "at its actual age (age_hours_at_check), not the nominal one"
        ),
    }
