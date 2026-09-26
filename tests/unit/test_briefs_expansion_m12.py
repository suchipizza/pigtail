"""M12 verifier fix: LLM expansion of a brief (PRD R18.7; D7 "LLM expansion"; ADR-053.1).

Fake LLM backend only (no real model calls in CI); synthetic briefs (the repo's example).
"""

from __future__ import annotations

import copy
import json
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from pigtail.briefs.budget import Allowance, BudgetGuard, BudgetStop
from pigtail.briefs.estimate import estimate, render_text
from pigtail.briefs.expansion import (
    EXPANSION_PROMPT,
    JOB,
    apply_expansion,
    expansion_input,
    make_guard,
    propose_expansion,
)
from pigtail.briefs.model import BriefInvalid, load_brief_text, validate_brief
from pigtail.briefs.store import BriefStore, VersionConflict
from pigtail.llm.store import UsageRow
from tests.conftest import make_client

EXAMPLE = Path(__file__).resolve().parents[2] / "docs" / "examples" / "brief-example.yaml"

FAKE_OUTPUT: dict[str, Any] = {
    "problem_statement": "Config errors surface late because files aren't checked where written.",
    "users": ["platform engineers", "Platform Engineers", "backend developers"],
    "keywords": ["config linter", "yaml validation"],
    "topics": ["configuration validation"],
    "competitors": [
        {"name": "Synthetic Validator", "url": "https://example.com/validator"},
        {"name": "Synthetic Checker", "url": "not a url"},
        {"name": "Synthetic Validator", "url": None},
    ],
    "github_topics": ["yaml", "JSON Schema", "!!!", "yaml"],
    "search_queries": ["yaml linter cli"],
}


def example_data(**extra: Any) -> dict[str, Any]:
    d = yaml.safe_load(EXAMPLE.read_text())
    d.pop("expansion", None)
    d.update(extra)
    return copy.deepcopy(d)


@pytest.fixture
def store(tmp_path: Path) -> BriefStore:
    s = BriefStore(tmp_path / "briefs")
    s.create(validate_brief(example_data()))
    return s


def guard_for(client: Any, brief: Any, **kw: Any) -> BudgetGuard:
    return BudgetGuard(
        budget=brief.budget,
        usage=client.store,
        allowance=kw.pop("allowance", Allowance(10_000_000, "configured")),
        **kw,
    )


# --- what is sent --------------------------------------------------------------------------------
def test_r18_7_sends_only_description_users_business_model_and_field_boundaries(store):
    data = example_data(notes="PRIVATE-NOTE-TOKEN")
    data["project"]["name"] = "PRIVATE-NAME-TOKEN"
    data["project"]["context"] = "PRIVATE-CONTEXT-TOKEN"
    data["field"]["core_field"] = "PRIVATE-CORE-TOKEN"
    data["field"]["seed_projects"] = ["PRIVATE-SEED-TOKEN"]
    data["field"]["reference_cases"] = [{"name": "PRIVATE-REF-TOKEN"}]
    data["field"]["widening_steps"] = ["PRIVATE-WIDEN-TOKEN"]
    data["distribution_exemplars"] = {"projects": [{"name": "PRIVATE-EXEMPLAR-TOKEN"}]}
    data["channels"] = {"planned": ["github"], "details": {"github": ["PRIVATE-CHANNEL-TOKEN"]}}
    brief = store.save_version(validate_brief(data))[0].brief
    client = make_client([copy.deepcopy(FAKE_OUTPUT)])
    propose_expansion(brief, client, guard_for(client, brief))
    sent = client.backends["subscription"].calls[0]  # type: ignore[attr-defined]
    text = sent["system"] + sent["prompt"]
    assert "PRIVATE-" not in text
    payload = json.loads(expansion_input(brief))
    assert set(payload) == {
        "description",
        "target_users",
        "business_model",
        "field_include",
        "field_exclude",
    }
    assert brief.project.description in sent["prompt"]
    assert brief.field.exclude[0] in sent["prompt"]
    # structured output schema, closed objects
    props = sent["schema"]["properties"]
    assert {
        "problem_statement",
        "users",
        "keywords",
        "topics",
        "competitors",
        "github_topics",
        "search_queries",
    } == set(props)
    assert sent["schema"]["additionalProperties"] is False


