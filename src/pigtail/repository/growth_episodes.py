"""Detect star-growth episodes: periods with growth well above the project's recent baseline.

Deterministic and explainable. Detection never looks at events; association with events is
added afterwards and is capped at `weakly_associated` (timing only).
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


def detect_episodes(daily: list[tuple[date, int]], *, max_episodes: int = 8) -> list[DetectedEpisode]:
    """`daily` is a gap-free daily cumulative series (exact history)."""
    if len(daily) < 3:
        return []
    gains = [0] + [max(0, daily[i][1] - daily[i - 1][1]) for i in range(1, len(daily))]
    gains[0] = daily[0][1]
    total = daily[-1][1]
    min_abs = max(5, round(total * 0.004))
    spike = [False] * len(gains)
    baselines = [0.0] * len(gains)
    for i, g in enumerate(gains):
        window = gains[max(0, i - 28) : i]
        base = (sum(window) / len(window)) if window else 0.0
        baselines[i] = base
        if len(window) < 7:  # too little history for a baseline: only an unmistakable burst counts
            spike[i] = g >= max(min_abs * 3, 20)
        else:
            spike[i] = g >= min_abs and g >= 4 * base + 2
    episodes: list[DetectedEpisode] = []
    i = 0
    n = len(gains)
    while i < n:
        if not spike[i]:
            i += 1
            continue
        start = i
        end = i
        base = baselines[i]
        j = i + 1
        while j < n and j - end <= 3:
            if spike[j] or gains[j] > max(2.0 * base, min_abs / 2):
                end = j
            j += 1
            if end - start >= 20:
                break
        delta = sum(gains[start : end + 1])
        peak_idx = max(range(start, end + 1), key=lambda k: gains[k])
        episodes.append(
            DetectedEpisode(
                start=daily[start][0],
                end=daily[end][0],
                delta=delta,
                peak_day=daily[peak_idx][0],
                peak_value=gains[peak_idx],
                baseline_per_day=round(base, 2),
            )
        )
        i = end + 1
    floor = max(15, round(total * 0.015))
    episodes = [e for e in episodes if e.delta >= floor]
    episodes.sort(key=lambda e: -e.delta)
    return sorted(episodes[:max_episodes], key=lambda e: e.start)
