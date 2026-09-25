"""Connector classes by source name (`evidence.source`), for replay and privacy scans (CB-08)."""

from __future__ import annotations

from pigtail.connectors.base import Connector
from pigtail.connectors.gharchive import GHArchiveConnector

CONNECTORS: dict[str, type[Connector]] = {
    GHArchiveConnector.name: GHArchiveConnector,
}
