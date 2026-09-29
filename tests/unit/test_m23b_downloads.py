"""M23b (ADR-090; ADR-088.3, ADR-083.9): npm and PyPI download connectors, the repo→package
mapping and the exploratory download windows. Contract tests on the recorded-shape fixtures
(synthetic values), fakes for the APIs; no network."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from pigtail.briefs.downloads import (
    METRICS,
    SECONDARY_METRICS,
    Coverage,
    RepoFiles,
    download_first_day,
    github_repo_of,
    map_npm,
    map_pypi,
    parse_package_json,
    parse_pyproject,
    parse_setup_cfg,
    points_to,
    window_value,
    workspace_paths,
)
from pigtail.briefs.selection import Anchor
from pigtail.capture.snapshots import LocalSnapshotStore
from pigtail.connectors.base import USER_AGENT, Clearance, CostEvent, RetryPolicy
from pigtail.connectors.downloads import (
    NPM_MAX_RANGE_DAYS,
    DownloadsParseError,
    NpmDownloadsConnector,
    PypiStatsConnector,
    chunk_ranges,
    parse_npm_latest,
    parse_npm_range,
    parse_pypistats_overall,
    pep503,
    valid_npm_name,
)
from tests.downloads_fake import FakeRegistries, fast, npm_latest, package_json

FIX = Path(__file__).parents[1] / "fixtures" / "downloads"


# --- contract: recorded-shape fixtures -----------------------------------------------------------
def test_npm_range_contract_m23b() -> None:
    s = parse_npm_range((FIX / "npm_range.json").read_bytes())
    assert s.package == "fake-pkg-a"
    assert len(s.days) == 5 and s.days[date(2026, 8, 3)] == 0
    assert sum(s.days.values()) == 813


def test_npm_latest_contract_reads_repository_only_m23b() -> None:
    meta = parse_npm_latest((FIX / "npm_latest.json").read_bytes())
    assert meta == {
        "name": "fake-pkg-a",
        "repository_url": "git+https://github.com/org-x/repo-npm.git",
        "repository_directory": "packages/core",
    }
    assert "npmuser001" not in json.dumps(meta) and "@" not in json.dumps(meta)


def test_pypistats_contract_without_mirrors_only_m23b() -> None:
    s = parse_pypistats_overall((FIX / "pypistats_overall.json").read_bytes())
    assert s.package == "fake-py-b"
    assert s.days == {date(2026, 8, 1): 100, date(2026, 8, 2): 40, date(2026, 8, 4): 7}


@pytest.mark.parametrize(
    "raw",
    [b"not json", b"{}", b'{"downloads":[{"day":"x","downloads":1}]}',
     b'{"downloads":[{"day":"2026-01-01","downloads":-1}]}'],
)  # fmt: skip
def test_npm_range_rejects_bad_shapes_m23b(raw: bytes) -> None:
    with pytest.raises(DownloadsParseError):
        parse_npm_range(raw)


def test_pypistats_rejects_bad_shapes_m23b() -> None:
    with pytest.raises(DownloadsParseError):
        parse_pypistats_overall(b'{"data": 3}')
    with pytest.raises(DownloadsParseError):
        parse_pypistats_overall((FIX / "npm_not_found.json").read_bytes())


def test_chunk_ranges_respects_the_18_month_limit_m23b() -> None:
    ch = chunk_ranges(date(2024, 1, 1), date(2026, 9, 1))
    assert ch[0][0] == date(2024, 1, 1) and ch[-1][1] == date(2026, 9, 1)
    assert all((b - a).days + 1 <= NPM_MAX_RANGE_DAYS for a, b in ch)
    assert all(ch[i + 1][0] == ch[i][1] + timedelta(days=1) for i in range(len(ch) - 1))
    assert chunk_ranges(date(2026, 1, 2), date(2026, 1, 1)) == []


def test_names_m23b() -> None:
    assert valid_npm_name("@scope/pkg") and valid_npm_name("left-pad")
    assert not valid_npm_name("Bad Name") and not valid_npm_name("../x")
    assert pep503("Foo_Bar.baz") == "foo-bar-baz"


# --- connectors: terms, UA, limits, retries, snapshot before parse --------------------------------
def test_connector_terms_metadata_m23b() -> None:
    assert NpmDownloadsConnector.terms.terms_basis == "TM-08"
    assert NpmDownloadsConnector.terms.clearance is Clearance.CLEARED
    assert PypiStatsConnector.terms.terms_basis == "TM-35"
    assert PypiStatsConnector.terms.clearance is Clearance.CLEARED_WITH_CONDITIONS
    # pypistats: at most one request every 20 s (etiquette; its 429 has no Retry-After)
    eff = PypiStatsConnector.rate_per_second * (1 - PypiStatsConnector.safety_margin)
    assert eff <= 1 / 20 + 1e-9
    assert NpmDownloadsConnector.rate_per_second * (1 - NpmDownloadsConnector.safety_margin) <= 1


def test_npm_fetch_snapshots_with_user_agent_and_cost_m23b(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    body = (FIX / "npm_range.json").read_bytes()

    def h(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, content=body)

    events: list[CostEvent] = []
    store = LocalSnapshotStore(tmp_path)
    c = NpmDownloadsConnector(
        store=store, http=httpx.Client(transport=httpx.MockTransport(h)), limiter=fast(),
        cost_hook=events.append, env={},
    )  # fmt: skip
    f = c.downloads_range("@scope/fake-pkg", date(2026, 8, 1), date(2026, 8, 5))
    assert f is not None and store.get(f.content_hash) == body
    assert seen[0].headers["User-Agent"] == USER_AGENT
    assert seen[0].url.raw_path.decode() == "/downloads/range/2026-08-01:2026-08-05/@scope/fake-pkg"
    assert f.evidence.terms_basis == "TM-08" and f.evidence.retention_class == "project_level"
    assert events and events[0].usd == 0.0


def test_npm_latest_is_person_level_and_404_is_none_m23b(tmp_path: Path) -> None:
    fake = FakeRegistries(npm_latest={"fake-a": npm_latest("fake-a", "github:org-x/repo-a")})
    c = fake.npm(LocalSnapshotStore(tmp_path))
    f = c.latest("fake-a")
    assert f is not None and f.evidence.retention_class == "person_level_24m"
    assert c.latest("fake-missing") is None
    assert c.downloads_range("fake-missing", date(2026, 8, 1), date(2026, 8, 2)) is None


def test_pypistats_429_backs_off_then_succeeds_m23b(tmp_path: Path) -> None:
    fake = FakeRegistries(pypi={"fake-py-b": {date(2026, 8, 1): 3}})
    fake.script = [httpx.Response(429, text="429 RATE LIMIT EXCEEDED")] * 2
    slept: list[float] = []
    c = PypiStatsConnector(
        store=LocalSnapshotStore(tmp_path), http=fake.client(), limiter=fast(),
        sleep=slept.append, env={},
    )  # fmt: skip
    f = c.overall("Fake_Py.B")
    assert f is not None
    assert parse_pypistats_overall(f.data).days == {date(2026, 8, 1): 3}
    assert len(slept) == 2  # two backoffs, no Retry-After header
    assert fake.log[-1].endswith("/api/packages/fake-py-b/overall?mirrors=false")
    assert isinstance(c.retry, RetryPolicy) and c.retry.base_delay >= 30


def test_connectors_can_be_turned_off_m23b() -> None:
    env = {"PIGTAIL_CONNECTOR_PYPISTATS_ENABLED": "false"}
    assert PypiStatsConnector.enabled_from_env(env) is False
    assert NpmDownloadsConnector.enabled_from_env({}) is True


# --- mapping (pure parts) ------------------------------------------------------------------------
@pytest.mark.parametrize(
    "url",
    [
        "git+https://github.com/Org-X/repo-a.git",
        "https://github.com/org-x/repo-a",
        "git://github.com/org-x/repo-a.git",
        "git+ssh://git@github.com/org-x/repo-a.git",
        "git@github.com:org-x/repo-a.git",
        "github:org-x/repo-a",
        "org-x/repo-a",
        "https://github.com/org-x/repo-a/tree/main/packages/core",
    ],
)
def test_repository_forms_point_back_m23b(url: str) -> None:
    assert points_to(url, "org-x/repo-a")


@pytest.mark.parametrize(
    "url", ["https://gitlab.com/org-x/repo-a", "https://github.com/org-x/repo-b", "", None,
            "https://org-x.github.io/repo-a", "https://example.org/org-x/repo-a"],
)  # fmt: skip
def test_repository_forms_not_pointing_back_m23b(url: str | None) -> None:
    assert not points_to(url, "org-x/repo-a")
    assert github_repo_of("gitlab:org-x/repo-a") is None


def test_package_json_and_workspaces_m23b() -> None:
    pj = parse_package_json(package_json("root", private=True, workspaces={"packages": ["p/*"]}))
    assert pj.name == "root" and pj.private and pj.workspaces == ("p/*",)
    plain, parents, other = workspace_paths(["packages/*", "./tools/cli", "!skip", "a/**/b"])
    assert plain == ["tools/cli"] and parents == ["packages"] and other == ["a/**/b"]
    with pytest.raises(DownloadsParseError):
        parse_package_json(b"[1]")


def test_python_manifests_m23b() -> None:
    pp = parse_pyproject(
        b'[project]\nname = "Fake_Py"\n[project.urls]\nSource = "https://github.com/org-x/py"\n'
    )
    assert pp.name == "Fake_Py" and pp.urls == ("https://github.com/org-x/py",)
    po = parse_pyproject(
        b'[tool.poetry]\nname = "fake-po"\nrepository = "https://github.com/org-x/po"\n'
    )
    assert po.name == "fake-po" and "https://github.com/org-x/po" in po.urls
    dyn = parse_pyproject(b'[project]\ndynamic = ["name"]\n')
    assert dyn.name is None and dyn.dynamic_name
    cfg = parse_setup_cfg(
        b"[metadata]\nname = fake-cfg\nurl = https://example.org\nproject_urls =\n"
        b"    Source = https://github.com/org-x/cfg\n"
    )
    assert cfg.name == "fake-cfg" and "https://github.com/org-x/cfg" in cfg.urls
    with pytest.raises(DownloadsParseError):
        parse_pyproject(b"[project\n")


# --- mapping against the fakes -------------------------------------------------------------------
def _files(fake: FakeRegistries, store: LocalSnapshotStore, dropped: list[str]) -> RepoFiles:
    return RepoFiles(fake.github(store), lambda f: dropped.append(f.content_hash))


def test_npm_root_package_confirmed_and_raw_dropped_m23b(tmp_path: Path) -> None:
    fake = FakeRegistries(
        files={"org-x/repo-a": {"package.json": package_json("fake-a")}},
        npm_latest={"fake-a": npm_latest("fake-a", "git+https://github.com/org-x/repo-a.git")},
    )
    store = LocalSnapshotStore(tmp_path)
    dropped: list[str] = []
    m = map_npm(_files(fake, store, dropped), fake.npm(store), "org-x/repo-a", None,
                lambda f: dropped.append(f.content_hash))  # fmt: skip
    assert m.status == "mapped" and m.package == "fake-a"
    kinds = {e.kind: e.raw for e in m.evidence}
    assert kinds == {"manifest": "dropped", "registry_manifest": "dropped"}
    assert len(dropped) == 2  # both person-level documents dropped after parsing (CB-24)


@pytest.mark.parametrize(
    ("files", "latest", "status", "reason"),
    [
        ({}, {}, "not_applicable", "no_manifest"),
        ({"package.json": package_json("fake-a")}, {}, "not_applicable", "not_published"),
        ({"package.json": package_json("fake-a", private=True)}, {}, "not_applicable",
         "no_publishable_package"),
        ({"package.json": package_json("fake-a")},
         {"fake-a": npm_latest("fake-a", "https://github.com/someone-else/fork")},
         "unknown", "repository_mismatch"),
        ({"package.json": package_json("fake-a")}, {"fake-a": npm_latest("fake-a", None)},
         "unknown", "no_repository_field"),
        ({"package.json": "{not json"}, {}, "unknown", "manifest_unparseable"),
    ],
)  # fmt: skip
def test_npm_mapping_outcomes_m23b(
    tmp_path: Path, files: dict[str, str], latest: dict[str, object], status: str, reason: str
) -> None:
    fake = FakeRegistries(files={"org-x/repo-a": files}, npm_latest=latest)  # type: ignore[arg-type]
    store = LocalSnapshotStore(tmp_path)
    m = map_npm(_files(fake, store, []), fake.npm(store), "org-x/repo-a", None, lambda f: None)
    assert (m.status, m.reason) == (status, reason)


def test_npm_workspaces_single_and_ambiguous_m23b(tmp_path: Path) -> None:
    url = "git+https://github.com/org-x/mono.git"
    files = {
        "package.json": package_json("mono-root", private=True, workspaces=["packages/*"]),
        "packages/core/package.json": package_json("fake-core"),
        "packages/ui/package.json": package_json("fake-ui", private=True),
    }
    fake = FakeRegistries(
        files={"org-x/mono": files},
        dirs={"org-x/mono": {"packages": ["core", "ui"]}},
        npm_latest={"fake-core": npm_latest("fake-core", url)},
    )
    store = LocalSnapshotStore(tmp_path)
    m = map_npm(_files(fake, store, []), fake.npm(store), "org-x/mono", None, lambda f: None)
    assert m.status == "mapped" and m.package == "fake-core"
    assert any(e.kind == "directory" and e.raw == "kept" for e in m.evidence)
    # two published, confirmed workspace packages and no root package: ambiguous
    files["packages/ui/package.json"] = package_json("fake-ui")
    fake.npm_latest["fake-ui"] = npm_latest("fake-ui", url)
    m2 = map_npm(_files(fake, store, []), fake.npm(store), "org-x/mono", None, lambda f: None)
    assert (m2.status, m2.reason) == ("unknown", "ambiguous_multiple_packages")
    assert {p["check"] for p in m2.packages} == {"confirmed"}


def test_npm_root_wins_over_workspaces_m23b(tmp_path: Path) -> None:
    url = "https://github.com/org-x/mono"
    fake = FakeRegistries(
        files={"org-x/mono": {
            "package.json": package_json("fake-root", workspaces=["packages/a", "packages/b"]),
            "packages/a/package.json": package_json("fake-wa"),
            "packages/b/package.json": package_json("fake-wb"),
        }},
        npm_latest={n: npm_latest(n, url) for n in ("fake-root", "fake-wa", "fake-wb")},
    )  # fmt: skip
    store = LocalSnapshotStore(tmp_path)
    m = map_npm(_files(fake, store, []), fake.npm(store), "org-x/mono", None, lambda f: None)
    assert m.status == "mapped" and m.package == "fake-root" and len(m.packages) == 3


def test_pypi_mapping_m23b(tmp_path: Path) -> None:
    store = LocalSnapshotStore(tmp_path)
    ok = FakeRegistries(files={"org-x/py": {"pyproject.toml": (
        '[project]\nname = "Fake.Py"\nauthors = [{name = "pyuser001"}]\n'
        '[project.urls]\nRepository = "https://github.com/org-x/py.git"\n')}})  # fmt: skip
    m = map_pypi(_files(ok, store, []), "org-x/py", None, "Python")
    assert m.status == "mapped" and m.package == "fake-py"
    assert [e.raw for e in m.evidence] == ["dropped"]
    no_url = FakeRegistries(files={"org-x/py": {"setup.cfg": "[metadata]\nname = fake-cfg\n"}})
    m = map_pypi(_files(no_url, store, []), "org-x/py", None, "Python")
    assert (m.status, m.reason) == ("unknown", "no_url_to_repo")
    none = FakeRegistries()
    assert map_pypi(_files(none, store, []), "org-x/js", None, "TypeScript").status == (
        "not_applicable"
    )
    m = map_pypi(_files(none, store, []), "org-x/py", None, "Python")
    assert (m.status, m.reason) == ("unknown", "no_static_python_manifest")


# --- windows -------------------------------------------------------------------------------------
def test_download_first_day_uses_utc_days_m23b() -> None:
    late = Anchor("launch", datetime(2026, 8, 1, 3, 30, tzinfo=UTC), "hour", "show_hn")
    assert download_first_day(late) == date(2026, 8, 1)  # stars would use 31 Jul (Pacific)
    day = Anchor("burst", datetime(2026, 8, 1, 7, 0, tzinfo=UTC), "day", "velocity-v0")
    assert download_first_day(day) == date(2026, 8, 1)


def test_metric_names_m23b() -> None:
    assert METRICS["npm"] == ("adopt.npm_downloads_launch@0-2", "adopt.npm_downloads_follow@3-30")
    assert METRICS["pypi"] == (
        "adopt.pypi_downloads_launch@0-2",
        "adopt.pypi_downloads_follow@3-30",
    )
    assert len(SECONDARY_METRICS) == 4


def _series(first: date, n: int, per_day: int = 10) -> dict[date, int]:
    return {first + timedelta(days=i): per_day for i in range(n)}


def test_window_values_m23b() -> None:
    first = date(2026, 6, 1)
    cov = Coverage(date(2015, 1, 10), date(2026, 9, 27))
    s = _series(first, 30)
    launch = window_value(s, first, 0, 3, date(2026, 9, 1), cov)
    follow = window_value(s, first, 3, 30, date(2026, 9, 1), cov)
    assert (launch["status"], launch["value"]) == ("observed", 30.0)
    assert (follow["status"], follow["value"]) == ("observed", 270.0)
    assert follow["window"] == {"first_day": "2026-06-01", "start": "2026-06-04",
                                "end": "2026-06-30"}  # fmt: skip
    assert launch["coverage"]["coverage_start"] == "2015-01-10"
    # pending until the window's end + settle lag (3 d)
    assert window_value(s, first, 3, 30, date(2026, 7, 3), cov)["status"] == "pending"
    assert window_value(s, first, 3, 30, date(2026, 7, 4), cov)["status"] == "observed"
    # a source gap day inside the window
    gap = Coverage(date(2015, 1, 10), date(2026, 9, 27), frozenset({first + timedelta(days=5)}))
    g = window_value(s, first, 3, 30, date(2026, 9, 1), gap)
    assert (g["status"], g["reason"]) == ("unknown", "source_gap_day")
    assert window_value(s, first, 0, 3, date(2026, 9, 1), gap)["status"] == "observed"
    # a missing day: npm has explicit zeros, so it's incomplete
    s2 = dict(s)
    del s2[first + timedelta(days=1)]
    assert window_value(s2, first, 0, 3, date(2026, 9, 1), cov)["reason"] == "incomplete_series"


def test_window_values_pypistats_coverage_m23b() -> None:
    cov = Coverage(date(2026, 4, 1), date(2026, 9, 27), zero_fill=True)
    first = date(2026, 3, 30)  # launch before the retained history
    r = window_value({}, first, 0, 3, date(2026, 9, 28), cov)
    assert (r["status"], r["reason"]) == ("unknown", "outside_source_history")
    assert r["coverage"]["coverage_start"] == "2026-04-01"
    first = date(2026, 5, 1)
    r = window_value({first: 4, first + timedelta(days=2): 1}, first, 0, 3, date(2026, 9, 28), cov)
    assert (r["status"], r["value"]) == ("observed", 5.0)
    assert r["coverage"]["zero_filled_days"] == 1 and r["coverage"]["missing_day_means_zero"]
