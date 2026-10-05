from datetime import date, timedelta

from pigtail.providers.github.client import Release
from pigtail.repository.growth_episodes import detect_episodes
from pigtail.repository.launch_episodes import cluster_launches, window_days
from pigtail.repository.releases import select_releases
from pigtail.repository.stars import daily_series, snapshot_days, value_at


def series(gains: list[int], start=date(2024, 1, 1)):
    cum, out = 0, []
    for i, g in enumerate(gains):
        cum += g
        out.append((start + timedelta(days=i), cum))
    return out


def test_detects_spike_and_ignores_steady_growth():
    gains = [3] * 60 + [200, 150, 40] + [3] * 60
    eps = detect_episodes(series(gains))
    assert len(eps) == 1
    ep = eps[0]
    assert ep.start == date(2024, 1, 1) + timedelta(days=60)
    assert ep.delta >= 350
    assert detect_episodes(series([5] * 200)) == []


def test_daily_series_fills_gaps_and_value_at():
    pts = [(date(2024, 1, 1), 1), (date(2024, 1, 4), 5)]
    d = daily_series(pts, date(2024, 1, 5))
    assert [v for _, v in d] == [1, 1, 1, 5, 5]
    assert value_at(pts, date(2024, 1, 3)) == 1
    assert (
        value_at(pts, date(2024, 1, 3), interpolate=True) == 4 or value_at(pts, date(2024, 1, 3), interpolate=True) == 3
    )


def test_snapshot_days_weekly_for_long_histories():
    pts = series([1] * 2000)
    snaps = snapshot_days(pts, pts[-1][0], "exact")
    assert len(snaps) < 400 and snaps[-1] == pts[-1]


def test_launch_clusters_need_a_strong_event():
    d = date(2024, 5, 1)
    ev = [
        ("r1", "release", d),
        ("h1", "hacker_news_post", d + timedelta(days=1)),
        ("s1", "show_hn", d + timedelta(days=30)),
        ("r2", "release", d + timedelta(days=31)),
    ]
    cl = cluster_launches(ev)
    assert len(cl) == 1 and cl[0].event_ids == ["s1", "r2"]
    cl2 = cluster_launches(ev, notable={"h1"})
    assert len(cl2) == 2


def test_windows_do_not_extend_past_today():
    w = window_days(date(2026, 10, 1), date(2026, 10, 5))
    labels = [x for x, _ in w]
    assert labels == ["before", "+24h", "+48h"]


def test_release_selection_keeps_majors_and_episode_releases():
    rs = [
        Release(
            tag=t,
            name=None,
            published_at=f"2024-0{m}-01T00:00:00Z",
            url="",
            prerelease=False,
            body_excerpt=None,
            api_path=f"$[{i}]",
        )
        for i, (t, m) in enumerate(
            [("v0.1.0", 1), ("v0.1.1", 2), ("v0.2.0", 3), ("v0.2.1", 4), ("v1.0.0", 5), ("v1.0.1", 6)]
        )
    ]
    sel = [r.tag for r in select_releases(rs, [(date(2024, 4, 1), date(2024, 4, 2))])]
    assert "v0.1.0" in sel and "v0.2.0" in sel and "v1.0.0" in sel and "v1.0.1" in sel and "v0.2.1" in sel
    assert "v0.1.1" not in sel
