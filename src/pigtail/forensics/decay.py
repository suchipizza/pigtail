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
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

DECAY_VERSION = "decay-v1"
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
        "SELECT d.id, d.kind, e.decay_url, e.upstream_hash, e.etag, e.last_modified"
        " FROM brief_evidence_decay d JOIN brief_case_evidence e ON e.id = d.case_evidence_id"
        " WHERE d.checked_at IS NULL AND d.due_at <= %s"
        " AND (%s::text IS NULL OR d.brief_id = %s)"
        " ORDER BY d.due_at, d.id LIMIT %s",
        (now, brief_id, brief_id, limit),
    ).fetchall()
    out = DecayRun()
    for did, kind, url, upstream, etag, lastmod in rows:
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
        conn.execute(
            "UPDATE brief_evidence_decay SET checked_at = %s, http_status = %s, result = %s,"
            " hash_changed = %s, gone = %s, detail = %s WHERE id = %s",
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
                did,
            ),
        )
        out.checked += 1
        out.results[result] = out.results.get(result, 0) + 1
    return out


def aggregate(conn: psycopg.Connection[Any], brief_id: str | None = None) -> dict[str, Any]:
    """Counts per source kind and age (offset), and the R19.8 check at 7 days."""
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
    by: list[dict[str, Any]] = []
    per_age: dict[int, dict[str, int]] = {}
    for kind, off, n, checked, ok, changed, gone, err, same in rows:
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
            }
        )
        a = per_age.setdefault(
            int(off), {"scheduled": 0, "checked": 0, "gone": 0, "changed": 0, "retrievable": 0}
        )
        a["scheduled"] += n
        a["checked"] += checked
        a["gone"] += gone
        a["changed"] += changed
        a["retrievable"] += ok
    ages = {
        f"+{d}d": {
            **v,
            "lost_share": round(v["gone"] / v["checked"], 4) if v["checked"] else None,
            "complete": v["checked"] == v["scheduled"],
        }
        for d, v in sorted(per_age.items())
    }
    seven = ages.get("+7d") or {}
    lost7 = seven.get("lost_share")
    return {
        "version": DECAY_VERSION,
        "by_kind_and_age": by,
        "by_age": ages,
        "r19_8": {
            "lost_at_7d": lost7,
            "threshold": LOST_AT_7D_ADR_ABOVE,
            "cadence_adr_needed": bool(lost7 is not None and lost7 > LOST_AT_7D_ADR_ABOVE),
            "complete_at_7d": bool(seven.get("complete")),
        },
        "note": "the +30-day checks complete a month after capture",
    }
