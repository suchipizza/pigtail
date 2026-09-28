"""ADR-085 addendum 5 (verifier M22 round 8), unit side: versions and the guard, the frozen topic
hits and the missing-row rule in the pre-registered parameters, Bluesky's paging rule, the
resolveHandle 400 rule, the retention period and the estimate's lower bound. No database, no
network; the Postgres side is in tests/integration/test_m22_addendum5_pg.py.
"""

from __future__ import annotations

import json

from pigtail.briefs import estimate as est
from pigtail.briefs import launch_sources as ls
from pigtail.briefs import outcomes as out
from pigtail.briefs import ph_cache
from pigtail.briefs.estimate import (
    SelectionState,
    estimate,
    ph_topic_scan_lower_bound,
    render_text,
)
from pigtail.briefs.model import sha256_json
from pigtail.briefs.selection import (
    ANCHOR_RULE_SOURCE_SHA256,
    ANCHOR_RULE_VERSION,
    PRE_WINDOW_RULE,
    SELECTION_VERSION,
    Context,
)
from pigtail.connectors import bluesky
from pigtail.connectors.bluesky import unresolvable_handle
from pigtail.privacy.retention import RetentionConfig
from tests.unit.test_m22_views import B


def test_versions_guard_and_the_cache_label(launch_sources_on):
    assert SELECTION_VERSION == "selection-v12" and ANCHOR_RULE_VERSION == "anchor-v11"
    assert est.ESTIMATE_MODEL == "estimate-v11"
    assert out.anchor_rule_source_sha256() == ANCHOR_RULE_SOURCE_SHA256
    # what the cache stores is unchanged: the listing cached under v10 stays reusable
    assert ph_cache.PH_TOPIC_CACHE_RULE == "ph-topic-cache-v1"
    names = set(out.ANCHOR_RULE_FUNCTIONS) | set(out.ANCHOR_RULE_CONSTANTS)
    for n in ("pigtail.connectors.bluesky:unresolvable_handle",
              "pigtail.connectors.bluesky:UNRESOLVABLE_ERROR",
              "pigtail.connectors.bluesky:UNRESOLVABLE_MESSAGE",
              "pigtail.briefs.launch_sources:PH_HITS_FROZEN",
              "pigtail.briefs.launch_sources:run_product_hunt",
              "pigtail.briefs.launch_sources:run_bluesky"):  # fmt: skip
        assert n in names, n


def test_the_params_state_the_frozen_hits_and_the_missing_row_rule(launch_sources_on, monkeypatch):
    p = Context.from_brief(B).params()
    ph = p["launch_sources"]["product_hunt"]
    assert "frozen in the run's checkpoint" in ph["topic_hits"]
    assert "topic_plan_rows_missing" in ph["incomplete"]
    idx = ph["topic_cache"]["index_rule"]
    assert "frozen" in idx and "failed:plan_rows_missing" in idx
    search = p["launch_sources"]["bluesky"]["search"]
    assert "until the list ends" in search and "launch-worded post before the window is" not in (
        search
    )
    assert "earliest launch-worded post is found" in PRE_WINDOW_RULE
    h0 = sha256_json(Context.from_brief(B).params())
    monkeypatch.setattr(bluesky, "UNRESOLVABLE_MESSAGE", "SYNTH other message")
    assert sha256_json(Context.from_brief(B).params()) != h0


def test_only_unable_to_resolve_handle_is_unresolvable():
    ok = {"error": "InvalidRequest", "message": "Unable to resolve handle"}
    assert unresolvable_handle(json.dumps(ok).encode())
    for body in (
        {"error": "InvalidRequest"},  # no message
        {"error": "InvalidRequest", "message": "Error: handle must be a valid handle"},
        {"error": "RateLimitExceeded", "message": "Unable to resolve handle"},
        {"error": "InvalidRequest", "message": None},
        ["InvalidRequest", "Unable to resolve handle"],
    ):
        assert not unresolvable_handle(json.dumps(body).encode()), body
    assert not unresolvable_handle(b"<html>Bad Request</html>")


def test_retention_is_30_days():
    assert ph_cache.PH_TOPIC_CACHE_RETENTION_DAYS == 30
    assert RetentionConfig().ph_topic_cache_days == 30
    assert ph_cache.PH_TOPIC_CACHE_MAX_AGE_DAYS == 14 < 30


def test_hours_on_the_planning_density_are_a_lower_bound(launch_sources_on):
    assert ph_topic_scan_lower_bound(None)
    assert not ph_topic_scan_lower_bound(None, done=True)
    measured = {"open-source": {"gaps_days": [3.0], "pages_per_day": 2.0}}
    assert not ph_topic_scan_lower_bound(measured)
    unmeasured = {**measured, "developer-tools": {"gaps_days": [30.0], "pages_per_day": None}}
    assert ph_topic_scan_lower_bound(unmeasured)
    # nothing to scan: no lower bound, whatever the density
    assert not ph_topic_scan_lower_bound({"open-source": {"gaps_days": [], "pages_per_day": None}})
    # a first run in an instance: "at least", with the reason
    first = estimate(B, selection=SelectionState(pending=True, shortlisted=100))
    assert first.selection["producthunt_hours_lower_bound"] is True
    text = render_text(first, B)
    assert "Product Hunt at least" in text and "unfinished 6-hour run" in text
    assert "ten hours" not in text
    # a measured density: "about"
    wd = B.window.months * est.DAYS_PER_MONTH
    warm = {
        t: {"window_days": wd, "reused_days": wd - 5, "gaps_days": [5.0], "gap_pages_done": [0],
            "pages_per_day": 2.0}
        for t in ("open-source", "developer-tools")
    }  # fmt: skip
    later = estimate(B, selection=SelectionState(pending=True, shortlisted=100, ph_cache=warm))
    assert later.selection["producthunt_hours_lower_bound"] is False
    assert "Product Hunt about" in render_text(later, B)
    assert ls.PH_HITS_FROZEN == "ph_topic_hits_frozen"