# --- a proposal, never saved by itself -----------------------------------------------------------
def test_r18_7_proposal_is_normalized_has_provenance_and_saves_nothing(store):
    brief = store.get("example-config-linter").brief
    client = make_client([copy.deepcopy(FAKE_OUTPUT)])
    p = propose_expansion(brief, client, guard_for(client, brief))
    assert [v.version for v in store.versions("example-config-linter")] == [1]  # nothing saved
    d = p.to_dict()
    assert d["label"] == "proposal" and d["saved"] is False and d["base_version"] == 1
    assert any("not evidence" in n for n in d["notes"])
    e = p.expansion
    assert e.generated_by == "llm"
    assert e.users == ["platform engineers", "backend developers"]  # case-insensitive dedupe
    assert e.github_topics == ["yaml", "json-schema"]  # slugified, invalid and duplicate dropped
    assert [(c.name, c.url) for c in e.competitors] == [
        ("Synthetic Validator", "https://example.com/validator"),
        ("Synthetic Checker", None),  # a non-URL is removed, never guessed
    ]
    prov = e.provenance
    assert prov is not None
    assert (prov.prompt_id, prov.prompt_version) == (EXPANSION_PROMPT.id, EXPANSION_PROMPT.version)
    assert prov.prompt_fingerprint == EXPANSION_PROMPT.fingerprint
    assert (prov.model, prov.backend, prov.job) == ("test-model", "subscription", JOB)
    assert prov.based_on_version == 1 and prov.edited_by_user is False
    assert prov.proposal_hash == e.fields_hash()


def test_r18_7_llm_call_is_job_brief_expansion_and_cached(store):
    brief = store.get("example-config-linter").brief
    client = make_client([copy.deepcopy(FAKE_OUTPUT)])
    first = propose_expansion(brief, client, guard_for(client, brief))
    again = propose_expansion(brief, client, guard_for(client, brief))
    assert not first.cached and again.cached
    assert len(client.backends["subscription"].calls) == 1  # type: ignore[attr-defined]
    jobs = {r[0] for r in client.store._db.execute("SELECT job FROM llm_usage")}
    assert jobs == {JOB}


def test_r18_7_accept_saves_a_new_version_with_expansion_and_provenance(store):
    brief = store.get("example-config-linter").brief
    client = make_client([copy.deepcopy(FAKE_OUTPUT)])
    p = propose_expansion(brief, client, guard_for(client, brief))
    stored, created = apply_expansion(
        store, brief.brief_id, p.to_dict()["expansion"], base_version=p.base_version
    )
    assert created and stored.version == 2
    exp = stored.brief.expansion
    assert exp is not None and exp.provenance is not None
    assert exp.provenance.edited_by_user is False
    assert "provenance:" in stored.yaml_text and "prompt_fingerprint" in stored.yaml_text
    # the user edits the proposal before accepting: flagged, from content, on every load
    edited = p.to_dict()["expansion"]
    edited["keywords"] = ["config linter"]
    s3, _ = apply_expansion(store, brief.brief_id, edited, base_version=2)
    assert s3.version == 3
    reloaded = store.get(brief.brief_id, 3).brief.expansion
    assert reloaded is not None and reloaded.provenance is not None
    assert reloaded.provenance.edited_by_user is True


def test_r18_7_accept_on_a_stale_base_is_refused(store):
    brief = store.get("example-config-linter").brief
    client = make_client([copy.deepcopy(FAKE_OUTPUT)])
    p = propose_expansion(brief, client, guard_for(client, brief))
    d = example_data()
    d["window"]["months"] = 12
    store.save_version(validate_brief(d), base_version=1)  # someone saved v2 meanwhile
    with pytest.raises(VersionConflict):
        apply_expansion(store, brief.brief_id, p.to_dict()["expansion"], base_version=1)
    assert store.latest_version(brief.brief_id) == 2


def test_r18_7_invalid_edited_expansion_names_the_field(store):
    with pytest.raises(BriefInvalid) as ei:
        apply_expansion(
            store, "example-config-linter", {"github_topics": ["Not A Slug"]}, base_version=1
        )
    assert ei.value.problems[0].path.startswith("expansion.github_topics")


