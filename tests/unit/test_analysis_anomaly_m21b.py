"""Aggregate anomaly checks on daily star counts (ADR-070.4, PRD R3.3; M21b), with synthetic
series only: spikes with and without matching forks, issues, downloads or mentions; unknown
days; stars-to-activity ratios against a population and the absolute fallback; the versioned
parameters (`schemas/analysis-params/v1.1.0.json`, v1.0.0 kept).
"""

from __future__ import annotations

import dataclasses
import json
from datetime import date, timedelta
from pathlib import Path

from pigtail.analysis import params
from pigtail.analysis.anomaly import (
    STAR_LABEL,
    check_population,
    check_repo,
    find_spikes,
    ratio_flags,
)
from pigtail.analysis.params import ANOMALY, PARAMS_VERSION

ROOT = Path(__file__).resolve().parents[2]
D0 = date(2026, 1, 1)
DAYS = 60
SPIKE = D0 + timedelta(days=40)


def flat(v: float, n: int = DAYS, start: date = D0) -> dict[date, float]:
    return {start + timedelta(days=i): v for i in range(n)}


def with_spike(base: dict[date, float], day: date, value: float, length: int = 1) -> dict:
    out = dict(base)
    for i in range(length):
        out[day + timedelta(days=i)] = value
    return out


def activity(base_forks: float = 1, base_issues: float = 1, **bumps: float) -> dict[str, dict]:
    """Daily forks and issues; `bumps` add `channel=value` on the spike day."""
    a = {"forks": flat(base_forks), "issues": flat(base_issues)}
    for ch, v in bumps.items():
        a.setdefault(ch, flat(0))
        a[ch] = with_spike(a[ch], SPIKE, v)
    return a


# --- parameters ---------------------------------------------------------------------------------
def test_adr_070_4_params_v1_1_0_mirror_json_and_v1_0_0_is_kept():
    doc = json.loads((ROOT / "schemas" / "analysis-params" / "v1.1.0.json").read_text())
    assert PARAMS_VERSION == doc["version"] == "1.1.0"
    anomaly = json.loads(json.dumps(dataclasses.asdict(params.ANOMALY), default=list))
    assert doc["anomaly_check"] == anomaly
    assert doc["anomaly_check"]["label"] == "unfiltered, anomaly-checked"
    assert doc["fake_star_filter"]["status"] == "retired"  # StarScout retired (ADR-070.4)
    old = json.loads((ROOT / "schemas" / "analysis-params" / "v1.0.0.json").read_text())
    assert old["version"] == "1.0.0" and "anomaly_check" not in old  # kept unchanged
    assert old["burst"] == doc["burst"]  # the burst rule did not change


# --- check 1: spike without matching activity ---------------------------------------------------
def test_r3_3_spike_with_no_matching_activity_is_flagged():
    stars = with_spike(flat(5), SPIKE, 400)
    rep = check_repo("synthetic/a", stars, activity())
    assert [s.start for s in rep.spikes] == [SPIKE]
    assert rep.status == "checked" and rep.flagged
    (flag,) = rep.flags
    assert flag.kind == "spike_no_activity" and flag.start == SPIKE
    assert flag.detail["channels_checked"] == ["forks", "issues"]
    d = rep.to_dict()
    assert d["label"] == STAR_LABEL == "unfiltered, anomaly-checked"
    assert d["params_version"] == "1.1.0" and d["rule_version"] == "anomaly-v0"


def test_r3_3_spike_matched_by_forks_issues_downloads_or_mentions_is_not_flagged():
    stars = with_spike(flat(5), SPIKE, 400)
    for bump in ({"forks": 25}, {"issues": 12}, {"downloads": 900}, {"mentions": 2}):
        rep = check_repo("synthetic/b", stars, activity(**bump))
        assert rep.status == "checked" and not rep.flagged, bump
        assert rep.spikes[0].channels_matching == (next(iter(bump)),)


def test_r3_3_activity_the_day_after_the_spike_still_matches():
    stars = with_spike(flat(5), SPIKE, 400)
    a = activity()
    a["forks"] = with_spike(a["forks"], SPIKE + timedelta(days=2), 30)  # within +3 days
    assert not check_repo("synthetic/c", stars, a).flagged
    a = activity()
    a["forks"] = with_spike(a["forks"], SPIKE + timedelta(days=6), 30)  # outside the window
    assert check_repo("synthetic/c", stars, a).flagged


def test_r3_3_tiny_lift_is_not_a_match():
    stars = with_spike(flat(5), SPIKE, 400)
    a = activity(base_forks=10)  # busy repo: 10 forks a day
    a["forks"] = with_spike(a["forks"], SPIKE, 14)  # +4 on the day: lift far below 2x
    rep = check_repo("synthetic/d", stars, a)
    assert rep.flagged and rep.spikes[0].channels_matching == ()


def test_r3_3_unknown_activity_means_unchecked_never_flagged():
    stars = with_spike(flat(5), SPIKE, 400)
    rep = check_repo("synthetic/e", stars, {})
    assert rep.status == "unchecked" and not rep.flagged
    gap = activity()
    for i in range(-1, 4):  # no known fork or issue day in the spike window
        for ch in ("forks", "issues"):
            gap[ch].pop(SPIKE + timedelta(days=i))
    assert check_repo("synthetic/e", stars, gap).status == "unchecked"


def test_r3_3_consecutive_spike_days_are_one_spike_and_small_or_gradual_growth_is_none():
    stars = with_spike(flat(5), SPIKE, 300, length=3)
    (spike,) = find_spikes(stars)
    assert (spike.start, spike.end, spike.stars) == (SPIKE, SPIKE + timedelta(days=2), 900)
    assert find_spikes(with_spike(flat(5), SPIKE, 40)) == []  # below min_spike_stars
    ramp = {D0 + timedelta(days=i): float(i * 3) for i in range(DAYS)}  # steady growth
    assert find_spikes(ramp) == []
    # a spike without enough baseline days is not judged
    assert find_spikes(with_spike(flat(5, n=10), D0 + timedelta(days=5), 500)) == []
    assert check_repo("synthetic/f", flat(5), activity()).status == "no_spikes"


# --- check 2: stars-to-activity ratio -------------------------------------------------------------
def test_r3_3_ratio_outlier_against_the_population():
    series = {}
    for i in range(9):  # normal repos: ~5 stars per fork+issue
        series[f"synthetic/n{i}"] = (flat(10 + i), activity())
    series["synthetic/odd"] = (flat(40), {"forks": flat(0), "issues": flat(0.02)})
    reps = check_population(series)
    assert [f.kind for f in reps["synthetic/odd"].flags] == ["ratio_outlier"]
    assert reps["synthetic/odd"].flags[0].detail["basis"] == "population"
    assert not any(reps[f"synthetic/n{i}"].flagged for i in range(9))


def test_r3_3_ratio_absolute_fallback_small_population_and_small_repos_skipped():
    odd = check_repo("synthetic/x", flat(40), {"forks": flat(0), "issues": flat(0)})
    ok = check_repo("synthetic/y", flat(10), activity())
    tiny = check_repo("synthetic/z", flat(1), {"forks": flat(0), "issues": flat(0)})  # 60 stars
    got = ratio_flags({"x": odd, "y": ok, "z": tiny})
    assert set(got) == {"x"} and got["x"].detail["basis"] == "absolute"
    assert got["x"].detail["max"] == ANOMALY.ratio_max_stars_per_activity
    none = check_repo("synthetic/w", flat(40), {"forks": flat(0)})  # no issues channel
    assert none.ratio is None and ratio_flags({"w": none}) == {}
