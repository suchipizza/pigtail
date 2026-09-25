"""M1-T23: `pigtail doctor` reports the HN, GitHub-events and ADR-022 flag states."""

from __future__ import annotations

import json

import pytest

from pigtail.cli import main
from pigtail.config import Settings
from pigtail.connectors.base import ADR022_ENV
from pigtail.privacy.doctor import exit_code, run_checks, source_flag_checks

KEY = "k" * 40


def flags(env: dict[str, str]) -> dict[str, tuple[str, str]]:
    s = Settings.from_env({"PSEUDONYM_KEY": KEY})
    return {c.name: (c.status, c.detail) for c in source_flag_checks(s, env)}


def test_m1_t23_doctor_defaults_hold_person_sources_rank_poller_on():
    out = flags({})
    assert out["adr022_person_sources"][0] == "ok" and "held" in out["adr022_person_sources"][1]
    assert out["hn_sources"] == ("ok", "hn_ranks on, hn_firebase off, hn_algolia off")
    assert out["github_events"][0] == "ok" and "off" in out["github_events"][1]


def test_m1_t23_doctor_person_source_without_adr022_flag_fails():
    out = flags({"PIGTAIL_ENABLE_HN": "1", "PIGTAIL_ENABLE_GITHUB_EVENTS": "1"})
    assert out["hn_sources"][0] == "fail" and ADR022_ENV in out["hn_sources"][1]
    assert out["github_events"][0] == "fail"
    checks = run_checks(
        Settings.from_env({"PSEUDONYM_KEY": KEY}),
        db_check=False,
        env={"PIGTAIL_ENABLE_HN": "1"},
    )
    assert exit_code([c for c in checks if c.name == "hn_sources"]) == 1


def test_m1_t23_doctor_adr022_flag_set_warns_and_events_need_token():
    env = {ADR022_ENV: "1", "PIGTAIL_ENABLE_HN": "1", "PIGTAIL_ENABLE_GITHUB_EVENTS": "1"}
    out = flags(env)
    assert out["adr022_person_sources"][0] == "warn"
    assert "CB-12" in out["adr022_person_sources"][1]
    assert out["hn_sources"][0] == "ok" and "mention capture on" in out["hn_sources"][1]
    assert out["github_events"][0] == "warn" and "GITHUB_TOKEN" in out["github_events"][1]
    out = flags({**env, "GITHUB_TOKEN": "not-a-real-token"})
    assert out["github_events"][0] == "ok" and "16 d" in out["github_events"][1]
    assert "not-a-real-token" not in json.dumps(out)  # never prints secrets


@pytest.mark.parametrize(
    ("env", "name", "status"),
    [
        ({"PIGTAIL_CONNECTOR_HN_RANKS_ENABLED": "false"}, "hn_sources", "warn"),
        ({"PIGTAIL_ENABLE_HN": "maybe"}, "hn_sources", "fail"),
        ({"PIGTAIL_ENABLE_GITHUB_EVENTS": "perhaps"}, "github_events", "fail"),
        ({ADR022_ENV: "yes"}, "adr022_person_sources", "ok"),  # only "1" lifts the hold
    ],
)
def test_m1_t23_doctor_flag_edge_cases(env, name, status):
    assert flags(env)[name][0] == status


def test_m1_t23_doctor_cli_lists_flag_checks(monkeypatch, capsys):
    monkeypatch.setenv("PSEUDONYM_KEY", KEY)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for var in ("PIGTAIL_ENABLE_HN", "PIGTAIL_ENABLE_GITHUB_EVENTS", ADR022_ENV):
        monkeypatch.delenv(var, raising=False)
    main(["doctor", "--json"])
    names = {c["name"] for c in json.loads(capsys.readouterr().out)}
    assert {"adr022_person_sources", "hn_sources", "github_events"} <= names
