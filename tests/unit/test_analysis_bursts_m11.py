"""M11: per-repo burst/quiet segmentation on star-history days (codebook v0.3.1 §3.2-3.3, outcome
model v2 §2.1) and the versioned analysis parameters (schemas/analysis-params/v1.0.0.json).
Synthetic series only."""

from __future__ import annotations

import dataclasses
import json
import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from pigtail.analysis import params
from pigtail.analysis.bursts import (
    calm_threshold,
    daily_baseline,
    day_start,
    evaluate_day,
    onset,
    segment,
)
from pigtail.analysis.params import BURST, PARAMS_VERSION

ROOT = Path(__file__).resolve().parents[2]
D0 = date(2026, 6, 1)


def days(start: date, values: list[int]) -> dict[date, int]:
    return {start + timedelta(days=i): v for i, v in enumerate(values)}


def flat(n: int, v: int = 5, start: date = D0) -> dict[date, int]:
    return days(start, [v] * n)


# --- parameters ---------------------------------------------------------------------------------
def test_m11_params_python_mirror_equals_versioned_json():
    doc = json.loads((ROOT / "schemas" / "analysis-params" / f"v{PARAMS_VERSION}.json").read_text())
    assert doc["id"] == params.PARAMS_ID and doc["version"] == PARAMS_VERSION
    assert doc["star_history_day_tz"] == params.STAR_HISTORY_DAY_TZ
    burst = {k: v for k, v in doc["burst"].items() if k != "series"}
    assert burst == dataclasses.asdict(params.BURST)
    assert doc["fake_star_filter"] == dataclasses.asdict(params.FAKE_STAR_FILTER)
    # the values moved from the retired thresholds JSON are unchanged
    old = json.loads((ROOT / "schemas" / "outcome-thresholds" / "v0.2.0.json").read_text())
    retired = {
        k: v for k, v in doc["fake_star_filter"].items() if k not in ("status", "retired_by")
    }
    assert old["fake_star_filter"] == retired
    assert doc["fake_star_filter"]["status"] == "retired"  # ADR-070.4 (v1.1.0)
    ob = old["burst_detection"]
    assert (ob["id"], ob["min_net_stars_48h"], ob["sigma"], ob["baseline_days"]) == (
        BURST.rule_version,
        BURST.min_net_stars_48h,
        BURST.sigma,
        BURST.baseline_days,
    )


# --- baseline (velocity-v0 statistics, unchanged from detection v1) ----------------------------
def test_m11_daily_baseline_matches_v0_statistics():
    start = date(2026, 8, 24)
    b = daily_baseline(flat(30, 5, start), start)
    assert b.quality == "full" and b.days_covered == 30 and b.mu_d == 5
    assert b.mean_48h == 10 and b.std_48h == 0 and b.sigma_used == pytest.approx(10**0.5)
    none = daily_baseline({}, start)
    assert none.quality == "none" and none.sigma_used == BURST.min_sigma
    short = days(start + timedelta(days=25), [20] * 5)
    p = daily_baseline(short, start)
    assert p.quality == "partial" and p.days_covered == 5 and p.std_48h == 0  # < 3 blocks
    created = daily_baseline(flat(30, 5, start), start, not_before=start + timedelta(days=20))
    assert created.days_covered == 10
    neg = daily_baseline({start: -40, start + timedelta(days=1): 0}, start)
    assert neg.mean_48h == 0 and neg.mu_d == 0  # net days can be negative; the mean is floored


def test_m11_thresholds():
    assert calm_threshold(0, 3) == 1  # mu + 1 when mu = 0
    assert calm_threshold(4, 3) == 10


# --- detection and onset ------------------------------------------------------------------------
def burst_series() -> dict[date, int]:
    """40 days at ~5/day, then 150, 160, 40, then calm again for 30 days."""
    base = [3, 5, 7, 4, 6, 5, 5]
    vals = [base[i % 7] for i in range(40)] + [150, 160, 40] + [base[i % 7] for i in range(30)]
    return days(D0, vals)


def test_m11_velocity_v0_fires_on_48h_sum_and_z():
    s = burst_series()
    d = D0 + timedelta(days=41)  # n(d-1) + n(d) = 150 + 160
    f = evaluate_day(s, d)
    assert f is not None and f.stars_48h == 310 and f.z > 50
    assert f.baseline.first_day == D0 + timedelta(days=10) and f.baseline.last_day == D0 + (
        timedelta(days=39)
    )  # the 30 endpoint days before d-1
    assert evaluate_day(s, D0 + timedelta(days=20)) is None  # flat
    assert evaluate_day({**s, d - timedelta(days=1): 60}, d) is not None  # 220 >= 100
    small = {**s, d - timedelta(days=1): 40, d: 50}  # 90 < 100 net stars
    assert evaluate_day(small, d) is None
    gap = dict(s)
    del gap[d - timedelta(days=1)]
    assert evaluate_day(gap, d) is None  # unknown day: no firing, never a zero


def test_m11_onset_day_precision():
    s = burst_series()
    f = evaluate_day(s, D0 + timedelta(days=40))  # 5 + 150 already fires
    assert f is not None
    o = onset(s, f)
    assert o.day == D0 + timedelta(days=40) and o.precision == "day"  # d-1 (5) is calm
    assert o.at == day_start(o.day) == datetime(2026, 7, 11, 7, tzinfo=UTC)  # US Pacific, PDT


