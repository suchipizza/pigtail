"""Replay hook (PRD §5 replayability; used by M1-T10).

Re-parses a stored snapshot through the connector's parser - the exact code path used at ingest,
including pseudonymization (M1-T8) - and returns the records. M1-T10 asserts
`replay(...) == records stored at ingest`.

If the raw bytes were dropped under a retention policy (DPIA CB-04), replay re-downloads the
document from the URL in the snapshot metadata and verifies the SHA-256 before parsing; nothing is
re-stored. Otherwise replay never touches the network and does not need the connector enabled.
"""

from __future__ import annotations

from pigtail.capture.snapshots import SnapshotNotFound
from pigtail.connectors.base import Connector, Record


def replay(connector: Connector, content_hash: str, url: str | None = None) -> list[Record]:
    meta = connector.store.meta(content_hash)
    try:
        data = connector.store.get(content_hash)  # verifies the SHA-256
    except SnapshotNotFound:
        data = connector.refetch_verified(url or meta.url, content_hash)
    return list(connector.records(data, meta))
