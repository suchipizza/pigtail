"""Raw-snapshot retention (DPIA control CB-04; PRD §10).

Raw GH Archive hourly dumps contain person-level data (logins, avatar URLs, comment text, possibly
commit e-mails). The scan needs them only until they are parsed into pseudonymized aggregates, so
raw bytes are kept for `GHARCHIVE_RAW_RETENTION_DAYS` (default 30) and then dropped. The
`evidence` record (source, url, content_hash, fetched_at) and the snapshot metadata sidecar stay;
the evidence moves to `deletion_state = 'raw_dropped'`. Replay re-downloads by URL and verifies
the hash (`pigtail.capture.replay`).

A content hash is dropped only when *every* evidence record pointing at it is past the cutoff.
With a `DeletionLog` each drop also writes a tombstone and `dry_run` is honoured
(`pigtail retention purge`, CB-01).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pigtail.capture.db import CaptureDB
from pigtail.capture.snapshots import SnapshotStore
from pigtail.privacy.deletion import DeletionLog, drop_raw


def purge_raw(
    db: CaptureDB,
    store: SnapshotStore,
    *,
    source: str,
    retention_days: int,
    now: datetime | None = None,
    log: DeletionLog | None = None,
) -> int:
    """Drop raw bytes older than the retention for `source`; return the number of hashes purged."""
    if retention_days < 0:
        raise ValueError("retention_days must be >= 0")
    cutoff = (now or datetime.now(UTC)) - timedelta(days=retention_days)
    rows = db.conn.execute(
        """
        SELECT content_hash FROM evidence
        GROUP BY content_hash
        HAVING bool_and(source = %s) AND max(fetched_at) <= %s
           AND bool_or(deletion_state = 'present')
        """,
        (source, cutoff),
    ).fetchall()
    purged = 0
    for (h,) in rows:
        if log is not None:
            drop_raw(db, store, h, log)
            purged += 1
            continue
        store.delete(h)
        db.conn.execute(
            "UPDATE evidence SET deletion_state = 'raw_dropped' "
            "WHERE content_hash = %s AND deletion_state = 'present'",
            (h,),
        )
        purged += 1
    return purged
