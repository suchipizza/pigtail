"""HN front-page rank connector, project-level only (M1-T14, R1.2, PRD §8.1 front-page minutes).

Why this connector is separate from `hn_firebase` and runs without the ADR-022 flag:

- **Rank history can't be backfilled.** Neither HN API exposes past ranks: Algolia has no rank
  field (source-matrix §2.3) and Firebase only serves the current `topstories`. Every day the
  poller is off is a day of front-page evidence (codebook §6.2 `hn_front_page`, outcome-model
  `att.hn_frontpage_minutes`) lost for good, so it must be able to run before the ADR-022
  person-level controls are complete.
- **It stores no person-level data.** It keeps story ids, ranks, urls, titles, scores, comment
  counts and times. The item's `by` field is dropped in `_parse()` (not even pseudonymized), story
  `text` and `kids` are not kept, and comments are never fetched. The `topstories` snapshot is an
  id list (`project_level`). An item's raw JSON does contain `by`, so item snapshots are taken
  (snapshot before parse), parsed, and their raw bytes are dropped at once by the poller
  (`pigtail.capture.hn_ranks`; hash, URL and fetch time are kept, `deletion_state = raw_dropped`).

Terms: TM-04 (CLEARED-WITH-CONDITIONS, commercial use pending LQ-6). API only; `topstories` at
most once a minute (enforced by the poller); items at 2 requests/s minus a 20 % margin.
Enabled by default; switch off with `PIGTAIL_CONNECTOR_HN_RANKS_ENABLED=false`.
`PIGTAIL_ENABLE_HN` does not affect it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import ClassVar

from pigtail.capture.snapshots import SnapshotMeta
from pigtail.connectors.base import Connector, Fetched, Record, TermsMetadata
from pigtail.connectors.hn import (
    FIREBASE_TERMS,
    item_url,
    list_url,
    normalize_github_repo,
    parse_id_list,
)

RANKS_TERMS = TermsMetadata(
    terms_url=FIREBASE_TERMS.terms_url,
    terms_basis=(
        FIREBASE_TERMS.terms_basis
        + " Rank poller (M1-T14): project-level fields only; `by` dropped at parse, item raw "
        "bytes dropped after parsing."
    ),
    clearance=FIREBASE_TERMS.clearance,
    commercial_use=FIREBASE_TERMS.commercial_use,
    deletion_obligation=FIREBASE_TERMS.deletion_obligation,
    notes=FIREBASE_TERMS.notes,
)

# Fields of an HN item that describe the story, not a person. Everything else is dropped.
PROJECT_FIELDS = ("type", "url", "title", "score", "descendants", "time", "deleted", "dead")


class HNRanksConnector(Connector):
    name: ClassVar[str] = "hn_ranks"
    version: ClassVar[str] = "0.1.0"
    terms: ClassVar[TermsMetadata] = RANKS_TERMS
    enabled_by_default: ClassVar[bool] = True
    person_level_hold: ClassVar[bool] = False  # project-level only (see module docstring)
    rate_per_second: ClassVar[float] = 2.0
    safety_margin: ClassVar[float] = 0.2
    retention_class = "project_level"
    reliability = "high"
    handle_fields: ClassVar[tuple[str, ...]] = ()
    handle_namespace: ClassVar[str] = "hn_ranks"  # no handles; not the "hn" namespace
    timeout_seconds: ClassVar[float] = 30.0

    def fetch_topstories(self) -> tuple[Fetched, list[int]]:
        f = self.fetch(list_url("topstories"), retention_class="project_level")
        return f, parse_id_list(f.data)

    def fetch_story(self, item_id: int) -> Fetched:
        """Item JSON, snapshotted as person-level (it contains `by`). The caller parses it with
        `story_record()` and then drops the raw bytes (`drop_after_parse`)."""
        return self.fetch(item_url(item_id), retention_class="person_level_24m")

    def story_record(self, f: Fetched) -> Record | None:
        """Project-level fields of a fetched item, or None for `null`."""
        recs = list(self.records(f.data, f.meta))
        return recs[0] if recs else None

    def _parse(self, data: bytes, meta: SnapshotMeta) -> Iterator[Record]:
        if meta.url.endswith("/topstories.json"):
            for rank, iid in enumerate(parse_id_list(data), start=1):
                yield {
                    "rank": rank,
                    "item_id": iid,
                    "evidence_type": "platform_metric",
                    "capture_mode": "api_json",
                }
            return
        item = json.loads(data)
        if not isinstance(item, dict) or "id" not in item:
            return
        rec: Record = {"item_id": int(item["id"])}
        for k in PROJECT_FIELDS:  # allow-list: `by`, `text`, `kids`, `parts` never pass
            rec[k] = item.get(k)
        rec["deleted"] = bool(rec["deleted"])
        rec["dead"] = bool(rec["dead"])
        url = rec["url"] if isinstance(rec["url"], str) else None
        rec["repo_full_name"] = normalize_github_repo(url)
        rec["evidence_type"] = "platform_metric"
        rec["capture_mode"] = "api_json"
        yield rec
