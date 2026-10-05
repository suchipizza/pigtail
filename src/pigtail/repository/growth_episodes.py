"""Detect star-growth episodes: periods with growth well above the project's recent baseline.

Deterministic and explainable. Detection never looks at events; association with events is
added afterwards and is capped at `weakly_associated` (timing only). Thresholds use the project's
size *at that time*, so early launch spikes of large projects are not hidden by today's total.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass
class DetectedEpisode:
    start: date
    end: date
    delta: int
    peak_day: date
    peak_value: int
    baseline_per_day: float
    excess: float = 0.0


def detect_episodes(daily: list[tuple[date, int]], *, max_episodes: int = 8) -> list[DetectedEpisode]:
    """`daily` is a gap-free daily cumulative series (exact history)."""
    if len(daily) < 3:
        return []
    gains = [daily[0][1]] + [max(0, daily[i][1] - daily[i - 1][1]) for i in range(1, len(daily))]
    n = len(gains)
    spike = [False] * n
    baselines = [0.0] * n
    for i, g in enumerate(gains):
        cum_prev = daily[i - 1][1] if i else 0
        window = gains[max(0, i - 28) : i]
        base = (sum(window) / len(window)) if window else 0.0
        baselines[i] = base
        floor = max(5, 0.002 * cum_prev)
        if len(window) < 7:  # too little history for a baseline: only an unmistakable burst counts
            spike[i] = g >= max(20, floor)
        else:
            spike[i] = g >= floor and g >= 4 * base + 2
    episodes: list[DetectedEpisode] = []
    i = 0
    while i < n:
        if not spike[i]:
            i += 1
            continue
        start = end = i
        base = baselines[i]
        tail_floor = max(2.0 * base, max(5, 0.002 * (daily[i - 1][1] if i else 0)) / 2)
        j = i + 1
        while j < n and j - end <= 3 and end - start < 20:
            if spike[j] or gains[j] > tail_floor:
                end = j
            j += 1
        delta = sum(gains[start : end + 1])
        days = end - start + 1
        excess = delta - base * days
        peak_idx = max(range(start, end + 1), key=lambda k: gains[k])
        cum_end = daily[end][1]
        if excess >= max(15, 0.005 * cum_end):
            episodes.append(
                DetectedEpisode(
                    start=daily[start][0],
                    end=daily[end][0],
                    delta=delta,
                    peak_day=daily[peak_idx][0],
                    peak_value=gains[peak_idx],
                    baseline_per_day=round(base, 2),
                    excess=excess,
                )
            )
        i = end + 1
    episodes.sort(key=lambda e: -e.excess)
    return sorted(episodes[:max_episodes], key=lambda e: e.start)