def test_r18_7_provenance_requires_generated_by_llm():
    d = example_data()
    d["expansion"] = {
        "generated_by": "user",
        "provenance": {
            "prompt_id": "brief_expansion",
            "prompt_version": "1",
            "prompt_fingerprint": "abc",
            "model": "m",
            "backend": "subscription",
            "input_hash": "0" * 64,
            "proposal_hash": "0" * 64,
            "generated_at": "2026-09-26T00:00:00Z",
        },
    }
    with pytest.raises(BriefInvalid) as ei:
        validate_brief(d)
    assert ei.value.problems[0].path == "expansion.generated_by"


# --- budget guard: no automatic backend switch, caps, approval (ADR-053.1) -----------------------
def test_adr_053_backend_other_than_the_briefs_is_refused_before_any_call(store):
    brief = store.get("example-config-linter").brief  # llm_backend: subscription
    client = make_client([], api=[copy.deepcopy(FAKE_OUTPUT)], default_backend="api")
    with pytest.raises(BudgetStop) as ei:
        propose_expansion(brief, client, make_guard(client, brief))
    assert ei.value.kind == "backend"
    assert client.backends["api"].calls == []  # type: ignore[attr-defined]


def test_adr_053_explicit_per_job_override_is_honoured(store):
    d = example_data()
    d["budget"]["llm_api_usd"] = 5
    brief = store.save_version(validate_brief(d))[0].brief
    client = make_client(
        [], api=[copy.deepcopy(FAKE_OUTPUT)], overrides={JOB: "api"}
    )  # the user routed this job to the API explicitly
    with pytest.raises(BudgetStop) as ei:  # api costs money: needs approval first
        propose_expansion(brief, client, make_guard(client, brief))
    assert ei.value.kind == "approval"
    p = propose_expansion(brief, client, make_guard(client, brief, approved_paid=True))
    assert p.expansion.provenance is not None and p.expansion.provenance.backend == "api"


def test_adr_053_subscription_share_cap_stops_before_the_call(store):
    brief = store.get("example-config-linter").brief
    client = make_client([copy.deepcopy(FAKE_OUTPUT)])
    client.store.record(
        UsageRow("subscription", "other", "m", "p", "1", "ok", input_tokens=4_999_000)
    )
    guard = guard_for(client, brief, allowance=Allowance(10_000_000, "configured"))
    with pytest.raises(BudgetStop) as ei:
        propose_expansion(brief, client, guard)
    assert ei.value.kind == "subscription_share"
    assert client.backends["subscription"].calls == []  # type: ignore[attr-defined]


# --- estimate (estimate-v1) ----------------------------------------------------------------------
def test_r18_7_estimate_run_makes_no_expansion_call_and_reports_the_proposal_cost():
    b = load_brief_text(EXAMPLE.read_text()).model_copy(update={"version": 1})
    e = estimate(b, allowance=Allowance(10_000_000, "configured"))
    stage = next(s for s in e.stages if s.stage == "expansion")
    assert stage.llm_calls == 0
    d = e.to_dict()
    assert d["model"] == "estimate-v1"
    assert d["expansion"]["status"] == "written_by_user"
    assert d["expansion"]["run_llm_calls"] == 0
    assert d["expansion"]["proposal"]["llm_calls"] == 1
    assert "pigtail brief expand example-config-linter" in render_text(e, b)
    no_exp = b.model_copy(update={"expansion": None})
    assert (
        estimate(no_exp, allowance=Allowance(10_000_000, "configured")).to_dict()["expansion"][
            "status"
        ]
        == "none"
    )


# --- CLI -----------------------------------------------------------------------------------------
@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    from pigtail.briefs import cli as brief_cli

    monkeypatch.setenv("PIGTAIL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PIGTAIL_SUBSCRIPTION_WEEKLY_TOKENS", "20000000")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    holder: dict[str, Any] = {"client": make_client([copy.deepcopy(FAKE_OUTPUT)])}
    monkeypatch.setattr(brief_cli, "_llm_client", lambda: holder["client"])
    return holder


