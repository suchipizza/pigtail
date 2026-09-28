"""Krippendorff's alpha for nominal, ordinal and interval data (PRD R7.2, R7.5, §9.2; codebook
§10; ADR-047.7, ADR-065, ADR-086).

The coincidence-matrix formulation of Krippendorff (2011), "Computing Krippendorff's
Alpha-Reliability" (LR [70]):

- The data are units (rows), each with one value per coder or `None` (missing). Only
  **pairable** units, with at least two values, count.
- Coincidences: every ordered pair of values (c, k) from different coders in a unit u adds
  `1 / (m_u - 1)`, where m_u is the number of values in u. `n_c` is the row sum and `n` the total.
- `alpha = 1 - (n - 1) * sum(o_ck * d2(c, k)) / sum(n_c * n_k * d2(c, k))`.
- Difference functions: nominal `0 if c == k else 1`; interval `(c - k)^2`; ordinal
  `(sum_{g=c..k} n_g - (n_c + n_k) / 2)^2`, over the ranks of the values in the given order.

`alpha(...)` returns None when the expected disagreement is 0 (only one value occurs, or no
pairable unit): alpha is undefined there, never 1.0 by convention (the codebook's "D_e > 0"
rule, §10.4). The tests reproduce the worked examples of Krippendorff (2011).

`bootstrap_ci` resamples with replacement (codebook §10.5: B = 10,000, percentile 95 %
interval, fixed seed); resamples with D_e = 0 are dropped and counted, and more than 5 % dropped
labels the interval "unstable". With `clusters` (the case of each unit) it resamples **cases**,
each drawn case bringing all its units (a cluster bootstrap, `kalpha-v2`, ADR-086 addendum 3):
units of one case are not independent (the pooled C11a statistic has 12 pattern units per case,
the item fields one unit per evidence item), so resampling units would make the interval too
narrow. Where every case has one unit this is the unit bootstrap. The interval's `method` and
`resampled` say which was used.
"""

from __future__ import annotations

import math
import random
from collections.abc import Hashable, Sequence
from dataclasses import dataclass
from typing import Literal

Level = Literal["nominal", "ordinal", "interval"]
ALPHA_VERSION = "kalpha-v2"  # v2: bootstrap over cases when units share a case
LOW_RELIABILITY_BELOW = 0.70  # PRD §9.2, ADR-047.7, ADR-065
MIN_PAIRABLE = 30  # codebook §10.4
MIN_RARER_BINARY = 5  # codebook §10.4
BOOTSTRAP_RESAMPLES = 10_000  # codebook §10.5
BOOTSTRAP_SEED = 20260928
UNSTABLE_DROPPED_SHARE = 0.05

Unit = Sequence[Hashable | None]


@dataclass(frozen=True)
class Coincidences:
    """The coincidence matrix of the pairable units (sparse), with the marginals."""

    o: dict[tuple[Hashable, Hashable], float]
    n_c: dict[Hashable, float]
    n: float
    pairable: int


def pairable(units: Sequence[Unit]) -> list[list[Hashable]]:
    """The values of every unit with at least two of them (missing values removed)."""
    out = []
    for u in units:
        vals = [v for v in u if v is not None]
        if len(vals) >= 2:
            out.append(vals)
    return out


def _unit_pairs(vals: Sequence[Hashable]) -> dict[tuple[Hashable, Hashable], float]:
    m = len(vals)
    out: dict[tuple[Hashable, Hashable], float] = {}
    for i, c in enumerate(vals):
        for j, k in enumerate(vals):
            if i != j:
                out[(c, k)] = out.get((c, k), 0.0) + 1.0 / (m - 1)
    return out


def coincidences(units: Sequence[Unit]) -> Coincidences:
    o: dict[tuple[Hashable, Hashable], float] = {}
    rows = pairable(units)
    for vals in rows:
        for key, w in _unit_pairs(vals).items():
            o[key] = o.get(key, 0.0) + w
    n_c: dict[Hashable, float] = {}
    for (c, _k), w in o.items():
        n_c[c] = n_c.get(c, 0.0) + w
    return Coincidences(o, n_c, sum(n_c.values()), len(rows))


def _order(values: Sequence[Hashable], order: Sequence[Hashable] | None) -> list[Hashable]:
    if order is not None:
        missing = [v for v in values if v not in order]
        if missing:
            raise ValueError(f"values not in the ordinal order: {missing!r}")
        return list(order)
    return sorted(values, key=lambda v: (str(type(v)), str(v)))


def _delta2(
    level: Level, n_c: dict[Hashable, float], order: Sequence[Hashable] | None
) -> dict[tuple[Hashable, Hashable], float]:
    vals = list(n_c)
    d: dict[tuple[Hashable, Hashable], float] = {}
    if level == "nominal":
        for c in vals:
            for k in vals:
                d[(c, k)] = 0.0 if c == k else 1.0
        return d
    if level == "interval":
        for c in vals:
            for k in vals:
                if not isinstance(c, int | float) or not isinstance(k, int | float):
                    raise ValueError("interval alpha needs numeric values")
                d[(c, k)] = float(c - k) ** 2
        return d
    ranked = [v for v in _order(vals, order) if v in n_c]
    pos = {v: i for i, v in enumerate(ranked)}
    for c in ranked:
        for k in ranked:
            lo, hi = sorted((pos[c], pos[k]))
            s = sum(n_c[ranked[g]] for g in range(lo, hi + 1))
            d[(c, k)] = (s - (n_c[c] + n_c[k]) / 2.0) ** 2
    return d


