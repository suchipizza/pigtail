"""Versioned analysis parameters (M11, M21b). Mirrors `schemas/analysis-params/v1.1.0.json`,
which is the citable, versioned copy (a test keeps the two equal); the Python copy is what runs,
so the app image does not need `schemas/`. `v1.0.0.json` is kept unchanged.

- `BURST`: the per-repo burst rule `velocity-v0` on star-history endpoint days (codebook v0.3.1
  §3.2-3.3; outcome model v2 §2.1): fire when `n(d-1) + n(d) >= 100` net stars and `z >= 3`
  against the 30 endpoint days before `d-1` (48-hour sums, sigma floored at the square root of
  the baseline mean, ADR-027 item 3); onset at `mu + 3*sqrt(mu)` with `mu_h = mu_d / 24`
  (ADR-039.4); end after 3 calm days; bursts less than 7 days apart merge; quiet intervals are
  at least 7 days.
- `FAKE_STAR_FILTER`: StarScout `starscout-v0` (outcome model v2 §4). **Retired** in v1.1.0
  (ADR-070.4): per-account methods don't work for repos the operator doesn't own. Kept, marked
  retired, so older reports stay citable; no code applies it.
- `ANOMALY`: the aggregate anomaly checks that replace it (`anomaly-v0`, ADR-070.4, PRD R3.3;
  `pigtail.analysis.anomaly`): star spikes with no matching forks, issues, downloads or
  external mentions, and odd stars-to-activity ratios. Star metrics are labelled
  "unfiltered, anomaly-checked".

A change is a new `PARAMS_VERSION` and a new JSON file, logged as an ADR.
"""

from __future__ import annotations

from dataclasses import dataclass, field

PARAMS_ID = "pigtail-analysis-params"
PARAMS_VERSION = "1.1.0"
STAR_HISTORY_DAY_TZ = "America/Los_Angeles"


@dataclass(frozen=True)
class BurstShapeParams:
    sudden_peak_max_index: int = 1  # peak on the onset day or the day after (within 48 h)
    decay_within_index: int = 7  # ... and below `decay_share` of the peak by day index 7
    decay_share: float = 0.5
    gradual_peak_min_index: int = 8  # peak more than 7 days after onset


@dataclass(frozen=True)
class BurstParams:
    rule_version: str = "velocity-v0"
    min_net_stars_48h: int = 100
    sigma: float = 3.0
    baseline_days: int = 30
    min_sigma: float = 1.0
    full_baseline_coverage: float = 0.9
    min_full_blocks: int = 3
    onset_sigma: float = 3.0
    onset_hours_per_day: int = 24
    end_quiet_days: int = 3
    merge_gap_days: int = 7
    min_quiet_days: int = 7
    shape: BurstShapeParams = field(default_factory=BurstShapeParams)


@dataclass(frozen=True)
class CampaignRule:
    min_fake_stars_in_month: int = 50
    min_fake_share_in_month: float = 0.5
    min_fake_share_all_time: float = 0.1
    comparators: str = "strictly greater than"


@dataclass(frozen=True)
class FakeStarParams:
    id: str = "starscout-v0"
    n: int = 50
    m: int = 10
    delta_t_days: int = 30
    rho: float = 0.5
    level: str = "campaign"
    campaign_rule: CampaignRule = field(default_factory=CampaignRule)
    status: str = "retired"
    retired_by: str = "ADR-070.4 (analysis-params v1.1.0): replaced by anomaly_check"


@dataclass(frozen=True)
class ActivityExcess:
    """Minimum excess over the channel's expected count in the spike window for the channel
    to "match" a star spike (besides the relative lift)."""

    forks: float = 3.0
    issues: float = 3.0
    downloads: float = 100.0
    mentions: float = 1.0


@dataclass(frozen=True)
class AnomalyParams:
    """ADR-070.4 aggregate anomaly checks on daily star counts (`pigtail.analysis.anomaly`)."""

    rule_version: str = "anomaly-v0"
    label: str = "unfiltered, anomaly-checked"
    channels: tuple[str, ...] = ("forks", "issues", "downloads", "mentions")
    # star spike: n(d) >= min_spike_stars and (n(d) - mean) / sigma >= spike_sigma against the
    # `baseline_days` known days before the spike (at least `min_baseline_days` of them);
    # sigma = max(sample std, sqrt(mean), min_sigma). Consecutive spike days are one spike.
    baseline_days: int = 30
    min_baseline_days: int = 14
    min_spike_stars: int = 50
    spike_sigma: float = 3.0
    min_sigma: float = 1.0
    # matching activity: in [first spike day - before, last spike day + after], a channel's
    # count is >= min_activity_lift x its expected count (baseline daily mean x window days)
    # and exceeds it by at least `min_activity_excess.<channel>`.
    window_before_days: int = 1
    window_after_days: int = 3
    min_activity_lift: float = 2.0
    min_activity_excess: ActivityExcess = field(default_factory=ActivityExcess)
    # stars-to-activity ratio over the window: stars / (forks + issues + 1). Flagged when its
    # log10 is a high outlier among the brief's repos (robust z >= ratio_robust_z, median/MAD)
    # with at least `ratio_min_population` repos, else above `ratio_max_stars_per_activity`.
    # Repos with fewer than `ratio_min_stars` stars are not judged.
    ratio_channels: tuple[str, ...] = ("forks", "issues")
    ratio_min_population: int = 8
    ratio_robust_z: float = 3.5
    ratio_max_stars_per_activity: float = 50.0
    ratio_min_stars: int = 200


BURST = BurstParams()
FAKE_STAR_FILTER = FakeStarParams()
ANOMALY = AnomalyParams()
