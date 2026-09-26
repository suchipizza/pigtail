"""Aggregate anomaly checks on daily star counts (PRD R3.3; ADR-070.4; parameters
`pigtail.analysis.params.ANOMALY`, `schemas/analysis-params/v1.1.0.json` block
`anomaly_check`, rule `anomaly-v0`).

Per-account fake-star filtering (StarScout) needs every stargazer's history and no longer works
for repos the operator doesn't own (ADR-070.4). These checks look only at a repo's **aggregate
daily counts**, so star metrics are reported as **"unfiltered, anomaly-checked"**: nothing is
removed, flags are shown next to the numbers and used in the sensitivity checks.

Pure functions, no database or network. `stars` maps an endpoint day to the repo's net new
stars that day; `activity` maps a channel (`forks`, `issues`, `downloads`, `mentions`) to its
daily counts on the same days. A day that is absent is **unknown**, not zero: a channel with no
known day in the window is not used, and a spike that no channel could be checked against is
`unchecked`, never flagged.

**Check 1: spike without matching activity** (`spike_no_activity`). Day `d` is a spike day when
`n(d) >= min_spike_stars` and `(n(d) - mean) / sigma >= spike_sigma`, with `mean` and
`sigma = max(sample std, sqrt(mean), min_sigma)` from the `baseline_days` known days before it
(at least `min_baseline_days`). Consecutive spike days form one spike. A channel *matches* a
spike when, over the window `[first day - window_before_days, last day + window_after_days]`,
its count is at least `min_activity_lift` times the expected count (the channel's baseline daily
mean over the same 30 days, times the number of known window days) and exceeds it by at least
`min_activity_excess.<channel>`. A spike checked against at least one channel with none
matching is flagged.

**Check 2: odd stars-to-activity ratio** (`ratio_outlier`). Over the window,
`ratio = stars / (forks + issues + 1)`. Among the brief's repos (`ratio_flags`), a repo is
flagged when `log10(ratio)` is a high outlier: robust z (median and MAD x 1.4826) at least
`ratio_robust_z`, given at least `ratio_min_population` judged repos; with fewer, when the
ratio exceeds `ratio_max_stars_per_activity`. Repos with fewer than `ratio_min_stars` stars, or
without both ratio channels, are not judged.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any, Literal

from pigtail.analysis.params import ANOMALY, PARAMS_VERSION, AnomalyParams

Series = Mapping[date, int | float]
FlagKind = Literal["spike_no_activity", "ratio_outlier"]
CheckStatus = Literal["checked", "partially_checked", "unchecked", "no_spikes"]
STAR_LABEL = ANOMALY.label  # "unfiltered, anomaly-checked"


@dataclass(frozen=True)
class Spike:
    start: date
    end: date
    stars: float  # net stars on the spike days
    baseline_mean: float
    z_max: float
    channels_checked: tuple[str, ...] = ()
    channels_matching: tuple[str, ...] = ()

    @property
    def checked(self) -> bool:
        return bool(self.channels_checked)

    @property
    def unexplained(self) -> bool:
        return self.checked and not self.channels_matching

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["start"], d["end"] = self.start.isoformat(), self.end.isoformat()
        d["channels_checked"] = list(self.channels_checked)
        d["channels_matching"] = list(self.channels_matching)
        return d


@dataclass(frozen=True)
class AnomalyFlag:
    kind: FlagKind
    start: date | None
    end: date | None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "detail": self.detail,
        }


@dataclass
class AnomalyReport:
    repo: str
    spikes: list[Spike]
    flags: list[AnomalyFlag]
    status: CheckStatus
    ratio: float | None
    stars_total: float
    rule_version: str = ANOMALY.rule_version
    params_version: str = PARAMS_VERSION
    label: str = STAR_LABEL

    @property
    def flagged(self) -> bool:
        return bool(self.flags)

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "label": self.label,
            "rule_version": self.rule_version,
            "params_version": self.params_version,
            "status": self.status,
            "flagged": self.flagged,
            "flags": [f.to_dict() for f in self.flags],
            "spikes": [s.to_dict() for s in self.spikes],
            "ratio": None if self.ratio is None else round(self.ratio, 4),
            "stars_total": self.stars_total,
        }


def _days_before(series: Series, day: date, n: int) -> list[float]:
    return [float(series[d]) for i in range(1, n + 1) if (d := day - timedelta(days=i)) in series]


def _sigma(values: list[float], mean: float, p: AnomalyParams) -> float:
    std = statistics.stdev(values) if len(values) >= 2 else 0.0
    return max(std, math.sqrt(max(mean, 0.0)), p.min_sigma)


def find_spikes(stars: Series, p: AnomalyParams = ANOMALY) -> list[Spike]:
    """Star spikes (consecutive spike days merged); days without a full baseline never spike."""
    spike_days: list[tuple[date, float, float]] = []  # (day, mean, z)
    for d in sorted(stars):
        n = float(stars[d])
        if n < p.min_spike_stars:
            continue
        base = _days_before(stars, d, p.baseline_days)
        if len(base) < p.min_baseline_days:
            continue
        mean = max(0.0, statistics.fmean(base))
        z = (n - mean) / _sigma(base, mean, p)
        if z >= p.spike_sigma:
            spike_days.append((d, mean, z))
    spikes: list[Spike] = []
    for d, mean, z in spike_days:
        if spikes and d - spikes[-1].end == timedelta(days=1):
            last = spikes[-1]
            spikes[-1] = Spike(last.start, d, last.stars + float(stars[d]), last.baseline_mean,
                               max(last.z_max, z))  # fmt: skip
        else:
            spikes.append(Spike(d, d, float(stars[d]), mean, z))
    return spikes


def _excess(p: AnomalyParams, channel: str) -> float:
    return float(getattr(p.min_activity_excess, channel, 1.0))


def match_spike(spike: Spike, activity: Mapping[str, Series], p: AnomalyParams = ANOMALY) -> Spike:
    """Check one spike against each channel with known days in its window."""
    first = spike.start - timedelta(days=p.window_before_days)
    last = spike.end + timedelta(days=p.window_after_days)
    window = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    checked: list[str] = []
    matching: list[str] = []
    for channel in p.channels:
        series = activity.get(channel)
        if not series:
            continue
        known = [d for d in window if d in series]
        if not known:
            continue
        base = _days_before(series, first, p.baseline_days)
        mean = statistics.fmean(base) if base else 0.0
        observed = sum(float(series[d]) for d in known)
        expected = max(mean, 0.0) * len(known)
        checked.append(channel)
        lift_ok = observed >= p.min_activity_lift * expected
        if lift_ok and observed - expected >= _excess(p, channel):
            matching.append(channel)
    return Spike(
        spike.start,
        spike.end,
        spike.stars,
        spike.baseline_mean,
        spike.z_max,
        tuple(checked),
        tuple(matching),
    )


def stars_to_activity(
    stars: Series, activity: Mapping[str, Series], p: AnomalyParams = ANOMALY
) -> float | None:
    """`stars / (forks + issues + 1)` over the known days; None without the ratio channels."""
    if any(not activity.get(c) for c in p.ratio_channels):
        return None
    total = sum(float(v) for v in stars.values())
    act = sum(float(v) for c in p.ratio_channels for v in activity[c].values())
    return total / (act + 1.0)


def check_repo(
    repo: str,
    stars: Series,
    activity: Mapping[str, Series],
    p: AnomalyParams = ANOMALY,
) -> AnomalyReport:
    """Check 1 for one repo, plus its ratio (check 2 needs the population: `ratio_flags`)."""
    spikes = [match_spike(s, activity, p) for s in find_spikes(stars, p)]
    flags = [
        AnomalyFlag(
            "spike_no_activity",
            s.start,
            s.end,
            {
                "stars": s.stars,
                "baseline_mean": round(s.baseline_mean, 3),
                "z_max": round(s.z_max, 2),
                "channels_checked": list(s.channels_checked),
            },
        )
        for s in spikes
        if s.unexplained
    ]
    status: CheckStatus
    if not spikes:
        status = "no_spikes"
    elif all(s.checked for s in spikes):
        status = "checked"
    elif any(s.checked for s in spikes):
        status = "partially_checked"
    else:
        status = "unchecked"
    return AnomalyReport(
        repo=repo,
        spikes=spikes,
        flags=flags,
        status=status,
        ratio=stars_to_activity(stars, activity, p),
        stars_total=float(sum(float(v) for v in stars.values())),
        rule_version=p.rule_version,
    )


def ratio_flags(
    reports: Mapping[str, AnomalyReport], p: AnomalyParams = ANOMALY
) -> dict[str, AnomalyFlag]:
    """Check 2 across the brief's repos; adds each flag to its report and returns them."""
    judged = {
        r: rep.ratio
        for r, rep in reports.items()
        if rep.ratio is not None and rep.ratio > 0 and rep.stars_total >= p.ratio_min_stars
    }
    out: dict[str, AnomalyFlag] = {}
    if not judged:
        return out
    logs = {r: math.log10(v) for r, v in judged.items() if v is not None}
    if len(logs) >= p.ratio_min_population:
        med = statistics.median(logs.values())
        mad = statistics.median(abs(v - med) for v in logs.values()) * 1.4826
        for r, v in logs.items():
            z = (v - med) / mad if mad > 0 else (math.inf if v > med else 0.0)
            if z >= p.ratio_robust_z:
                out[r] = AnomalyFlag(
                    "ratio_outlier",
                    None,
                    None,
                    {
                        "ratio": round(judged[r] or 0.0, 3),
                        "robust_z": round(z, 2) if math.isfinite(z) else None,
                        "basis": "population",
                        "population": len(logs),
                    },
                )
    else:
        for r, v in judged.items():
            if v is not None and v > p.ratio_max_stars_per_activity:
                out[r] = AnomalyFlag(
                    "ratio_outlier",
                    None,
                    None,
                    {
                        "ratio": round(v, 3),
                        "basis": "absolute",
                        "max": p.ratio_max_stars_per_activity,
                        "population": len(logs),
                    },
                )
    for r, flag in out.items():
        reports[r].flags.append(flag)
    return out


def check_population(
    series: Mapping[str, tuple[Series, Mapping[str, Series]]], p: AnomalyParams = ANOMALY
) -> dict[str, AnomalyReport]:
    """Both checks for a brief's repos: `{repo: (stars, activity)}` -> reports."""
    reports = {repo: check_repo(repo, st, act, p) for repo, (st, act) in series.items()}
    ratio_flags(reports, p)
    return reports
