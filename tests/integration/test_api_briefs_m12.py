"""M12 D7 `/api/briefs` (R18.1–R18.5): behind the login, same-origin JSON writes, form and YAML
equivalence, versions, diff, estimate, several briefs. Synthetic briefs in a tmp data dir.
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
) -> Iterator[tuple[TestClient, Path, Any]]:
    monkeypatch.setenv("PIGTAIL_SUBSCRIPTION_WEEKLY_TOKENS", "20000000")
    data = tmp_path / "data"
    ui = UISettings(password_hash=password_hash, dist_dir=tmp_path / "dist")
    s = Settings.from_env({"PIGTAIL_DATA_DIR": str(data)})
    app = create_app(
        conninfo=pg_url, ui=ui, store=LocalSnapshotStore(tmp_path / "snap"), settings=s
    )
    with TestClient(app, base_url="http://localhost") as client:
        yield client, data, capture_db


def login(client: TestClient) -> None:
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200


def example() -> dict[str, Any]:
    return copy.deepcopy(yaml.safe_load(EXAMPLE.read_text()))


def test_d7_brief_routes_require_login(env):
    client, _, _ = env
    for path in ("/api/briefs", f"/api/briefs/{BID}", "/api/brief-template",
                 f"/api/briefs/{BID}/estimate"):  # fmt: skip
        assert client.get(path).status_code == 401, path
    assert client.post("/api/briefs", json={"yaml": "x: 1"}).status_code == 401


def test_d7_form_and_yaml_produce_the_same_stored_brief(env):
    client, data, _ = env
    login(client)
    r1 = client.post("/api/briefs", json={"brief": example()})
    assert r1.status_code == 201, r1.text
    d2 = example()
    d2["brief_id"] = "example-config-linter-yaml"
    r2 = client.post("/api/briefs", json={"yaml": yaml.safe_dump(d2)})
    assert r2.status_code == 201, r2.text
    b1, b2 = r1.json()["brief"], r2.json()["brief"]
    for b in (b1, b2):
        for k in ("brief_id", "created_at", "edited_at"):
            b.pop(k)
    assert b1 == b2
    assert (data / "briefs" / BID / "v0001.yaml").is_file()
    assert client.post("/api/briefs", json={"brief": example()}).status_code == 409


def test_d7_invalid_brief_rejected_with_field_named(env):
    client, _, _ = env
    login(client)
    bad = example()
    bad["panel"]["winners"] = 30
    del bad["success"]["primary"]
    r = client.post("/api/briefs", json={"brief": bad})
    assert r.status_code == 422
    paths = {e["path"] for e in r.json()["errors"]}
    assert {"panel.winners", "success.primary"} <= paths
    assert "panel.winners" in r.json()["detail"] or "success.primary" in r.json()["detail"]
    r = client.post("/api/briefs/validate", json={"yaml": "brief_id: [oops"})
    assert r.status_code == 422 and r.json()["errors"][0]["path"] == "(yaml)"
    r = client.post("/api/briefs/validate", json={"brief": example()})
    assert r.status_code == 200 and r.json()["ok"] is True and "yaml" in r.json()
    assert client.post("/api/briefs", json={}).status_code == 422


def test_d7_writes_are_same_origin_json_only_and_audited(env):
    client, _, db = env
    login(client)
    assert (
        client.post(
            "/api/briefs", json={"brief": example()}, headers={"Sec-Fetch-Site": "cross-site"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/briefs", json={"brief": example()}, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    r = client.post(
        "/api/briefs", content=yaml.safe_dump(example()), headers={"Content-Type": "text/plain"}
    )
    assert r.status_code in (415, 422)
    assert client.post("/api/briefs", json={"brief": example()}).status_code == 201
    events = [r[0] for r in db.conn.execute("SELECT event, route FROM ui_audit_log ORDER BY id")]
    assert events[-1] == "brief_create"
    routes = [r[0] for r in db.conn.execute("SELECT route FROM ui_audit_log")]
    assert all(BID not in r for r in routes)  # no brief id or content in the audit log


def test_d7_edit_creates_versions_diff_and_conflicts(env):
    client, _, _ = env
    login(client)
    client.post("/api/briefs", json={"brief": example()})
    d = example()
    d["window"]["months"] = 12
    r = client.post(f"/api/briefs/{BID}/versions", json={"brief": d, "base_version": 1})
    assert r.status_code == 201 and r.json()["version"] == 2 and r.json()["created"] is True
    r = client.post(f"/api/briefs/{BID}/versions", json={"brief": d, "base_version": 2})
    assert r.status_code == 200 and r.json()["created"] is False  # unchanged: no new version
    d["window"]["months"] = 15
    r = client.post(f"/api/briefs/{BID}/versions", json={"brief": d, "base_version": 1})
    assert r.status_code == 409  # stale edit
    got = client.get(f"/api/briefs/{BID}").json()
    assert got["version"] == 2 and [v["version"] for v in got["versions"]] == [1, 2]
    assert client.get(f"/api/briefs/{BID}/versions/1").json()["brief"]["window"]["months"] == 18
    diff = client.get(f"/api/briefs/{BID}/diff", params={"from": 1, "to": 2}).json()
    assert diff["changes"] == [{"path": "window.months", "kind": "changed", "old": 18, "new": 12}]
    other = example()
    other["brief_id"] = "someone-else"
    r = client.post(f"/api/briefs/{BID}/versions", json={"brief": other})
    assert r.status_code == 422 and r.json()["errors"][0]["path"] == "brief_id"
    assert client.get("/api/briefs/nope-nope").status_code == 404
    assert client.get("/api/briefs/..%2Fetc").status_code == 404


def test_d7_list_holds_several_briefs_with_status_and_budget(env):
    client, _, _ = env
    login(client)
    client.post("/api/briefs", json={"brief": example()})
    d = example()
    d["brief_id"] = "second-synthetic"
    d["project"]["name"] = "Second synthetic"
    client.post("/api/briefs", json={"brief": d})
    items = client.get("/api/briefs").json()["items"]
    assert [i["brief_id"] for i in items] == [BID, "second-synthetic"]
    assert items[0]["status"] == "draft" and items[0]["last_run"] is None
    assert items[0]["budget"]["money_usd"] == 0 and items[0]["budget"]["subscription_share"] == 0.5


def test_d7_estimate_and_template(env):
    client, _, _ = env
    login(client)
    t = client.get("/api/brief-template").json()
    assert t["brief"]["brief_id"] == BID and "schema_version: brief/v1" in t["yaml"]
    client.post("/api/briefs", json={"brief": example()})
    e = client.get(f"/api/briefs/{BID}/estimate").json()
    assert e["label"] == "estimate"
    assert e["money"]["usd"] == 0.0 and e["money"]["requires_approval"] is False
    assert e["llm"]["subscription"]["allowance"]["basis"] == "configured"