def test_r18_7_cli_expand_propose_then_accept(cli_env, tmp_path, capsys):
    from pigtail.cli import main

    assert main(["brief", "new", "--example", "--id", "synthetic-demo"]) == 0
    capsys.readouterr()
    out = tmp_path / "proposal.yaml"
    assert main(["brief", "expand", "synthetic-demo", "--out", str(out)]) == 0
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
    assert main(["brief", "versions", "synthetic-demo", "--json"]) == 0
    assert [v["version"] for v in json.loads(capsys.readouterr().out)] == [1]  # nothing saved
    doc = yaml.safe_load(out.read_text())
    assert doc["base_version"] == 1 and doc["expansion"]["generated_by"] == "llm"
    doc["expansion"]["keywords"].append("schema check")  # the user edits the proposal
    out.write_text(yaml.safe_dump(doc))
    assert main(["brief", "expand", "synthetic-demo", "--accept", str(out)]) == 0
    msg = capsys.readouterr().out
    assert "v2" in msg and "edited by you" in msg
    assert main(["brief", "show", "synthetic-demo"]) == 0
    shown = capsys.readouterr().out
    assert "edited_by_user: true" in shown and "schema version" not in shown
    # accepting the same proposal again is stale now (v2 exists) and saves nothing
    assert main(["brief", "expand", "synthetic-demo", "--accept", str(out)]) == 1
    assert "refused" in capsys.readouterr().err


def test_r18_7_cli_expand_json_and_budget_stop_exit_codes(cli_env, capsys):
    from pigtail.cli import main

    main(["brief", "new", "--example", "--id", "synthetic-demo"])
    capsys.readouterr()
    assert main(["brief", "expand", "synthetic-demo", "--json"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["saved"] is False and got["expansion"]["provenance"]["backend"] == "subscription"
    cli_env["client"] = make_client([], api=[], default_backend="api")
    assert main(["brief", "expand", "synthetic-demo"]) == 4  # backend mismatch: budget stop
    assert "nothing was sent" in capsys.readouterr().err


# --- stale edits on the default path (verifier's case) -------------------------------------------
def test_m12_cli_edit_from_old_export_is_refused_by_default(cli_env, tmp_path, capsys):
    """v1 exported, v2 saved (window 18 -> 12), then the v1 export is edited and saved without
    --base-version: before the fix this silently reverted v2's change."""
    from pigtail.cli import main

    bid = "synthetic-demo"
    main(["brief", "new", "--example", "--id", bid])
    capsys.readouterr()
    main(["brief", "show", bid])
    v1_export = capsys.readouterr().out
    assert "version: 1" in v1_export
    v2 = tmp_path / "v2.yaml"
    v2.write_text(v1_export.replace("months: 18", "months: 12"))
    assert main(["brief", "edit", bid, "--from", str(v2)]) == 0
    stale = tmp_path / "stale.yaml"
    stale.write_text(v1_export.replace("losers: 20", "losers: 18"))
    capsys.readouterr()
    assert main(["brief", "edit", bid, "--from", str(stale)]) == 1
    assert "refused" in capsys.readouterr().err
    main(["brief", "show", bid, "--json"])
    latest = json.loads(capsys.readouterr().out)
    assert latest["version"] == 2 and latest["window"]["months"] == 12  # v2 not reverted
    # a file without a version field falls back to latest, with a warning
    no_version = (
        "\n".join(line for line in v1_export.splitlines() if not line.startswith("version:"))
        .replace("losers: 20", "losers: 18")
        .replace("months: 18", "months: 12")
    )
    nv = tmp_path / "nv.yaml"
    nv.write_text(no_version)
    assert main(["brief", "edit", bid, "--from", str(nv)]) == 0
    captured = capsys.readouterr()
    assert "no `version:` field" in captured.err and "v3" in captured.out
    # the explicit flag still wins
    stale.write_text(
        v1_export.replace("losers: 20", "losers: 16").replace("months: 18", "months: 12")
    )
    assert main(["brief", "edit", bid, "--from", str(stale), "--base-version", "3"]) == 0


def test_m12_edit_base_version_rules():
    from pigtail.briefs.cli import edit_base_version

    assert edit_base_version(explicit=4, file_version=1, latest=5, from_editor=False) == 4
    assert edit_base_version(explicit=None, file_version=2, latest=5, from_editor=False) == 2
    assert edit_base_version(explicit=None, file_version=None, latest=5, from_editor=False) == 5
    assert edit_base_version(explicit=None, file_version=2, latest=5, from_editor=True) == 5


def test_now_is_recorded_as_generation_time(store):
    brief = store.get("example-config-linter").brief
    client = make_client([copy.deepcopy(FAKE_OUTPUT)])
    t = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
    p = propose_expansion(brief, client, guard_for(client, brief), now=t + timedelta(0))
    assert p.expansion.provenance is not None and p.expansion.provenance.generated_at == t
