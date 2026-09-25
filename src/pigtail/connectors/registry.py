"""Connector classes by source name (`evidence.source`), for replay and privacy scans (CB-08)."""

from __future__ import annotations

from pigtail.connectors.base import Connector
from pigtail.connectors.gharchive import GHArchiveConnector
from pigtail.connectors.hn import HNAlgoliaConnector, HNFirebaseConnector
from pigtail.connectors.hn_ranks import HNRanksConnector

CONNECTORS: dict[str, type[Connector]] = {
    c.name: c
    for c in (GHArchiveConnector, HNFirebaseConnector, HNAlgoliaConnector, HNRanksConnector)
}
