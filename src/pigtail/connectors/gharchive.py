"""GH Archive hourly dumps connector (R1.1, R2.1/R2.2).

Source: `https://data.gharchive.org/YYYY-MM-DD-H.json.gz` (one gzipped JSON event per line, hour
without leading zero). The BigQuery path is a later option.

Each hourly dump is snapshotted whole (it contains person-level data: retention class
`person_level_24m`). `_parse()` streams the gzip line by line and yields one minimal record per
event: `{event_id, type, repo_id, repo_name, actor, created_at}`. The bot filter v0 (login-based
part) runs in `_pre_code()` on the raw login; everything downstream sees `actor=None` plus the
coded fields (`automated_account`, `bot_rule_version`, role and bucket; Directive §8.1).

Status since M11 (ADR-047.1, ADR-047.6, ADR-047.8): the global velocity scan that used this
connector was removed. The connector is kept, unused by any job, for brief-restricted discovery
signals in M13 (GH Archive restricted to a brief's repos/topics, discovery only; since mid-2025
the dumps are nearly push-events only, so stars and forks come from the GitHub API).
"""

from __future__ import annotations

import gzip
import io
import json
from collections.abc import Iterator
from datetime import datetime
from typing import Any, ClassVar

from pigtail.capture.botfilter import is_bot_login
from pigtail.capture.snapshots import SnapshotMeta
from pigtail.connectors.base import Clearance, Connector, Record, TermsMetadata

BASE_URL = "https://data.gharchive.org"


def hour_url(hour: datetime) -> str:
    return f"{BASE_URL}/{hour:%Y-%m-%d}-{hour.hour}.json.gz"


class GHArchiveConnector(Connector):
    name: ClassVar[str] = "gharchive"
    version: ClassVar[str] = "0.1.0"
    terms: ClassVar[TermsMetadata] = TermsMetadata(
        terms_url="https://www.gharchive.org/",
        terms_basis=(
            "TM-01 (docs/compliance/terms-memos.md): GH Archive data has no stated licence; "
            "GitHub ToS and Acceptable Use Policies §7 apply (research use of public "
            "information; comply with the GitHub Privacy Statement). Conditions: discard "
            "actors at ingest (roles and buckets only), never sell or export personal "
            "information, aggregate-only open public outputs, cross-check star counts against "
            "the GitHub API."
        ),
        clearance=Clearance.CLEARED_WITH_CONDITIONS,
        commercial_use=None,  # unknown: open question Q1 in TM-01
        deletion_obligation=None,  # unknown: none stated
        notes="WatchEvent capture is known to be incomplete (gharchive.org issues 310/320).",
    )
    enabled_by_default: ClassVar[bool] = True
    rate_per_second: ClassVar[float] = 1.0
    retention_class = "person_level_24m"
    reliability = "high"
    handle_fields: ClassVar[tuple[str, ...]] = ("actor",)
    handle_namespace: ClassVar[str] = "github"
    repo_fields: ClassVar[tuple[str, ...]] = ("repo_id",)
    repo_host: ClassVar[str] = "github"
    timeout_seconds: ClassVar[float] = 600.0

    def _parse(self, data: bytes, meta: SnapshotMeta) -> Iterator[Record]:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
            for line in gz:
                if not line.strip():
                    continue
                try:
                    ev: dict[str, Any] = json.loads(line)
                    repo = ev["repo"]
                    rec: Record = {
                        "event_id": str(ev.get("id")),
                        "type": ev["type"],
                        "repo_id": int(repo["id"]),
                        "repo_name": repo["name"],
                        "actor": (ev.get("actor") or {}).get("login"),
                        "created_at": ev.get("created_at"),
                    }
                except (ValueError, KeyError, TypeError):
                    continue  # malformed line: skip (counted by the caller via totals)
                yield rec

    def _pre_code(self, record: Record) -> Record | None:
        login = record.get("actor")
        bot = login is None or is_bot_login(login)
        record["automated_account"] = bot
        if bot:
            record["actor"] = None  # bots are not persons; don't keep or hash their login
        return record
