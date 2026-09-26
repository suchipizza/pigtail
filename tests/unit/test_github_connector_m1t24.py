"""M1-T24 (ADR-032; TM-02, TM-33): GitHub connector, rate limits, budgets, parsing, gates.

Synthetic data only (tests/github_fake.py, tests/fixtures/github/). No network, no real token.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from pigtail.capture.snapshots import LocalSnapshotStore
from pigtail.connectors.base import ADR022_ENV, FetchError, PersonSourceHold, TokenBucket
from pigtail.connectors.github import (
    GitHubConnector,
    GitHubRepoEventsConnector,
    MissingToken,
    RateLimited,
    parse_star_history,
    parse_week_label,
)
from pigtail.connectors.github_budget import (
    Budget,
    BudgetConfig,
    BudgetExhausted,
    JobCaps,
    MemoryLedger,
    hour_of,
)
from pigtail.pseudonymize import Pseudonymizer
from tests.conftest import TEST_KEY
from tests.github_fake import NOW, TOKEN, FakeGitHub

FIX = Path(__file__).parents[1] / "fixtures" / "github"


class Sleeps(list[float]):
    def __call__(self, s: float) -> None:
        self.append(s)


def fast_limiters() -> dict[str, TokenBucket]:
    return {r: TokenBucket(1000, burst=1000) for r in ("core", "graphql", "search")}


def gh(
    tmp_path: Path,
    fake: FakeGitHub,
    *,
    token: str | None = TOKEN,
    budget: Budget | None = None,
    sleep: Sleeps | None = None,
    cls: type[Any] = GitHubConnector,
    env: dict[str, str] | None = None,
    **kw: Any,
) -> Any:
    sl = sleep if sleep is not None else Sleeps()
    return cls(
        store=LocalSnapshotStore(tmp_path / "snap"),
        http=fake.client(),
        env=env if env is not None else {},
        token=token,
        budget=budget or Budget(clock=lambda: NOW, sleep=sl),
        limiters=fast_limiters(),
        sleep=sl,
        clock=lambda: NOW,
        **kw,
    )


# --- token and headers ---------------------------------------------------------------------------
def test_m1_t24_refuses_live_calls_without_token(tmp_path):
    fake = FakeGitHub()
    c = gh(tmp_path, fake, token=None, env={})
    with pytest.raises(MissingToken):
        c.star_history("org-x/repo-1")
    with pytest.raises(MissingToken):
        c.graphql("query { rateLimit { cost } }")
    with pytest.raises(MissingToken):
        c.search_repositories("stars:1..2")
    assert fake.requests == []  # nothing was sent


def test_m1_t24_token_read_from_env_and_never_shown(tmp_path):
    fake = FakeGitHub()
    c = GitHubConnector(
        store=LocalSnapshotStore(tmp_path / "s"),
        http=fake.client(),
        env={"GITHUB_TOKEN": TOKEN},
        limiters=fast_limiters(),
        clock=lambda: NOW,
    )
    assert c.has_token and TOKEN not in repr(c)
    c.star_history("org-x/repo-1")
    req = fake.requests[0]
    assert req.headers["Authorization"] == f"Bearer {TOKEN}"
    assert req.headers["User-Agent"].startswith("pigtail/")
    assert req.headers["X-GitHub-Api-Version"] == "2026-03-10"
    assert req.headers["Accept"] == "application/vnd.github+json"


def test_m1_t24_terms_metadata_tm02_tm33():
    assert "TM-02" in GitHubConnector.terms.terms_basis
    assert "TM-33" in GitHubConnector.terms.terms_basis
    assert "TM-33" in GitHubRepoEventsConnector.terms.terms_basis
    assert GitHubConnector.retention_class == "project_level"
    assert GitHubRepoEventsConnector.retention_class == "person_level_30d"


# --- primary / secondary rate limits -------------------------------------------------------------
def test_m1_t24_primary_limit_waits_until_reset_then_retries(tmp_path):
    fake = FakeGitHub()
    reset = int((NOW + timedelta(seconds=120)).timestamp())
    fake.script = [
        httpx.Response(
            403,
            json={"message": "API rate limit exceeded"},
            headers={
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Limit": "5000",
                "X-RateLimit-Reset": str(reset),
                "X-RateLimit-Resource": "core",
            },
        )
    ]
    sleeps = Sleeps()
    budget = Budget(BudgetConfig(max_wait_seconds=600), clock=lambda: NOW, sleep=sleeps)
    c = gh(tmp_path, fake, sleep=sleeps, budget=budget)
    _, weeks = c.star_history("org-x/repo-1")
    assert weeks and len(fake.requests) == 2
    assert 120 <= sleeps[0] <= 122  # until X-RateLimit-Reset (+1 s)


def test_m1_t24_secondary_limit_retry_after_and_exponential_wait(tmp_path):
    fake = FakeGitHub()
    fake.script = [
        httpx.Response(429, headers={"Retry-After": "30"}),
        httpx.Response(403, json={"message": "You have exceeded a secondary rate limit"}),
        httpx.Response(403, json={"message": "You have exceeded a secondary rate limit"}),
    ]
    sleeps = Sleeps()
    c = gh(tmp_path, fake, sleep=sleeps)
    c.star_history("org-x/repo-1")
    assert sleeps[:3] == [30.0, 120.0, 240.0]  # Retry-After, then 60 s doubling per repeat
    assert len(fake.requests) == 4


def test_m1_t24_rate_limit_too_long_raises_and_plain_403_is_not_retried(tmp_path):
    fake = FakeGitHub()
    fake.script = [httpx.Response(429, headers={"Retry-After": "7200"})]
    c = gh(tmp_path, fake)
    with pytest.raises(RateLimited):
        c.star_history("org-x/repo-1")
    fake.script = [httpx.Response(403, json={"message": "Repository access blocked"})]
    with pytest.raises(FetchError) as e:
        c.star_history("org-x/repo-1")
    assert e.value.status == 403 and not isinstance(e.value, RateLimited)


def test_m1_t24_5xx_backoff(tmp_path):
    fake = FakeGitHub()
    fake.script = [httpx.Response(502), httpx.Response(503)]
    sleeps = Sleeps()
    c = gh(tmp_path, fake, sleep=sleeps)
    c.star_history("org-x/repo-1")
    assert len(fake.requests) == 3 and len(sleeps) == 2


# --- budgets -------------------------------------------------------------------------------------
def test_m1_t24_budget_hourly_cap_is_a_hard_stop(tmp_path):
    fake = FakeGitHub()
    ledger = MemoryLedger()
    budget = Budget(
        BudgetConfig(per_hour={"core": 2, "graphql": 10, "search": 10}), ledger, clock=lambda: NOW
    )
    c = gh(tmp_path, fake, budget=budget)
    c.star_history("org-x/repo-1", conditional=False)
    c.star_history("org-x/repo-2", conditional=False)
    with pytest.raises(BudgetExhausted) as e:
        c.star_history("org-x/repo-1", conditional=False)
    assert e.value.reason == "hourly_cap" and len(fake.requests) == 2
    assert ledger.used(hour_of(NOW), "core") == 2


def test_m1_t24_budget_shared_ledger_counts_other_processes(tmp_path):
    ledger = MemoryLedger()
    ledger.add(hour_of(NOW), "search", units=1260, requests=1260)  # spent by another job
    budget = Budget(BudgetConfig(), ledger, clock=lambda: NOW)
    fake = FakeGitHub()
    c = gh(tmp_path, fake, budget=budget)
    with pytest.raises(BudgetExhausted):
        c.search_repositories("created:2026-09-01T00:00:00Z..2026-09-02T00:00:00Z stars:1..*")
    assert fake.requests == []


def test_m1_t24_budget_job_cap_and_server_reserve(tmp_path):
    fake = FakeGitHub()
    budget = Budget(job=JobCaps({"core": 1}), clock=lambda: NOW)
    c = gh(tmp_path, fake, budget=budget)
    c.star_history("org-x/repo-1", conditional=False)
    with pytest.raises(BudgetExhausted) as e:
        c.star_history("org-x/repo-1", conditional=False)
    assert e.value.reason == "job_cap"

    # server says 1,000 of 5,000 left (< 30 % reserve) and the reset is an hour away: stop
    fake2 = FakeGitHub()
    fake2.remaining["core"] = 1000
    b2 = Budget(clock=lambda: NOW)
    c2 = gh(tmp_path, fake2, budget=b2)
    c2.star_history("org-x/repo-1", conditional=False)
    with pytest.raises(BudgetExhausted) as e2:
        c2.star_history("org-x/repo-1", conditional=False)
    assert e2.value.reason == "server_reserve" and len(fake2.requests) == 1


def test_m1_t24_budget_sleeps_when_reset_is_near():
    sleeps = Sleeps()
    b = Budget(clock=lambda: NOW, sleep=sleeps)
    b.after(
        "core",
        {
            "X-RateLimit-Limit": "5000",
            "X-RateLimit-Remaining": "10",
            "X-RateLimit-Reset": str(int((NOW + timedelta(seconds=30)).timestamp())),
        },
        units=1,
    )
    b.before("core")
    assert sleeps == [31.0]


def test_m1_t24_budget_config_from_env_validates():
    cfg = BudgetConfig.from_env({"GITHUB_BUDGET_CORE_PER_HOUR": "1000"})
    assert cfg.per_hour["core"] == 1000 and cfg.per_hour["graphql"] == 3500
    assert cfg.per_hour["search"] == 1260  # 70 % of 1,800/h
    with pytest.raises(ValueError):
        BudgetConfig.from_env({"GITHUB_BUDGET_CORE_PER_HOUR": "6000"})  # above GitHub's limit
    with pytest.raises(ValueError):
        BudgetConfig.from_env({"GITHUB_BUDGET_RESERVE_FRACTION": "1.5"})


# --- ETag / 304 ----------------------------------------------------------------------------------
def test_m1_t24_conditional_request_304_is_free_and_stores_nothing(tmp_path):
    fake = FakeGitHub()
    ledger = MemoryLedger()
    c = gh(tmp_path, fake, budget=Budget(ledger=ledger, clock=lambda: NOW))
    c1, w1 = c.star_history("org-x/repo-1")
    c2, w2 = c.star_history("org-x/repo-1")
    assert c1.status == 200 and c2.status == 304 and c2.fetched is None
    assert fake.requests[1].headers["If-None-Match"] == c.cache.get(c1.url).etag
    assert w2 == w1  # re-read from the stored snapshot
    row = ledger.rows[(hour_of(NOW), "core")]
    assert row["units"] == 1 and row["requests"] == 2 and row["not_modified"] == 1


# --- GraphQL -------------------------------------------------------------------------------------
REPO_FIELDS = "fragment R on Repository { databaseId nameWithOwner stargazerCount forkCount }"


def batch_query(n_nodes: int, n_names: int) -> str:
    """A small aliased GraphQL query (the generic `graphql()` call; the watch-list batches that
    built these were removed in M11)."""
    parts = [f"r{i}: node(id: $i{i}) {{ ...R }}" for i in range(n_nodes)]
    parts += [
        f"r{i}: repository(owner: $o{i}, name: $n{i}) {{ ...R }}"
        for i in range(n_nodes, n_nodes + n_names)
    ]
    decl = [f"$i{i}: ID!" for i in range(n_nodes)]
    decl += [f"$o{i}: String!, $n{i}: String!" for i in range(n_nodes, n_nodes + n_names)]
    return (
        f"query({', '.join(decl)}) {{ {' '.join(parts)} rateLimit {{ cost remaining limit "
        f"resetAt }} }} {REPO_FIELDS}"
    )


def test_m1_t24_graphql_cost_recording_and_not_found(tmp_path):
    fake = FakeGitHub()
    fake.graphql_cost = 3
    ledger = MemoryLedger()
    c = gh(tmp_path, fake, budget=Budget(ledger=ledger, clock=lambda: NOW))
    q = batch_query(1, 2)
    v = {"i0": "R_fake_7000001", "o1": "org-x", "n1": "repo-2", "o2": "org-x", "n2": "nope"}
    assert "org-x" not in q  # values travel only as variables
    res = c.graphql(q, v)
    assert res.cost == 3 and res.data["r0"]["stargazerCount"] == 4000
    assert res.data["r2"] is None and res.errors[0]["type"] == "NOT_FOUND"
    assert ledger.used(hour_of(NOW), "graphql") == 3  # estimate 1, corrected to the real cost
    assert json.loads(fake.requests[0].content)["variables"] == v


def test_m1_t24_graphql_rate_limited_error_waits(tmp_path):
    fake = FakeGitHub()
    reset = (NOW + timedelta(seconds=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fake.script = [
        httpx.Response(
            200,
            json={
                "data": {"rateLimit": {"cost": 1, "remaining": 0, "limit": 5000, "resetAt": reset}},
                "errors": [{"type": "RATE_LIMITED", "message": "x"}],
            },
        )
    ]
    sleeps = Sleeps()
    budget = Budget(clock=lambda: NOW, sleep=sleeps)
    c = gh(tmp_path, fake, sleep=sleeps, budget=budget)
    res = c.graphql(batch_query(0, 1), {"o0": "org-x", "n0": "repo-1"})
    assert res.data["r0"]["databaseId"] == 7000001
    assert 41.0 in sleeps


# --- search / star history parsing ---------------------------------------------------------------
def test_m1_t24_search_paging_capped_at_1000_results(tmp_path):
    c = gh(tmp_path, FakeGitHub())
    with pytest.raises(ValueError):
        c.search_repositories("stars:1..*", page=11)
    with pytest.raises(ValueError):
        c.search_repositories("stars:1..*", per_page=101)


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("2026-09-20", date(2026, 9, 20)),
        ("2026-09-20T00:00:00-07:00", date(2026, 9, 20)),  # the endpoint's date, not UTC's
        (int(datetime(2026, 9, 20, 7, tzinfo=UTC).timestamp()), date(2026, 9, 20)),
    ],
)
def test_m1_t24_star_history_day_labels_kept_as_given(label, expected):
    assert parse_week_label(label) == expected
    weeks = parse_star_history(
        json.dumps([{"week": label, "total": 7, "days": [1, 1, 1, 1, 1, 1, 1]}]).encode()
    )
    assert weeks[0].week_start == expected and weeks[0].week_label == str(label)


def test_m1_t24_star_history_validation(tmp_path):
    with pytest.raises(ValueError):
        parse_star_history(b'[{"week": "2026-09-20", "total": 1, "days": [1]}]')
    c = gh(tmp_path, FakeGitHub())
    with pytest.raises(ValueError):
        c.star_history("org-x/repo-1", per_page=31)
    with pytest.raises(ValueError):
        c.star_history("org-x/repo-1", page=101)


# --- per-repo events connector (TM-33, ADR-022, CB-23) -------------------------------------------
def test_m1_t24_events_connector_off_by_default_and_held_by_adr022(tmp_path):
    pz = Pseudonymizer(TEST_KEY)
    c = gh(tmp_path, FakeGitHub(), cls=GitHubRepoEventsConnector, pseudonymizer=pz, env={})
    assert not c.enabled
    with pytest.raises(PersonSourceHold):
        gh(
            tmp_path,
            FakeGitHub(),
            cls=GitHubRepoEventsConnector,
            pseudonymizer=pz,
            env={"PIGTAIL_ENABLE_GITHUB_EVENTS": "1"},
        )
    ok = gh(
        tmp_path,
        FakeGitHub(),
        cls=GitHubRepoEventsConnector,
        pseudonymizer=pz,
        env={"PIGTAIL_ENABLE_GITHUB_EVENTS": "1", ADR022_ENV: "1"},
    )
    assert ok.enabled and ok.person_level_hold


def test_m1_t24_events_parse_keeps_only_watch_and_fork_pseudonymized(tmp_path):
    pz = Pseudonymizer(TEST_KEY)
    fake = FakeGitHub()
    fake.events["org-x/repo-1"] = json.loads((FIX / "events_page.json").read_text())
    c = gh(
        tmp_path,
        fake,
        cls=GitHubRepoEventsConnector,
        pseudonymizer=pz,
        env={"PIGTAIL_ENABLE_GITHUB_EVENTS": "1", ADR022_ENV: "1"},
    )
    got = c.repo_events("org-x/repo-1")
    assert got.fetched is not None and got.poll_interval_s == 60
    recs = list(c.records(got.fetched.data, got.fetched.meta))
    assert {r["type"] for r in recs} == {"WatchEvent", "ForkEvent"}  # CB-23
    assert len(recs) == 4
    text = json.dumps(recs)
    assert "ghuser" not in text and "helper-app" not in text and "payload" not in text
    bot = next(r for r in recs if r["is_bot"])
    assert bot["actor"] is None  # bots are neither kept nor hashed
    assert all(r["actor"] is None or r["actor"].startswith("p_") for r in recs)
    assert got.fetched.evidence.retention_class == "person_level_30d"
    with pytest.raises(ValueError):
        c.repo_events("org-x/repo-1", page=4)  # the window is 300 events


def test_m1_t24_github_connector_registered_and_env_required():
    from pigtail.connectors.registry import CONNECTORS

    assert CONNECTORS["github"] is GitHubConnector
    assert CONNECTORS["github_events"] is GitHubRepoEventsConnector
    assert GitHubConnector.requires_env == ("GITHUB_TOKEN",)
