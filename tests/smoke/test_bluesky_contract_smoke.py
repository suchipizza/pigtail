"""ADR-085 addendum 2: live contract check of the Bluesky search as pigtail calls it, against a
known positive (the official `bsky.app` account has posted links to its own public repo
`bluesky-social/social-app`). Runs only with PIGTAIL_RUN_SMOKE=1; free, unauthenticated, two
requests; nothing is stored beyond a temporary snapshot directory. It guards the request shape
(the AppView answers 400 to `q=*` with `since`/`until`) that a fake cannot prove.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pigtail.capture.snapshots import LocalSnapshotStore
from pigtail.connectors.bluesky import (
    BlueskySearchConnector,
    bsky_evidence_url,
    parse_search_page,
)

KNOWN_HANDLE = "bsky.app"  # an organisation account, public, not a person
KNOWN_URL = "https://github.com/bluesky-social/social-app"


@pytest.mark.smoke
@pytest.mark.skipif(os.environ.get("PIGTAIL_RUN_SMOKE") != "1", reason="set PIGTAIL_RUN_SMOKE=1")
def test_bluesky_search_finds_a_known_post_live(tmp_path: Path) -> None:
    c = BlueskySearchConnector(store=LocalSnapshotStore(tmp_path / "snap"), env={})
    did = c.resolve_handle(KNOWN_HANDLE)
    assert did is not None and did.startswith("did:plc:")
    assert c.resolve_handle("nonexistent-handle-zz9.bsky.social") is None
    f = c.search_posts(
        author=did,
        url=KNOWN_URL,
        since=datetime(2020, 1, 1, tzinfo=UTC),
        until=datetime.now(UTC),
        cursor=None,
        evidence_url=bsky_evidence_url(c.base, "bluesky-social/social-app", "repo_url", 0),
    )
    hits, _cursor = parse_search_page(f.data)
    assert hits, "the known positive returned no post: the request shape or the API changed"
    assert all(h.at is not None for h in hits)
