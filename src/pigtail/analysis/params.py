"""Versioned analysis parameters (M11). Mirrors `schemas/analysis-params/v1.0.0.json`, which is
the citable, versioned copy (a test keeps the two equal); the Python copy is what runs, so the
app image does not need `schemas/`.

- `BURST`: the per-repo burst rule `velocity-v0` on star-history endpoint days (codebook v0.3.0
  §3.2-3.3; outcome model v2 §2.1): fire when `n(d-1) + n(d) >= 100` net stars and `z >= 3`
  against the 30 endpoint days before `d-1` (48-hour sums, sigma floored at the square root of
  the baseline mean, ADR-027 item 3); onset at `mu + 3*sqrt(mu)` with `mu_h = mu_d / 24`
  (ADR-039.4); end after 3 calm days; bursts less than 7 days apart merge; quiet intervals are
  at least 7 days.
- `FAKE_STAR_FILTER`: StarScout `starscout-v0` (outcome model v2 §4); moved here from the
  retired thresholds JSON (`schemas/outcome-thresholds/v0.2.0.json`, ADR-049.12). Not applied
  by any code yet (M13).

A change is a new `PARAMS_VERSION` and a new JSON file, logged as an ADR.
"""

from __future__ import annotations

from dataclasses import dataclass, field

PARAMS_ID = "pigtail-analysis-params"
PARAMS_VERSION = "1.0.0"
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


BURST = BurstParams()
FAKE_STAR_FILTER = FakeStarParams()
