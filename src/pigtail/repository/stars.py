"""Star-series utilities: daily series, interpolation, snapshot selection."""

from __future__ import annotations

from bisect import bisect_right
from datetime import date, timedelta


def daily_series(points: list[tuple[date, int]], end: date) -> list[tuple[date, int]]:
    """Expand exact (day, cumulative) points into a gap-free daily cumulative series."""
    if not points:
        return []
    out = []
    i = 0
    cur = 0
    d = points[0][0]
    while d <= end:
        while i < len(points) and points[i][0] <= d:
            cur = points[i][1]
            i += 1
        out.append((d, cur))
        d += timedelta(days=1)
    return out


def value_at(points: list[tuple[date, int]], d: date, *, interpolate: bool = False) -> int | None:
    """Cumulative stars at end of day d. Exact series: last known value. Sampled: optional interpolation."""
    if not points or d < points[0][0]:
        return 0 if points and d < points[0][0] and not interpolate else None
    days = [p[0] for p in points]
    i = bisect_right(days, d) - 1
    if i >= len(points) - 1 or not interpolate:
        return points[min(i, len(points) - 1)][1]
    (d0, v0), (d1, v1) = points[i], points[i + 1]
    span = (d1 - d0).days or 1
    return round(v0 + (v1 - v0) * (d - d0).days / span)


def snapshot_days(points: list[tuple[date, int]], end: date, quality: str) -> list[tuple[date, int]]:
    """Points to store as MetricSnapshots: daily for short histories, weekly otherwise; sampled as-is."""
    if not points:
        return []
    if quality != "exact":
        return points
    series = daily_series(points, end)
    if len(series) <= 1100:
        return series
    weekly = series[::7]
    if weekly[-1][0] != series[-1][0]:
        weekly.append(series[-1])
    return weekly
