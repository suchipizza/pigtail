"""Unreferenced-cache purge (PRD R19.10; Directive §5.2, ADR-063; purpose limitation, PRD §10):
once a brief's report is final, collected data that no brief references is purged, and the purge
is logged (M24, ADR-089).

**What is purged:** the raw bytes of every *present* snapshot whose content hash no brief
references. An evidence row is referenced (`cache-purge-v2`, M24-T7) when its id appears in any
of: `brief_evidence` (the retention anchor every brief pipeline writes,
`snapshot_retention.link`); the pilot and coding runs' items (`brief_case_evidence`, with their
detail) and the decay checks on them (`brief_evidence_decay.detail`); the codings and
adjudications (`brief_coding.evidence_ids`, `excerpts`, `detail`); the report facts of a case
(`brief_pilot_case.facts`, `evidence_stats`) and its gaps; the exploratory secondary outcomes
(`brief_secondary_outcome.record`, ADR-090); the selection (`brief_selection` and
`brief_selection_case` JSON); and the citations of every stored report or plan JSON in the
instance's private reports directory (`extra_refs`, `report_refs`). JSON columns are scanned for
evidence ids (`ev_` + 24 hex), so a nested citation is found wherever it sits.

Evidence rows are kept with `deletion_state = 'raw_dropped'` and their hash (snapshot or drop:
coded facts and hashes stay); one count-only tombstone per blob goes to `deletion_log` (reason
`purpose_limitation`, action `cache_purged`) and the run is recorded (`runs`,
`retention.cache_purge`). A dry run lists the counts and changes nothing.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pigtail.capture.db import CaptureDB
from pigtail.capture.snapshots import SnapshotStore

PURGE_VERSION = "cache-purge-v2"
EVIDENCE_ID_RE = re.compile(r"ev_[0-9a-f]{24}")
_ID_PG = "ev_[0-9a-f]{24}"

# (table, SQL expression giving the text scanned for evidence ids), M24-T7
JSON_REFERENCES: tuple[tuple[str, str], ...] = (
    ("brief_case_evidence", "detail::text"),
    ("brief_evidence_decay", "detail::text"),
    ("brief_coding", "excerpts::text || ' ' || detail::text"),
    ("brief_pilot_case", "coalesce(facts::text, '') || ' ' || evidence_stats::text"),
    ("brief_case_gap", "detail::text"),
    ("brief_secondary_outcome", "record::text"),
    ("brief_selection_case", "detail::text"),
    ("brief_selection", "summary::text || ' ' || views::text || ' ' || context::text"),
)

_REFERENCED = " UNION ".join(
    [
        "SELECT evidence_id AS id FROM brief_evidence",
        "SELECT evidence_id FROM brief_case_evidence",
        "SELECT unnest(evidence_ids) FROM brief_coding",
        *(
            f"SELECT m[1] FROM {t}, regexp_matches({expr}, '{_ID_PG}', 'g') AS m"
            for t, expr in JSON_REFERENCES
        ),
        "SELECT unnest(%(extra)s::text[])",
    ]
)

_UNREFERENCED = f"""
WITH refs AS ({_REFERENCED})
SELECT e.content_hash, array_agg(e.id ORDER BY e.id)
FROM evidence e
WHERE e.deletion_state = 'present'
GROUP BY e.content_hash
HAVING NOT bool_or(e.id IN (SELECT id FROM refs))
ORDER BY e.content_hash
"""


def referenced_ids(db: CaptureDB, extra_refs: Iterable[str] = ()) -> set[str]:
    """Every evidence id a brief references (module docstring)."""
    rows = db.conn.execute(
        f"SELECT DISTINCT id FROM ({_REFERENCED}) r WHERE id IS NOT NULL",
        {"extra": sorted(set(extra_refs))},
    ).fetchall()
    return {str(r[0]) for r in rows}


def report_refs(data_dir: Path | str | None) -> set[str]:
    """Evidence ids cited by every stored report or plan JSON under `<data_dir>/reports`
    (private, ADR-073.1). No directory: an empty set."""
    out: set[str] = set()
    if data_dir is None:
        return out
    root = Path(data_dir) / "reports"
    if not root.is_dir():
        return out
    for p in sorted(root.rglob("*.json")):
        try:
            out |= set(EVIDENCE_ID_RE.findall(p.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue
    return out


@dataclass
class PurgeResult:
    dry_run: bool
    blobs: int = 0
    evidence_rows: int = 0
    by_source: dict[str, int] = field(default_factory=dict)
    version: str = PURGE_VERSION

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def purge_unreferenced(
    db: CaptureDB,
    store: SnapshotStore,
    *,
    apply: bool,
    run_id: str | None = None,
    extra_refs: Iterable[str] = (),
) -> PurgeResult:
    """Drop the raw bytes of every unreferenced snapshot (module docstring); `apply=False`
    only counts. `extra_refs`: evidence ids cited outside the database (the stored reports'
    JSON, `report_refs`)."""
    from pigtail.privacy.deletion import DeletionLog

    res = PurgeResult(dry_run=not apply)
    rows = db.conn.execute(_UNREFERENCED, {"extra": sorted(set(extra_refs))}).fetchall()
    log = DeletionLog(db, "purpose_limitation", run_id=run_id, dry_run=not apply)
    for h, ids in rows:
        ids = list(ids)
        src = db.conn.execute(
            "SELECT source, count(*) FROM evidence WHERE id = ANY(%s) GROUP BY source", (ids,)
        ).fetchall()
        for s, n in src:
            res.by_source[str(s)] = res.by_source.get(str(s), 0) + int(n)
        res.blobs += 1
        res.evidence_rows += len(ids)
        if not apply:
            continue
        db.conn.execute(
            "UPDATE evidence SET deletion_state = 'raw_dropped' WHERE id = ANY(%s)"
            " AND deletion_state = 'present'",
            (ids,),
        )
        store.delete(str(h))
        log.write("cache_purged", "snapshot", rows=len(ids), content_hash=str(h))
    return res
