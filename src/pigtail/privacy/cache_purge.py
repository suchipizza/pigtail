"""Unreferenced-cache purge (PRD R19.10; Directive §5.2, ADR-063; purpose limitation, PRD §10):
once a brief's report is final, collected data that no brief references is purged, and the purge
is logged (M24, ADR-089).

**What is purged:** the raw bytes of every *present* snapshot whose content hash no brief run
references: no evidence row with that hash is linked in `brief_evidence` (the retention anchor
every brief pipeline writes, `snapshot_retention.link`) or in `brief_case_evidence` (the pilot
and coding runs' items). Evidence rows are kept with `deletion_state = 'raw_dropped'` and their
hash (snapshot or drop: coded facts and hashes stay); one count-only tombstone per blob goes to
`deletion_log` (reason `purpose_limitation`, action `cache_purged`) and the run is recorded
(`runs`, `retention.cache_purge`). A dry run lists the counts and changes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pigtail.capture.db import CaptureDB
from pigtail.capture.snapshots import SnapshotStore

PURGE_VERSION = "cache-purge-v1"

_UNREFERENCED = """
SELECT e.content_hash, array_agg(e.id ORDER BY e.id)
FROM evidence e
WHERE e.deletion_state = 'present'
GROUP BY e.content_hash
HAVING NOT bool_or(
    EXISTS (SELECT 1 FROM brief_evidence be WHERE be.evidence_id = e.id)
    OR EXISTS (SELECT 1 FROM brief_case_evidence ce WHERE ce.evidence_id = e.id)
)
ORDER BY e.content_hash
"""


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
    db: CaptureDB, store: SnapshotStore, *, apply: bool, run_id: str | None = None
) -> PurgeResult:
    """Drop the raw bytes of every unreferenced snapshot (module docstring); `apply=False`
    only counts."""
    from pigtail.privacy.deletion import DeletionLog

    res = PurgeResult(dry_run=not apply)
    rows = db.conn.execute(_UNREFERENCED).fetchall()
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