def _alpha_from(co: Coincidences, level: Level, order: Sequence[Hashable] | None) -> float | None:
    if co.n <= 1:
        return None
    d = _delta2(level, co.n_c, order)
    observed = sum(w * d[key] for key, w in co.o.items())
    expected = sum(co.n_c[c] * co.n_c[k] * d[(c, k)] for c in co.n_c for k in co.n_c)
    if expected <= 0:
        return None
    return 1.0 - (co.n - 1.0) * observed / expected


def alpha(
    units: Sequence[Unit], level: Level = "nominal", order: Sequence[Hashable] | None = None
) -> float | None:
    """Krippendorff's alpha of `units` (one row per unit, one value per coder, None = missing).
    `order` ranks the values of ordinal data (lowest first); default: their sort order."""
    return _alpha_from(coincidences(units), level, order)


def expected_disagreement_positive(units: Sequence[Unit]) -> bool:
    """At least two distinct values among the pairable units (codebook §10.4 rule 2)."""
    return len({v for vals in pairable(units) for v in vals}) >= 2


def raw_agreement(units: Sequence[Unit]) -> float | None:
    """Share of pairable units whose values are all equal (reported next to alpha)."""
    rows = pairable(units)
    if not rows:
        return None
    return sum(1 for vals in rows if len(set(vals)) == 1) / len(rows)


@dataclass(frozen=True)
class BootstrapCI:
    low: float | None
    high: float | None
    resamples: int
    dropped: int
    seed: int
    resampled: str = "units"  # "units" or "cases"
    clusters: int | None = None  # cases with a pairable unit (resampled: cases)

    @property
    def unstable(self) -> bool:
        return self.resamples > 0 and self.dropped / self.resamples > UNSTABLE_DROPPED_SHARE

    def to_dict(self) -> dict[str, object]:
        return {
            "low": None if self.low is None else round(self.low, 4),
            "high": None if self.high is None else round(self.high, 4),
            "resamples": self.resamples,
            "dropped": self.dropped,
            "seed": self.seed,
            "unstable": self.unstable,
            "resampled": self.resampled,
            **({"cases": self.clusters} if self.clusters is not None else {}),
            "method": (
                "nonparametric cluster bootstrap over cases (all units of a drawn case), "
                "percentile, 95 %"
                if self.resampled == "cases"
                else "nonparametric bootstrap over units, percentile, 95 %"
            ),
        }


def bootstrap_ci(
    units: Sequence[Unit],
    level: Level = "nominal",
    order: Sequence[Hashable] | None = None,
    *,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
    clusters: Sequence[Hashable] | None = None,
) -> BootstrapCI:
    """Percentile 95 % interval of alpha over `resamples` bootstrap samples (codebook §10.5):
    of the pairable units, or, with `clusters` (one case id per unit of `units`) and a case
    with several pairable units, of the cases (module docstring). The order of `order`
    (ordinal) is kept for every resample."""
    if clusters is not None and len(clusters) != len(units):
        raise ValueError("clusters must name the case of every unit")
    groups: dict[Hashable, list[dict[tuple[Hashable, Hashable], float]]] = {}
    for i, u in enumerate(units):
        vals = [v for v in u if v is not None]
        if len(vals) < 2:
            continue
        cid = clusters[i] if clusters is not None else i
        groups.setdefault(cid, []).append(_unit_pairs(vals))
    if not groups:
        return BootstrapCI(None, None, 0, 0, seed)
    by_case = clusters is not None and any(len(g) > 1 for g in groups.values())
    blocks = list(groups.values())
    rng = random.Random(seed)
    stats: list[float] = []
    dropped = 0
    k = len(blocks)
    for _ in range(resamples):
        o: dict[tuple[Hashable, Hashable], float] = {}
        n_units = 0
        for _i in range(k):
            block = blocks[rng.randrange(k)]
            n_units += len(block)
            for pairs in block:
                for key, w in pairs.items():
                    o[key] = o.get(key, 0.0) + w
        n_c: dict[Hashable, float] = {}
        for (c, _k2), w in o.items():
            n_c[c] = n_c.get(c, 0.0) + w
        a = _alpha_from(Coincidences(o, n_c, sum(n_c.values()), n_units), level, order)
        if a is None:
            dropped += 1
            continue
        stats.append(a)
    resampled = "cases" if by_case else "units"
    n_cases = k if by_case else None
    if not stats:
        return BootstrapCI(None, None, resamples, dropped, seed, resampled, n_cases)
    stats.sort()
    lo = stats[max(0, math.floor(0.025 * len(stats)))]
    hi = stats[min(len(stats) - 1, math.ceil(0.975 * len(stats)) - 1)]
    return BootstrapCI(lo, hi, resamples, dropped, seed, resampled, n_cases)
