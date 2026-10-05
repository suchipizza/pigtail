"""Pick the releases worth showing as timeline events."""

from __future__ import annotations

import re
from datetime import date, timedelta

from pigtail.domain.time import parse_dt
from pigtail.providers.github.client import Release

_VER = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


def _version(tag: str) -> tuple[int, int, int] | None:
    m = _VER.search(tag)
    return (int(m[1]), int(m[2]), int(m[3] or 0)) if m else None


def select_releases(releases: list[Release], episode_windows: list[tuple[date, date]], cap: int = 25) -> list[Release]:
    dated = [r for r in releases if parse_dt(r.published_at)]
    dated.sort(key=lambda r: r.published_at or "")
    if not dated:
        return []
    keep: dict[str, Release] = {}
    stable = [r for r in dated if not r.prerelease]
    if stable:
        keep[stable[0].tag] = stable[0]
        keep[stable[-1].tag] = stable[-1]
    prev: tuple[int, int, int] | None = None
    for r in stable:
        v = _version(r.tag)
        if v and (prev is None or v[0] > prev[0] or (v[0] == 0 and v[1] > prev[1])):
            keep[r.tag] = r
        if v:
            prev = v
    for r in dated:
        d = parse_dt(r.published_at)
        if d and any(s - timedelta(days=3) <= d.date() <= e for s, e in episode_windows):
            keep[r.tag] = r
    out = sorted(keep.values(), key=lambda r: r.published_at or "")
    if len(out) > cap:
        first, last = out[0], out[-1]
        major = [r for r in out[1:-1] if (_version(r.tag) or (0, 0, 0))[1:] == (0, 0)]
        rest = [r for r in out[1:-1] if r not in major]
        out = sorted(
            [first, *major[: cap - 2], *rest[: max(0, cap - 2 - len(major))], last],
            key=lambda r: r.published_at or "",
        )
    return out
