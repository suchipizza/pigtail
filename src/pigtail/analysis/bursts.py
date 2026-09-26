"""Per-repo burst and quiet segmentation on star-history days (M11; codebook v0.3.0 §3.2-3.3,
outcome model v2 §2.1; parameters `pigtail.analysis.params.BURST`, version `PARAMS_VERSION`).

Pure functions over one repo's daily series: no database, no network, no global scan and no
watch list (those were removed in M11, ADR-047.6). The statistics are the velocity-v0 baseline
the removed detection code used (git tag `archive/global-collection`,
`src/pigtail/capture/detection_v1.py` `daily_baseline`), kept unchanged.

**Series.** `series` maps an endpoint day (the star-history endpoint's own day label, in
`star_history_day_tz`, never converted to UTC) to its raw net star count `n(d)`. A day that is
absent is *unknown* (never fetched, an incomplete week, or not ended yet), not zero. Callers pass
only days that have ended.

**Detection (`velocity-v0`).** Day `d` fires when `n(d-1)` and `n(d)` are known,
`n(d-1) + n(d) >= min_net_stars_48h` (100) and `z = (n(d-1) + n(d) - mean_48h) / sigma_used >=
sigma` (3), with the baseline taken from the `baseline_days` (30) endpoint days before `d-1`:
`mean_48h` = mean stars per observed day x 2 (floored at 0), `std_48h` = sample std of the
2-day block sums whose two days are observed (when at least `min_full_blocks` blocks exist, else
0), `sigma_used = max(std_48h, sqrt(mean_48h), min_sigma)`, `quality` = `full` (at least 90 % of
the days observed), `partial` or `none`. Days before the repo's creation don't count.

**Onset.** The 48-hour window is endpoint days `d-1` and `d`. Where `hourly` net gains cover every
hour of that window, the onset is the earliest hour whose gain exceeds `mu_h + 3*sqrt(mu_h)`
(`1` when `mu_h = 0`), `mu_h = mu_d / 24` (ADR-039.4; DST days are not corrected: the window is
48 absolute hours from the start of `d-1`), else the start of the window; precision `hour`.
Otherwise the same rule on days with `mu_d`: the start of the first of `d-1`, `d` whose count
exceeds the threshold, else the start of `d-1`; precision `day`. `mu_d` is the baseline's mean
stars per endpoint day. Hourly data exists only in caches from before the re-scope; GH Archive
is never used (ADR-047.8).

**End, merging, quiet.** A burst ends at the start of the first run of `end_quiet_days` (3)
consecutive known days after the onset day with `n(d) <= mu_d + 3*sqrt(mu_d)` (`1` when
`mu_d = 0`); with no such run inside the window it is open (`end = None`). A new burst starts at
the first firing day not inside a burst. Bursts separated by fewer than `merge_gap_days` (7) days
merge into one with `multi_peak = True`. Quiet intervals are maximal runs of at least
`min_quiet_days` (7) known days inside the window that are not in a burst; unknown days and days
before creation split them.

**Shape** (descriptive): `sudden_fast_decay` when the peak is on day index 0 or 1 from the onset
day and some day with index <= 7 after the peak is below 50 % of the peak; `gradual_build` when
the peak index is >= 8; `mixed` otherwise; `unknown` when a day the rule needs is unknown.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pigtail.analysis.params import BURST, PARAMS_VERSION, STAR_HISTORY_DAY_TZ, BurstParams

BaselineQuality = Literal["full", "partial", "none"]
Precision = Literal["hour", "day"]
Shape = Literal["sudden_fast_decay", "gradual_build", "mixed", "unknown"]
Phase = Literal["pre_first_burst", "inter_burst", "post_last_burst"]

DAY = timedelta(days=1)


@dataclass(frozen=True)
class DailyBaseline:
    first_day: date
    last_day: date
    mu_d: float  # mean stars per observed endpoint day (floored at 0)
    mean_48h: float
    std_48h: float
    sigma_used: float
    days_covered: int
    quality: BaselineQuality


@dataclass(frozen=True)
class Firing:
    day: date
    stars_48h: int
    z: float
    baseline: DailyBaseline


@dataclass(frozen=True)
class Onset:
    day: date  # endpoint day containing the onset
    at: datetime  # UTC instant: the onset hour, or the start of `day` in the endpoint's zone
    precision: Precision


@dataclass(frozen=True)
class Burst:
    onset: Onset
    first_firing_day: date
    end: date | None  # first day of the calm run (exclusive end); None: open at window end
    last_day: date  # last day inside the burst
    stars_48h: int
    z: float
    baseline: DailyBaseline
    multi_peak: bool
    peak_day: date | None
    peak_stars: int | None
    shape: Shape


@dataclass(frozen=True)
class Quiet:
    first_day: date
    last_day: date
    phase: Phase


@dataclass(frozen=True)
class Segmentation:
    first_day: date
    last_day: date
    bursts: tuple[Burst, ...]
    quiet: tuple[Quiet, ...]
    rule_version: str
    params_version: str = PARAMS_VERSION


def _days(first: date, last: date) -> list[date]:
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def calm_threshold(mu: float, sigma: float) -> float:
    """`mu + sigma*sqrt(mu)`, or `mu + 1` when `mu = 0` (codebook §3.2)."""
    return mu + sigma * math.sqrt(mu) if mu > 0 else mu + 1


def daily_baseline(
    series: Mapping[date, int],
    start: date,
    p: BurstParams = BURST,
    not_before: date | None = None,
) -> DailyBaseline:
    """velocity-v0 baseline statistics over the `p.baseline_days` endpoint days from `start`."""
    n = p.baseline_days
    days = [start + timedelta(days=i) for i in range(n)]
    obs = [d for d in days if d in series and (not_before is None or d >= not_before)]
    last = days[-1]
    if not obs:
        return DailyBaseline(start, last, 0.0, 0.0, 0.0, p.min_sigma, 0, "none")
    mu_d = max(0.0, sum(series[d] for d in obs) / len(obs))
    mean = mu_d * 2
    seen = set(obs)
    blocks = [
        float(series[days[i]] + series[days[i + 1]])
        for i in range(0, n - 1, 2)
        if days[i] in seen and days[i + 1] in seen
    ]
    std = statistics.stdev(blocks) if len(blocks) >= max(2, p.min_full_blocks) else 0.0
    quality: BaselineQuality = "full" if len(obs) >= p.full_baseline_coverage * n else "partial"
    sigma_used = max(std, math.sqrt(mean), p.min_sigma)
    return DailyBaseline(start, last, mu_d, mean, std, sigma_used, len(obs), quality)


def evaluate_day(
    series: Mapping[date, int],
    d: date,
    p: BurstParams = BURST,
    created: date | None = None,
) -> Firing | None:
    """The firing of day `d`, or None (does not fire, or `n(d-1)` / `n(d)` unknown)."""
    a, b = series.get(d - DAY), series.get(d)
    if a is None or b is None:
        return None
    base = daily_baseline(series, d - timedelta(days=1 + p.baseline_days), p, created)
    s48 = a + b
    z = (s48 - base.mean_48h) / base.sigma_used
    if s48 >= p.min_net_stars_48h and z >= p.sigma:
        return Firing(d, s48, z, base)
    return None


def day_start(day: date, tz: str = STAR_HISTORY_DAY_TZ) -> datetime:
    """Start of an endpoint day in its zone, as a UTC instant."""
    return datetime(day.year, day.month, day.day, tzinfo=ZoneInfo(tz)).astimezone(UTC)


def onset(
    series: Mapping[date, int],
    firing: Firing,
    p: BurstParams = BURST,
    tz: str = STAR_HISTORY_DAY_TZ,
    hourly: Mapping[datetime, int] | None = None,
) -> Onset:
    """Onset of the burst detected by `firing` (module docstring). `hourly` maps UTC hour starts
    to net star gains in that hour."""
    d0 = firing.day - DAY
    mu_d = firing.baseline.mu_d
    if hourly is not None:
        start = day_start(d0, tz)
        hours = [start + timedelta(hours=k) for k in range(2 * p.onset_hours_per_day)]
        if all(h in hourly for h in hours):
            thr = calm_threshold(mu_d / p.onset_hours_per_day, p.onset_sigma)
            for h in hours:
                if hourly[h] > thr:
                    return Onset(h.astimezone(ZoneInfo(tz)).date(), h, "hour")
            return Onset(d0, start, "hour")
    thr = calm_threshold(mu_d, p.onset_sigma)
    for day in (d0, firing.day):
        if series[day] > thr:
            return Onset(day, day_start(day, tz), "day")
    return Onset(d0, day_start(d0, tz), "day")


def burst_end(
    series: Mapping[date, int], onset_day: date, mu_d: float, last_day: date, p: BurstParams = BURST
) -> date | None:
    """First day of the first run of `p.end_quiet_days` calm known days after `onset_day`."""
    thr = calm_threshold(mu_d, p.sigma)
    run = 0
    start: date | None = None
    for d in _days(onset_day + DAY, last_day):
        v = series.get(d)
        if v is not None and v <= thr:
            if run == 0:
                start = d
            run += 1
            if run >= p.end_quiet_days:
                return start
        else:
            run = 0
    return None


def _shape(
    series: Mapping[date, int], onset_day: date, last_day: date, p: BurstParams
) -> tuple[date | None, int | None, Shape]:
    days = _days(onset_day, last_day)
    if any(d not in series for d in days):
        return None, None, "unknown"
    peak_day = max(days, key=lambda d: (series[d], -d.toordinal()))  # earliest of equal peaks
    peak = series[peak_day]
    idx = (peak_day - onset_day).days
    s = p.shape
    if idx >= s.gradual_peak_min_index:
        return peak_day, peak, "gradual_build"
    if idx <= s.sudden_peak_max_index:
        after = [onset_day + timedelta(days=i) for i in range(idx + 1, s.decay_within_index + 1)]
        known = [series[d] for d in after if d in series]
        if any(v < s.decay_share * peak for v in known):
            return peak_day, peak, "sudden_fast_decay"
        if len(known) < len(after):
            return peak_day, peak, "unknown"
    return peak_day, peak, "mixed"


def _finish(
    series: Mapping[date, int],
    o: Onset,
    f: Firing,
    end: date | None,
    last_day: date,
    multi_peak: bool,
    p: BurstParams,
) -> Burst:
    last_in = (end - DAY) if end is not None else last_day
    peak_day, peak, shape = _shape(series, o.day, last_in, p)
    return Burst(o, f.day, end, last_in, f.stars_48h, f.z, f.baseline, multi_peak, peak_day,
                 peak, shape)  # fmt: skip


def segment(
    series: Mapping[date, int],
    first_day: date,
    last_day: date,
    *,
    p: BurstParams = BURST,
    tz: str = STAR_HISTORY_DAY_TZ,
    created: date | None = None,
    hourly: Mapping[datetime, int] | None = None,
) -> Segmentation:
    """Bursts and quiet intervals of one repo in the case window `[first_day, last_day]`."""
    raw: list[tuple[Onset, Firing, date | None]] = []
    busy_until: date | None = None  # days before this are inside the current burst
    open_burst = False
    for d in _days(first_day, last_day):
        if open_burst or (busy_until is not None and d < busy_until):
            continue
        if created is not None and d - DAY < created:
            continue
        f = evaluate_day(series, d, p, created)
        if f is None:
            continue
        o = onset(series, f, p, tz, hourly)
        end = burst_end(series, o.day, f.baseline.mu_d, last_day, p)
        raw.append((o, f, end))
        if end is None:
            open_burst = True
        else:
            busy_until = end
    merged: list[tuple[Onset, Firing, date | None, bool]] = []
    for o, f, end in raw:
        if merged:
            po, pf, pend, _mp = merged[-1]
            if pend is not None and (o.day - pend).days < p.merge_gap_days:
                merged[-1] = (po, pf, end, True)
                continue
        merged.append((o, f, end, False))
    bursts = tuple(_finish(series, o, f, end, last_day, mp, p) for o, f, end, mp in merged)
    return Segmentation(
        first_day, last_day, bursts, quiet_intervals(series, first_day, last_day, bursts, p,
                                                     created), p.rule_version,
    )  # fmt: skip


def quiet_intervals(
    series: Mapping[date, int],
    first_day: date,
    last_day: date,
    bursts: tuple[Burst, ...],
    p: BurstParams = BURST,
    created: date | None = None,
) -> tuple[Quiet, ...]:
    in_burst: set[date] = set()
    for bu in bursts:
        in_burst.update(_days(bu.onset.day, bu.last_day))
    runs: list[tuple[date, date]] = []
    start: date | None = None
    prev: date | None = None
    for d in _days(first_day, last_day):
        ok = d in series and d not in in_burst and (created is None or d >= created)
        if ok and start is None:
            start = d
        if not ok and start is not None and prev is not None:
            runs.append((start, prev))
            start = None
        prev = d
    if start is not None:
        runs.append((start, last_day))
    out = []
    for a, b in runs:
        if (b - a).days + 1 < p.min_quiet_days:
            continue
        phase: Phase
        if not bursts or b < bursts[0].onset.day:
            phase = "pre_first_burst"
        elif a > bursts[-1].last_day:
            phase = "post_last_burst"
        else:
            phase = "inter_burst"
        out.append(Quiet(a, b, phase))
    return tuple(out)
