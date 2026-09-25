"""M1-T3 / R1.1: bot filter v0, aggregation and detection maths (no database)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pigtail.capture.botfilter import is_bot_login
from pigtail.capture.snapshots import LocalSnapshotStore, SnapshotMeta
from pigtail.capture.velocity import (
    Candidate,
    VelocityConfig,
    WindowAggregator,
    baseline_stats,
    day_chunks,
    evaluate,
    floor_hour,
    hours_between,
)
from pigtail.connectors.gharchive import GHArchiveConnector, hour_url

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "gharchive"
T0 = datetime(2026, 9, 20, tzinfo=UTC)
H = timedelta(hours=1)


def test_hour_url_has_no_leading_zero():
    assert hour_url(T0 + 5 * H) == "https://data.gharchive.org/2026-09-20-5.json.gz"
    assert hour_url(T0 + 15 * H) == "https://data.gharchive.org/2026-09-20-15.json.gz"


@pytest.mark.parametrize(
    ("login", "bot"),
    [
        ("dependabot[bot]", True),
        ("helper-app[bot]", True),
        ("ci-bot", True),
        ("build_bot", True),
        ("bot-runner", True),
        ("renovate", True),
        ("github-actions", True),
        ("user0001", False),
        ("abbot", False),
        ("robotics-fan", False),
        ("botanist", False),
    ],
)
def test_r1_1_bot_filter_v0_login_rules(login: str, bot: bool):
    assert is_bot_login(login) is bot


def gh(tmp_path: Path, pz) -> GHArchiveConnector:
    return GHArchiveConnector(store=LocalSnapshotStore(tmp_path), pseudonymizer=pz, env={})


def meta() -> SnapshotMeta:
    return SnapshotMeta("gharchive", "fixture", T0, "gharchive/0.1.0", "synthetic")


def aggregate(tmp_path: Path, pz, cfg: VelocityConfig | None = None) -> WindowAggregator:
    c = gh(tmp_path, pz)
    agg = WindowAggregator(cfg or VelocityConfig())
    for i in range(3):
        data = (FIX / f"2026-09-20-{i}.json.gz").read_bytes()
        for rec in c.records(data, meta()):
            agg.add(T0 + i * H, rec)
    return agg


def test_m1_t8_gharchive_records_pseudonymized_and_bots_blanked(tmp_path, pz):
    c = gh(tmp_path, pz)
    recs = list(c.records((FIX / "2026-09-20-0.json.gz").read_bytes(), meta()))
    actors = {r["actor"] for r in recs}
    assert pz.pseudonym("user0001", "github") in actors
    assert not any(a and a.startswith("user") for a in actors)
    bots = [r for r in recs if r["is_bot"]]
    assert bots and all(r["actor"] is None for r in bots)


def test_r1_1_aggregation_raw_bot_lockstep_filtered(tmp_path, pz):
    rows = {(r.hour, r.repo_host_id): r for r in aggregate(tmp_path, pz).rows()}
    r1h0 = rows[(T0, 1000001)]
    assert (r1h0.stars_raw, r1h0.stars_bot, r1h0.stars_filtered) == (62, 1, 60)  # dup + bot
    assert (r1h0.forks_raw, r1h0.forks_filtered) == (2, 1)
    assert not r1h0.lockstep_flag
    r1h1 = rows[(T0 + H, 1000001)]
    assert r1h1.stars_filtered == 70 and not r1h1.lockstep_flag  # 30 % star-only: organic
    farm = rows[(T0 + H, 1000002)]
    assert farm.lockstep_flag and farm.stars_raw == 120
    assert (farm.stars_lockstep, farm.stars_filtered) == (120, 0)
    assert rows[(T0 + 2 * H, 1000002)].stars_filtered == 5
    bg = rows[(T0, 1000003)]
    assert (bg.stars_filtered, bg.forks_filtered) == (3, 2)


def test_r1_1_lockstep_thresholds_configurable(tmp_path, pz):
    cfg = VelocityConfig(lockstep_min_stars=500)
    rows = {(r.hour, r.repo_host_id): r for r in aggregate(tmp_path, pz, cfg).rows()}
    assert rows[(T0 + H, 1000002)].stars_filtered == 120


def test_r1_1_malformed_lines_skipped(tmp_path, pz):
    c = gh(tmp_path, pz)
    recs = list(c.records((FIX / "2026-09-20-1.json.gz").read_bytes(), meta()))
    assert recs and all("type" in r for r in recs)


# --- baseline / detection ---------------------------------------------------------------------
CFG = VelocityConfig()
BASE_START = T0


def all_hours() -> list[datetime]:
    return [BASE_START + i * H for i in range(CFG.baseline_hours)]


def cand(stars: int) -> Candidate:
    return Candidate(1, "org-a/repo-1", T0, stars, stars, 0)


def test_r1_1_zero_baseline_reduces_to_absolute_threshold():
    b = baseline_stats({}, set(), BASE_START, CFG)
    assert (b.quality, b.hours_covered, b.mean_48h, b.sigma_used) == ("none", 0, 0.0, 1.0)
    assert evaluate(cand(100), b, CFG).fires
    assert not evaluate(cand(99), b, CFG).fires


def test_r1_1_observed_but_quiet_baseline_is_full_quality_zero():
    b = baseline_stats({}, set(all_hours()), BASE_START, CFG)
    assert b.quality == "full" and b.mean_48h == 0 and b.sigma_used == 1.0
    assert evaluate(cand(120), b, CFG).fires


def test_r1_1_high_baseline_blocks_absolute_threshold():
    # a busy repo: ~3 stars/hour steadily -> 144 per 48 h; 150 is not a burst
    hourly = {h: 3 for h in all_hours()}
    b = baseline_stats(hourly, set(all_hours()), BASE_START, CFG)
    assert b.mean_48h == pytest.approx(144)
    assert b.std_48h == 0 and b.sigma_used == pytest.approx(12)  # Poisson floor sqrt(144)
    assert not evaluate(cand(150), b, CFG).fires
    assert evaluate(cand(144 + 3 * 12), b, CFG).fires


def test_r1_1_variable_baseline_uses_block_std():
    hourly = {h: (10 if (i // 48) % 2 else 0) for i, h in enumerate(all_hours())}
    b = baseline_stats(hourly, set(all_hours()), BASE_START, CFG)
    assert b.mean_48h == pytest.approx(480 * 7 / 15)
    assert b.std_48h > 200 and b.sigma_used == b.std_48h
    assert not evaluate(cand(800), b, CFG).fires


def test_r1_1_partial_baseline_scales_observed_hours_only():
    observed = set(all_hours()[:100])  # 100 h observed, the rest unknown (not zero)
    hourly = {h: 1 for h in all_hours()}
    b = baseline_stats(hourly, observed, BASE_START, CFG)
    assert b.quality == "partial" and b.hours_covered == 100
    assert b.mean_48h == pytest.approx(48)
    assert b.std_48h == 0  # only 2 full blocks < min_full_blocks


def test_r1_1_thresholds_configurable():
    b = baseline_stats({}, set(), BASE_START, CFG)
    cfg = VelocityConfig(min_stars_48h=50, sigma=5)
    assert evaluate(cand(60), b, cfg).fires and not evaluate(cand(60), b, CFG).fires


def test_chunks_split_at_utc_midnight():
    s, e = T0 + 20 * H, T0 + 50 * H
    chunks = list(day_chunks(s, e))
    assert chunks == [(s, T0 + 24 * H), (T0 + 24 * H, T0 + 48 * H), (T0 + 48 * H, e)]
    assert len(hours_between(s, e)) == 30
    assert floor_hour(datetime(2026, 9, 20, 5, 42)) == T0 + 5 * H