def test_m11_onset_hour_refined_from_hourly_snapshots():
    s = burst_series()
    f = evaluate_day(s, D0 + timedelta(days=41))
    assert f is not None
    start = day_start(f.day - timedelta(days=1))
    hourly = {start + timedelta(hours=k): 0 for k in range(48)}
    hourly[start + timedelta(hours=9)] = 1  # below mu_h + 3*sqrt(mu_h)
    hourly[start + timedelta(hours=14)] = 40
    o = onset(s, f, hourly=hourly)
    mu_h = f.baseline.mu_d / 24
    assert 1 <= calm_threshold(mu_h, 3) < 40
    assert o.precision == "hour" and o.at == start + timedelta(hours=14)
    diffuse = {h: 0 for h in hourly}
    assert onset(s, f, hourly=diffuse).at == start  # no single hour qualifies: window start
    partial = dict(hourly)
    del partial[start + timedelta(hours=3)]
    assert onset(s, f, hourly=partial).precision == "day"  # hourly must cover the window


# --- segmentation -------------------------------------------------------------------------------
def test_m11_segment_end_quiet_and_shape():
    s = burst_series()
    seg = segment(s, D0 + timedelta(days=31), D0 + timedelta(days=72))
    assert seg.rule_version == "velocity-v0" and seg.params_version == PARAMS_VERSION
    (b,) = seg.bursts
    assert b.onset.day == D0 + timedelta(days=40)
    assert b.end == D0 + timedelta(days=43)  # first of 3 calm days
    assert b.last_day == D0 + timedelta(days=42) and not b.multi_peak
    assert b.peak_day == D0 + timedelta(days=41) and b.peak_stars == 160
    assert b.shape == "sudden_fast_decay"  # peak at index 1, below half within 7 days
    phases = [(q.phase, (q.last_day - q.first_day).days + 1) for q in seg.quiet]
    assert phases == [("pre_first_burst", 9), ("post_last_burst", 30)]


def test_m11_bursts_less_than_7_days_apart_merge_and_unknown_days_split_quiet():
    base = [5] * 40
    vals = base + [150, 160, 5, 5, 5, 5, 200, 180, 5, 5, 5] + [5] * 20
    s = days(D0, vals)
    seg = segment(s, D0 + timedelta(days=31), D0 + timedelta(days=len(vals) - 1))
    (b,) = seg.bursts
    assert b.multi_peak and b.onset.day == D0 + timedelta(days=40)
    assert b.peak_stars == 200 and b.shape == "mixed"  # peak at index 6
    # an unknown day inside the calm tail splits the quiet interval
    gap = dict(s)
    del gap[D0 + timedelta(days=60)]
    q = segment(gap, D0 + timedelta(days=31), D0 + timedelta(days=len(vals) - 1)).quiet
    assert [(x.first_day, x.last_day) for x in q if x.phase == "post_last_burst"] == [
        (D0 + timedelta(days=48), D0 + timedelta(days=59)),
        (D0 + timedelta(days=61), D0 + timedelta(days=len(vals) - 1)),
    ]


def test_m11_gradual_build_open_burst_and_creation():
    created = D0 + timedelta(days=10)
    ramp = [0] * 10 + [2] * 30 + [60, 70, 80, 90, 100, 120, 150, 180, 220, 300, 400]
    s = days(D0, ramp)
    seg = segment(s, D0, D0 + timedelta(days=len(ramp) - 1), created=created)
    (b,) = seg.bursts
    assert b.end is None and b.last_day == D0 + timedelta(days=len(ramp) - 1)  # still running
    assert b.shape == "gradual_build"
    # days before creation are neither burst nor quiet
    assert all(q.first_day >= created for q in seg.quiet)


def test_m11_no_burst_is_all_pre_first_burst():
    seg = segment(flat(60), D0 + timedelta(days=31), D0 + timedelta(days=59))
    assert seg.bursts == () and [q.phase for q in seg.quiet] == ["pre_first_burst"]


@pytest.mark.parametrize("n", [0, 1, 3])
def test_m11_baseline_none_uses_absolute_threshold_only(n: int):
    s: dict[date, Any] = days(D0 + timedelta(days=30 - n), [0] * n)
    s[D0 + timedelta(days=30)] = 50
    s[D0 + timedelta(days=31)] = 60
    f = evaluate_day(s, D0 + timedelta(days=31))
    assert f is not None and f.stars_48h == 110
    assert f.baseline.quality == ("none" if n == 0 else "partial")
    assert math.isfinite(f.z)


# --- ADR-052: no phantom re-firing after a single-day spike -------------------------------------
def test_adr052_single_day_spike_is_one_burst_not_multi_peak():
    """A single-day spike: day 61's 48-hour window (days 60-61) starts on the burst's last day, so
    its firing is ignored (neither a new burst nor a merge)."""
    vals = [2] * 90
    vals[60] = 300
    s = days(D0, vals)
    seg = segment(s, D0 + timedelta(days=31), D0 + timedelta(days=89))
    (b,) = seg.bursts
    assert b.onset.day == D0 + timedelta(days=60)
    assert b.end == D0 + timedelta(days=61) and b.last_day == D0 + timedelta(days=60)
    assert not b.multi_peak
    assert evaluate_day(s, D0 + timedelta(days=61)) is not None  # it does fire; it is ignored


def test_adr052_genuine_second_peak_still_merges_as_multi_peak():
    """Two separate spikes 4 days apart: the second one's window starts after the first burst's
    last day, so it is a new firing and merges (gap < 7 days) with multi_peak True."""
    vals = [2] * 90
    vals[60] = 300
    vals[65] = 300
    s = days(D0, vals)
    seg = segment(s, D0 + timedelta(days=31), D0 + timedelta(days=89))
    (b,) = seg.bursts
    assert b.multi_peak and b.onset.day == D0 + timedelta(days=60)
    assert b.last_day == D0 + timedelta(days=65) and b.end == D0 + timedelta(days=66)
