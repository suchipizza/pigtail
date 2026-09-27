"""ADR-085 addendum 4 (owner decision 2026-09-27: reuse the Product Hunt category scan across runs
and briefs): the gap rule, the cache rule in the pre-registered parameters, the versions and the
guard, Product Hunt's incomplete state, and the estimate (gaps only, the measured 230 requests an
hour, "cached listing reused for N of M days"). Unit tests, no database, no network; the Postgres
side is in tests/integration/test_m22_ph_cache_pg.py.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from pigtail.briefs import estimate as est
from pigtail.briefs import launch_sources as ls
from pigtail.briefs import outcomes as out
from pigtail.briefs import ph_cache
from pigtail.briefs.estimate import SelectionState, estimate, ph_topic_scan_pages, render_text
from pigtail.briefs.model import sha256_json
from pigtail.briefs.outcomes import incomplete_source, view_b_anchor
from pigtail.briefs.ph_cache import ScanRow, gaps, month_intervals, ph_key_hash
from pigtail.briefs.selection import ANCHOR_RULE_VERSION, SELECTION_VERSION, Context
from tests.unit.test_m22_round6 import W0, W1, cand, show_hn
from tests.unit.test_m22_round7 import d, ph_sig
from tests.unit.test_m22_views import B


def day(n: int) -> datetime:
    return datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=n)


# --- the gap rule ------------------------------------------------------------------------------
def test_gaps_are_the_parts_of_the_window_no_interval_covers():
    w = (day(0), day(100))
    assert gaps(w, []) == [w]
    assert gaps(w, [(day(-10), day(200))]) == []
    assert gaps(w, [(day(10), day(20)), (day(15), day(40)), (day(60), day(70))]) == [
        (day(0), day(10)), (day(40), day(60)), (day(70), day(100)),
    ]  # fmt: skip
    assert gaps(w, [(day(0), day(95))]) == [(day(95), day(100))]  # a window shifted by 5 days
    assert gaps(w, [(day(200), day(300))]) == [w]  # outside the window: no cover
    assert gaps(w, [(day(50), day(40))]) == [w]  # an empty interval covers nothing


def test_gaps_are_scanned_as_calendar_months_clipped_to_the_gap():
    a, b = datetime(2025, 1, 20, 6, tzinfo=UTC), datetime(2025, 4, 3, 12, tzinfo=UTC)
    m = [datetime(2025, k, 1, tzinfo=UTC) for k in (2, 3, 4)]
    assert month_intervals((a, b)) == [(a, m[0]), (m[0], m[1]), (m[1], m[2]), (m[2], b)]
    dec = datetime(2025, 12, 31, 23, 59, 59, tzinfo=UTC)
    jan = datetime(2026, 1, 1, tzinfo=UTC)
    assert month_intervals((dec, jan + timedelta(days=4))) == [
        (dec, jan), (jan, jan + timedelta(days=4)),
    ]  # fmt: skip
    assert month_intervals((jan, jan)) == []
    one = (datetime(2025, 5, 1, tzinfo=UTC), datetime(2025, 6, 1, tzinfo=UTC))
    assert month_intervals(one) == [one]


def test_names_are_stored_only_as_the_hash_of_their_slot_key():
    assert ph_key_hash(ls.ph_name_key("Cache Lint!")) == ph_key_hash(ls.ph_repo_key("o/cache-lint"))
    h = ph_key_hash("cachelint")
    assert len(h) == 64 and h == h.lower() and "cachelint" not in h


def test_a_scan_covers_its_interval_only_up_to_when_it_started():
    r = ScanRow(1, "open-source", day(0), day(100), "complete", None, 3, day(60), day(61), "x")
    assert r.covered == (day(0), day(60))
    assert gaps((day(0), day(100)), [r.covered]) == [(day(60), day(100))]
    done = ScanRow(2, "open-source", day(0), day(100), "complete", None, 3, day(120), day(121), "x")
    assert done.covered == (day(0), day(100))
    d0 = done.to_dict(reused=True)
    assert set(d0) == {"topic", "posted_after", "posted_before", "status", "pages", "started_at",
                       "finished_at", "reused"}  # fmt: skip


# --- parameters, versions, guard ---------------------------------------------------------------
def test_versions_and_the_cache_rule_in_the_params(launch_sources_on):
    assert SELECTION_VERSION == "selection-v11" and ANCHOR_RULE_VERSION == "anchor-v10"
    assert est.ESTIMATE_MODEL == "estimate-v9"
    p = Context.from_brief(B).params()
    ph = p["launch_sources"]["product_hunt"]
    tc = ph["topic_cache"]
    assert tc["max_age_days"] == ph_cache.PH_TOPIC_CACHE_MAX_AGE_DAYS == 14
    assert tc["rule"] == "ph-topic-cache-v1" and tc["gap_rule"] == ph_cache.GAP_RULE
    assert "started_at" in tc["gap_rule"] and "truncated and failed" in tc["gap_rule"]
    assert tc["index_rule"] == ph_cache.INDEX_RULE
    assert ph["incomplete_max_share"] == 0.10 and "topic_scan_failed" in ph["incomplete"]
    assert "createdAt" in ph["topic_scan"] and "tagline" not in ph["topic_scan"]


def test_the_guard_covers_the_cache_rule(monkeypatch):
    names = set(out.ANCHOR_RULE_FUNCTIONS) | set(out.ANCHOR_RULE_CONSTANTS)
    for n in ("gaps", "month_intervals", "ph_key_hash", "usable_scans", "start_scan",
              "save_page", "end_scan", "window_posts",
              "ScanRow", "PH_TOPIC_CACHE_RULE", "PH_TOPIC_CACHE_MAX_AGE_DAYS", "GAP_RULE",
              "INDEX_RULE"):  # fmt: skip
        assert f"pigtail.briefs.ph_cache:{n}" in names, n
    assert "pigtail.connectors.producthunt:TOPIC_FIELDS" in names
    assert "pigtail.briefs.launch_sources:PH_INCOMPLETE_MAX_SHARE" in names
    h0 = sha256_json(Context.from_brief(B).params())
    for mod, name, value in (
        (ph_cache, "PH_TOPIC_CACHE_MAX_AGE_DAYS", 30),
        (ph_cache, "gaps", lambda w, c: [w]),
        (ph_cache, "month_intervals", lambda g: [g]),
        (ls, "PH_INCOMPLETE_MAX_SHARE", 0.5),
    ):
        monkeypatch.setattr(mod, name, value)
        assert sha256_json(Context.from_brief(B).params()) != h0, name
        monkeypatch.undo()
    assert sha256_json(Context.from_brief(B).params()) == h0


# --- Product Hunt's incomplete state (verifier round 7 minor) ---------------------------------
def test_an_incomplete_product_hunt_signal_leaves_no_anchor():
    ok = [show_hn(9101, d(4))]
    done = cand("org-p/done", [*ok, ph_sig()])
    assert incomplete_source(done, ("product_hunt",)) is None
    failed = cand("org-p/failed", [*ok, ph_sig(status="incomplete")])
    assert incomplete_source(failed, ("product_hunt",)) == "product_hunt"
    a, why, rel = view_b_anchor(failed, W0, W1, required=("product_hunt",))
    assert a is None and why == "launch_source_incomplete:product_hunt" and rel == []
    # a signal without a status (written before anchor-v9) never counts as complete
    old = ph_sig()
    old.pop("status")
    assert incomplete_source(cand("org-p/old", [*ok, old]), ("product_hunt",)) == "product_hunt"


# --- the estimate --------------------------------------------------------------------------------
def test_the_estimate_counts_only_the_gaps_at_the_measured_rate(launch_sources_on):
    assert est.PH_REQUESTS_PER_HOUR == 230
    window_days = B.window.months * est.DAYS_PER_MONTH
    cache = {
        t: {"window_days": window_days, "reused_days": window_days - 5, "gaps_days": [5.0],
            "gap_pages_done": [0], "pages_per_day": 2.0}
        for t in ("open-source", "developer-tools")
    }  # fmt: skip
    pages, note = ph_topic_scan_pages(B, 2, cache)
    assert pages == 2 * 10
    assert note == (
        f"Product Hunt: cached listing reused for {int(window_days - 5)} of "
        f"{round(window_days)} days"
    )
    # pages already read by an unfinished scan of that gap are not counted again
    cache["open-source"]["gap_pages_done"] = [4]
    assert ph_topic_scan_pages(B, 2, cache)[0] == 16
    # a topic without a complete scan uses the planning density, per month interval (each with
    # its own cap, which a month doesn't reach)
    months = B.window.months
    cache["developer-tools"] |= {"pages_per_day": None,
                                 "gaps_days": [est.DAYS_PER_MONTH] * months,
                                 "gap_pages_done": [0] * months,
                                 "reused_days": 0.0}  # fmt: skip
    per_day = est.PH_TOPIC_POSTS_PER_MONTH / 20 / est.DAYS_PER_MONTH
    capped = months * min(ls.PH_TOPIC_MAX_PAGES, math.ceil(est.DAYS_PER_MONTH * per_day))
    assert math.ceil(est.DAYS_PER_MONTH * per_day) < ls.PH_TOPIC_MAX_PAGES  # no cap hit
    pages, note = ph_topic_scan_pages(B, 2, cache)
    assert pages == 6 + capped and "(developer-tools)" in note and "(open-source)" in note
    assert ph_topic_scan_pages(B, 2, cache, done=True)[0] == 0
    # through `estimate`: the requests, the hours at 230 an hour, the note in the text
    e = estimate(B, selection=SelectionState(pending=True, shortlisted=100, ph_cache=cache))
    s = e.selection
    assert s["producthunt_topic_pages"] == 6 + capped
    assert s["producthunt_requests"] == 2 * 100 + math.ceil(100 * 0.05) + 6 + capped
    assert s["producthunt_hours"] == pytest.approx(s["producthunt_requests"] / 230, abs=0.01)
    assert s["ph_cache_note"] == note and note in render_text(e, B)
    # a first run in an instance (no cache): the whole window, and 0 days reused
    first = estimate(B, selection=SelectionState(pending=True, shortlisted=100))
    assert first.selection["ph_cache_note"].startswith(
        "Product Hunt: cached listing reused for 0 of "
    )
    assert first.selection["producthunt_topic_pages"] == 2 * capped
    # the first run of an instance scans the whole window; a later one only its gaps
    warm = {t: {**c, "pages_per_day": 2.0, "gaps_days": [5.0], "gap_pages_done": [0]}
            for t, c in cache.items()}  # fmt: skip
    later = estimate(B, selection=SelectionState(pending=True, shortlisted=100, ph_cache=warm))
    assert later.selection["producthunt_topic_pages"] == 20
    assert first.selection["producthunt_hours"] - later.selection["producthunt_hours"] == (
        pytest.approx((2 * capped - 20) / 230, abs=0.01)
    )
