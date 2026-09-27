"""ADR-085: live contract check of the Product Hunt collector as pigtail calls it, against a known
positive (Supabase's public launch, slug `supabase`, featured on 2020-05-27). Runs only with
PIGTAIL_RUN_SMOKE=1 and PH_API_TOKEN set (the operator's own developer token); one request,
project-level fields only; nothing is stored beyond a temporary snapshot directory.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pigtail.capture.snapshots import LocalSnapshotStore
from pigtail.connectors.producthunt import ProductHuntConnector, parse_post

# read at import: the autouse conftest fixture removes PH_API_TOKEN from each test's environment
TOKEN = os.environ.get("PH_API_TOKEN") or None


@pytest.mark.smoke
@pytest.mark.skipif(os.environ.get("PIGTAIL_RUN_SMOKE") != "1", reason="set PIGTAIL_RUN_SMOKE=1")
@pytest.mark.skipif(TOKEN is None, reason="PH_API_TOKEN not set")
def test_product_hunt_slug_lookup_finds_a_known_launch_live(tmp_path: Path) -> None:
    c = ProductHuntConnector(store=LocalSnapshotStore(tmp_path / "snap"), token=TOKEN)
    assert c.has_token
    f = c.post_by_slug("supabase", evidence_url="https://api.producthunt.com/v2/api/graphql#smoke")
    post = parse_post(f.data)
    assert post is not None, "the known launch was not found: the query or the API changed"
    at = post.event_at
    assert at is not None and at.year == 2020
    assert c.rate_limit is None or c.rate_limit > 0
