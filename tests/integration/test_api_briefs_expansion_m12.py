"""M12 verifier fixes on the D7 API: LLM expansion proposals (R18.7) and stale edits on the
default save path (R18.4). Fake LLM backend; synthetic briefs in a tmp data dir.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from pigtail.api.app import create_app
from pigtail.api.auth import hash_password
from pigtail.api.settings import UISettings
from pigtail.capture.snapshots import LocalSnapshotStore
from pigtail.config import Settings
from tests.conftest import make_client
from tests.unit.test_briefs_expansion_m12 import FAKE_OUTPUT

pytestmark = pytest.mark.db

PASSWORD = "correct-horse-battery-staple"
EXAMPLE = Path(__file__).resolve().parents[2] / "docs" / "examples" / "brief-example.yaml"
BID = "example-config-linter"


@pytest.fixture(scope="module")
def password_hash() -> str:
    return hash_password(PASSWORD)


@pytest.fixture
def env(
    capture_db: Any,
    pg_url: str,
    tmp_path: Path,
    password_hash: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, dict[str, Any], Any]]:
    monkeypatch.setenv("PIGTAIL_SUBSCRIPTION_WEEKLY_TOKENS", "20000000")
    holder: dict[str, Any] = {"client": make_client([copy.deepcopy(FAKE_OUTPUT)])}
    ui = UISettings(password_hash=password_hash, dist_dir=tmp_path / "dist")
    s = Settings.from_env({"PIGTAIL_DATA_DIR": str(tmp_path / "data")})
    app = create_app(
        conninfo=pg_url,
        ui=ui,
        store=LocalSnapshotStore(tmp_path / "snap"),
        settings=s,
        llm_client=lambda: holder["client"],
    )
    with TestClient(app, base_url="http://localhost") as client:
        assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        yield client, holder, capture_db


def example() -> dict[str, Any]:
    return copy.deepcopy(yaml.safe_load(EXAMPLE.read_text()))


def versions(client: TestClient) -> list[int]:
    return [v["version"] for v in client.get(f"/api/briefs/{BID}").json()["versions"]]


def test_r18_7_expansion_requires_login(env, password_hash, tmp_path):
    client, _, _ = env
    client.post("/api/auth/logout", json={})
    assert client.post(f"/api/briefs/{BID}/expansion", json={}).status_code == 401


def test_r18_7_propose_returns_a_proposal_and_saves_nothing(env):
    client, holder, db = env
    client.post("/api/briefs", json={"brief": example()})
    r = client.post(f"/api/briefs/{BID}/expansion", json={"version": 1})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["label"] == "proposal" and p["saved"] is False and p["base_version"] == 1
    prov = p["expansion"]["provenance"]
    assert prov["job"] == "brief_expansion" and prov["backend"] == "subscription"
    assert {"prompt_id", "prompt_version", "prompt_fingerprint", "model"} <= set(prov)
    assert versions(client) == [1]
    # only the allowed fields were sent
    sent = holder["client"].backends["subscription"].calls[0]["prompt"]
    assert "Example Config Linter" not in sent  # the project name is never sent
    assert "no existing community" not in sent  # nor the context
    rows = list(db.conn.execute("SELECT event, route FROM ui_audit_log ORDER BY id"))
    assert rows[-1][0] == "brief_expansion" and BID not in rows[-1][1]
    assert client.post(f"/api/briefs/{BID}/expansion", json={}).status_code == 200  # latest
    assert client.post("/api/briefs/nope-nope/expansion", json={}).status_code == 404


def test_r18_7_accepting_saves_a_version_with_provenance(env):
    client, _, _ = env
    client.post("/api/briefs", json={"brief": example()})
    p = client.post(f"/api/briefs/{BID}/expansion", json={}).json()
    current = client.get(f"/api/briefs/{BID}").json()["brief"]
    exp = p["expansion"]
    exp["keywords"] = [*exp["keywords"], "schema check"]  # edited before accepting
    r = client.post(
        f"/api/briefs/{BID}/versions",
        json={"brief": {**current, "expansion": exp}, "base_version": p["base_version"]},
    )
    assert r.status_code == 201, r.text
    saved = r.json()["brief"]["expansion"]
    assert saved["generated_by"] == "llm" and saved["provenance"]["edited_by_user"] is True
    assert versions(client) == [1, 2]


def test_r18_7_budget_stop_is_409_and_nothing_is_sent(env):
    client, holder, _ = env
    client.post("/api/briefs", json={"brief": example()})
    holder["client"] = make_client([], api=[copy.deepcopy(FAKE_OUTPUT)], default_backend="api")
    r = client.post(f"/api/briefs/{BID}/expansion", json={})
    assert r.status_code == 409
    assert r.json()["stop"]["kind"] == "backend" and "nothing was sent" in r.json()["detail"]
    assert holder["client"].backends["api"].calls == []
    assert versions(client) == [1]


# --- stale edits on the default path (verifier's case) -------------------------------------------
def test_m12_api_save_of_an_old_export_without_base_version_is_refused(env):
    client, _, _ = env
    client.post("/api/briefs", json={"brief": example()})
    v1_export = client.get(f"/api/briefs/{BID}/versions/1").json()["brief"]
    assert v1_export["version"] == 1
    v2 = {**copy.deepcopy(v1_export), "window": {"months": 12}}
    r = client.post(f"/api/briefs/{BID}/versions", json={"brief": v2})  # base from `version: 1`
    assert r.status_code == 201 and r.json()["version"] == 2
    stale = copy.deepcopy(v1_export)
    stale["panel"]["losers"] = 18
    r = client.post(f"/api/briefs/{BID}/versions", json={"brief": stale})
    assert r.status_code == 409 and "revert" in r.json()["detail"]
    r = client.post(f"/api/briefs/{BID}/versions", json={"yaml": yaml.safe_dump(stale)})
    assert r.status_code == 409
    got = client.get(f"/api/briefs/{BID}").json()
    assert got["version"] == 2 and got["brief"]["window"]["months"] == 12  # v2 not reverted


def test_m12_api_save_without_any_base_needs_force_latest(env):
    client, _, _ = env
    client.post("/api/briefs", json={"brief": example()})
    d = example()  # the example has no `version` field
    d["panel"]["losers"] = 18
    r = client.post(f"/api/briefs/{BID}/versions", json={"brief": d})
    assert r.status_code == 409 and "force_latest" in r.json()["detail"]
    r = client.post(f"/api/briefs/{BID}/versions", json={"brief": d, "force_latest": True})
    assert r.status_code == 201 and r.json()["version"] == 2


def test_m12_api_payload_version_and_base_version_must_agree(env):
    client, _, _ = env
    client.post("/api/briefs", json={"brief": example()})
    d = example()
    d["window"]["months"] = 12
    client.post(f"/api/briefs/{BID}/versions", json={"brief": d, "base_version": 1})
    old = client.get(f"/api/briefs/{BID}/versions/1").json()["brief"]  # version: 1
    old["panel"]["losers"] = 18
    r = client.post(f"/api/briefs/{BID}/versions", json={"brief": old, "base_version": 2})
    assert r.status_code == 409 and "says version 1" in r.json()["detail"]
    latest = client.get(f"/api/briefs/{BID}").json()["brief"]
    latest["panel"]["losers"] = 18
    r = client.post(f"/api/briefs/{BID}/versions", json={"brief": latest, "base_version": 2})
    assert r.status_code == 201 and r.json()["version"] == 3
