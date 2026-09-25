"""M1-T12 D1 preview: read-only API (R14.2), traceability (R13.2), operator auth + audit (R13.3,
DPIA CB-19) on a seeded Postgres.

Synthetic data only: fake repos `org-a/repo-1`, `org-b/repo-2`, fake HN ids 9000001+, and fake
handles `hnuserNNN` that exist only inside the (temporary) snapshot bytes and source text. They
must never appear in a JSON response.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from pigtail.api.app import create_app
from pigtail.api.auth import AuthStore, hash_password
from pigtail.api.settings import UISettings
from pigtail.capture.models import Evidence, evidence_id
from pigtail.capture.snapshots import LocalSnapshotStore, SnapshotMeta, sha256_hex
from pigtail.config import Settings

pytestmark = pytest.mark.db

PASSWORD = "correct-horse-battery-staple"
CASE1 = "case_00000000000000000001"
CASE2 = "case_00000000000000000002"
T0 = datetime(2026, 9, 20, 0, tzinfo=UTC)
HANDLE_MARKER = "hnuser"
PSEUDO = "p_0123456789abcdef"


@pytest.fixture(scope="module")
def password_hash() -> str:
    return hash_password(PASSWORD)


class Seed:
    """Evidence ids by name, and the store."""

    def __init__(self, store: LocalSnapshotStore) -> None:
        self.store = store
        self.ev: dict[str, str] = {}
        self.hash: dict[str, str] = {}


def _evidence(
    db: Any,
    seed: Seed,
    name: str,
    *,
    source: str,
    url: str,
    data: bytes,
    fetched_at: datetime,
    retention: str = "project_level",
    state: str = "present",
    reliability: str = "high",
    case_id: str | None = None,
    repo_id: str | None = None,
    content_type: str = "application/json",
) -> str:
    meta = SnapshotMeta(source, url, fetched_at, "test/1", "synthetic", content_type)
    h = seed.store.put(data, meta)
    ev = Evidence(
        id=evidence_id(source, url, h),
        source=source,
        url=url,
        fetched_at=fetched_at,
        content_hash=h,
        snapshot_ref=seed.store.ref(h),
        content_type=content_type,
        http_status=200,
        reliability=reliability,  # type: ignore[arg-type]
        terms_basis="synthetic",
        retention_class=retention,  # type: ignore[arg-type]
        deletion_state=state,  # type: ignore[arg-type]
        collector_version="test/1",
        case_id=case_id,
        repo_id=repo_id,
    )
    db.upsert_evidence(ev)
    if state != "present":
        seed.store.delete(h)
    seed.ev[name], seed.hash[name] = ev.id, h
    return ev.id


def seed_db(db: Any, store: LocalSnapshotStore) -> Seed:
    s = Seed(store)
    x = db.conn.execute
    x(
        "INSERT INTO repos (id, host, host_id, full_name, first_seen_at) VALUES"
        " ('github:1000001', 'github', 1000001, 'org-a/repo-1', %s),"
        " ('github:1000002', 'github', 1000002, 'org-b/repo-2', %s)",
        (T0 - timedelta(days=5), T0 - timedelta(days=5)),
    )
    detection = {
        "rule_version": "velocity-v0",
        "detected_hour": (T0 + timedelta(hours=2)).isoformat(),
        "stars_48h": 150,
        "stars_48h_raw": 170,
        "forks_48h": 12,
        "baseline_mean_48h": 2.0,
        "baseline_std_48h": 1.0,
        "sigma_used": 3.0,
        "z_score": 49.3,
        "baseline_hours_covered": 0,
        "baseline_quality": "none",
        "threshold_min_stars": 100,
        "threshold_sigma": 3.0,
        "bot_filter_version": "bf-v0",
        "coverage": {
            "source": "gharchive",
            "window_start": (T0 - timedelta(hours=45)).isoformat(),
            "window_end": (T0 + timedelta(hours=3)).isoformat(),
            "observed_stars": 170,
            "reference_stars": None,
            "reference_source": None,
            "ratio": None,
        },
    }
    x(
        "INSERT INTO cases (id, repo_id, opened_at, trigger, status, detection) VALUES"
        " (%s, 'github:1000001', %s, 'velocity', 'live', %s),"
        " (%s, 'github:1000002', %s, 'manual', 'closed', NULL)",
        (CASE1, T0 + timedelta(hours=3), json.dumps(detection), CASE2, T0 - timedelta(days=3)),
    )
    # GH Archive: hours 00-03 scanned ok (00 raw dropped after retention), hour 04 missing.
    for i in range(4):
        hour = T0 + timedelta(hours=i)
        name = f"gh{i}"
        _evidence(
            db,
            s,
            name,
            source="gharchive",
            url=f"https://data.gharchive.org/2026-09-20-{i}.json.gz",
            data=f'{{"hour": {i}, "actor": "{HANDLE_MARKER}9{i}"}}'.encode(),
            fetched_at=hour + timedelta(hours=1),
            retention="person_level_24m",
            state="raw_dropped" if i == 0 else "present",
            content_type="application/gzip",
        )
        x(
            "INSERT INTO gharchive_hours (hour, status, content_hash, evidence_id, events,"
            " bot_filter_version) VALUES (%s, 'ok', %s, %s, 100, 'bf-v0')",
            (hour, s.hash[name], s.ev[name]),
        )
    x(
        "INSERT INTO gharchive_hours (hour, status, events, bot_filter_version)"
        " VALUES (%s, 'missing', 0, 'bf-v0')",
        (T0 + timedelta(hours=4),),
    )
    for i, (raw, filt, forks) in enumerate([(60, 50, 4), (60, 55, 4), (50, 45, 4)]):
        x(
            "INSERT INTO repo_hourly_activity (repo_host_id, hour, repo_name, stars_raw,"
            " stars_bot, stars_lockstep, stars_filtered, forks_raw, forks_filtered)"
            " VALUES (1000001, %s, 'org-a/repo-1', %s, %s, 0, %s, %s, %s)",
            (T0 + timedelta(hours=i), raw, raw - filt, filt, forks, forks),
        )
    # HN: two rank polls, one story about the repo (title/url hold fake handles).
    for j, (minutes, rank) in enumerate([(70, 12), (130, 5)]):
        at = T0 + timedelta(minutes=minutes)
        eid = _evidence(
            db,
            s,
            f"poll{j}",
            source="hn_ranks",
            url="https://hacker-news.firebaseio.com/v0/topstories.json",
            data=f"[9000001, {j}]".encode(),
            fetched_at=at,
        )
        x(
            "INSERT INTO hn_rank_poll (observed_at, n_items, content_hash, evidence_id)"
            " VALUES (%s, 2, %s, %s)",
            (at, s.hash[f"poll{j}"], eid),
        )
        x(
            "INSERT INTO hn_rank_observation (item_id, observed_at, rank, score)"
            " VALUES (9000001, %s, %s, %s)",
            (at, rank, 40 + j * 30),
        )
    item_ev = _evidence(
        db,
        s,
        "story_item",
        source="hn_ranks",
        url="https://hacker-news.firebaseio.com/v0/item/9000001.json",
        data=b'{"id": 9000001, "by": "hnuser001"}',
        fetched_at=T0 + timedelta(minutes=70),
        retention="person_level_24m",
        state="raw_dropped",
    )
    x(
        "INSERT INTO hn_story (item_id, type, url, title, created_at, score, repo_full_name,"
        " repo_id, best_rank, first_seen_at, last_seen_at, evidence_id) VALUES"
        " (9000001, 'story', 'https://github.com/hnuser003', 'Show HN: repo-1, thanks @hnuser002',"
        " %s, 70, 'org-a/repo-1', 'github:1000001', 5, %s, %s, %s)",
        (
            T0 + timedelta(minutes=60),
            T0 + timedelta(minutes=70),
            T0 + timedelta(minutes=130),
            item_ev,
        ),
    )
    search_ev = _evidence(
        db,
        s,
        "search",
        source="hn_algolia",
        url="https://hn.algolia.com/api/v1/search?query=org-a%2Frepo-1",
        data=b'{"hits": [{"author": "hnuser004", "objectID": "9000002"}]}',
        fetched_at=T0 + timedelta(hours=2, minutes=30),
        retention="person_level_24m",
        reliability="medium",
        case_id=CASE1,
        repo_id="github:1000001",
    )
    x(
        "INSERT INTO hn_mention (repo_full_name, item_id, item_type, author, created_at,"
        " story_id, title, match_kind, repo_id, case_id, evidence_id, first_seen_at,"
        " last_seen_at) VALUES ('org-a/repo-1', 9000002, 'story', %s, %s, 9000002,"
        " 'Ask HN: anyone used org-a/repo-1? cc @hnuser005', 'full_name', 'github:1000001',"
        " %s, %s, %s, %s)",
        (
            PSEUDO,
            T0 + timedelta(hours=2),
            CASE1,
            search_ev,
            T0 + timedelta(hours=3),
            T0 + timedelta(hours=3),
        ),
    )
    _evidence(
        db,
        s,
        "deleted",
        source="hn_firebase",
        url="https://hacker-news.firebaseio.com/v0/item/9000003.json",
        data=b'{"id": 9000003, "by": "hnuser006", "text": "gone"}',
        fetched_at=T0 + timedelta(hours=2, minutes=40),
        retention="person_level_24m",
        state="deleted_upstream",
        reliability="low",
        case_id=CASE1,
        repo_id="github:1000001",
    )
    _evidence(
        db,
        s,
        "page",
        source="web",
        url="https://example.org/org-a/repo-1/readme.html",
        data=b"<html><script>alert(1)</script><p>readme</p></html>",
        fetched_at=T0 + timedelta(hours=2, minutes=50),
        repo_id="github:1000001",
        content_type="text/html",
        reliability="unknown",
    )
    _evidence(
        db,
        s,
        "tampered",
        source="web",
        url="https://example.org/org-a/repo-1/changelog.txt",
        data=b"original changelog",
        fetched_at=T0 + timedelta(hours=2, minutes=55),
        case_id=CASE1,
        content_type="text/plain",
    )
    tampered_path = Path(store.root) / "sha256" / s.hash["tampered"][:2] / s.hash["tampered"][2:4]
    (tampered_path / s.hash["tampered"]).write_bytes(b"edited after capture")
    return s


@pytest.fixture
def env(
    capture_db: Any, pg_url: str, tmp_path: Path, password_hash: str
) -> Iterator[tuple[TestClient, Seed, Any]]:
    store = LocalSnapshotStore(tmp_path / "snapshots")
    seed = seed_db(capture_db, store)
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><div id=root></div>")
    (dist / "assets" / "app.js").write_text("console.log('ui')")
    ui = UISettings(password_hash=password_hash, dist_dir=dist)
    app = create_app(conninfo=pg_url, ui=ui, store=store, settings=Settings.from_env({}))
    with TestClient(app, base_url="http://localhost") as client:
        yield client, seed, capture_db


def login(client: TestClient, password: str = PASSWORD, **headers: str) -> Any:
    return client.post("/api/auth/login", json={"password": password}, headers=headers)


def audit_rows(db: Any) -> list[tuple[Any, ...]]:
    return db.conn.execute(
        "SELECT event, route, status, evidence_id, content_hash, session, client"
        " FROM ui_audit_log ORDER BY id"
    ).fetchall()


API_GETS = [
    "/api/auth/me",
    "/api/cases",
    f"/api/cases/{CASE1}",
    f"/api/cases/{CASE1}/timeline",
    f"/api/cases/{CASE1}/evidence",
    "/api/evidence/ev_000000000000000000000000",
    "/api/snapshots/" + "0" * 64,
    "/api/anything/else",
]


# --- R13.3 / CB-19: authentication ------------------------------------------------------------
def test_r13_3_every_api_route_requires_login(env):
    client, _, _ = env
    for path in API_GETS:
        r = client.get(path)
        assert r.status_code == 401, path
    assert client.get("/healthz").status_code == 200


def test_r13_3_login_sets_strict_httponly_cookie_and_audits(env):
    client, _, db = env
    r = login(client)
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie.replace("Strict", "strict")
    assert "Secure" not in cookie  # loopback over http
    assert PASSWORD not in cookie
    assert client.get("/api/auth/me").json()["authenticated"] is True
    rows = audit_rows(db)
    assert rows[-1][0] == "login_success"
    client_hash = rows[-1][6]
    assert client_hash is None or (len(client_hash) == 16 and "." not in client_hash)
    # The stored session is a hash, not the cookie value.
    stored = db.conn.execute("SELECT token_hash FROM ui_sessions").fetchall()
    assert len(stored) == 1 and stored[0][0] not in cookie


def test_r13_3_cookie_is_secure_off_loopback(capture_db, pg_url, tmp_path, password_hash):
    app = create_app(
        conninfo=pg_url,
        ui=UISettings(password_hash=password_hash, dist_dir=tmp_path),
        store=LocalSnapshotStore(tmp_path / "s"),
    )
    with TestClient(app, base_url="https://pigtail.example.org") as client:
        r = login(client)
    assert r.status_code == 200
    assert "Secure" in r.headers["set-cookie"]


def test_r13_3_logout_ends_session(env):
    client, _, db = env
    login(client)
    assert client.post("/api/auth/logout", json={}).status_code == 200
    client.cookies.clear()
    assert client.get("/api/cases").status_code == 401
    assert db.conn.execute("SELECT count(*) FROM ui_sessions").fetchone()[0] == 0
    assert "logout" in [r[0] for r in audit_rows(db)]


def test_r13_3_stolen_cookie_value_after_logout_is_rejected(env):
    client, _, _ = env
    login(client)
    token = client.cookies.get("pigtail_session")
    assert token
    client.post("/api/auth/logout", json={})
    client.cookies.clear()
    client.cookies.set("pigtail_session", token)  # replay the old cookie value
    assert client.get("/api/cases").status_code == 401


def test_cb19_bad_password_is_rate_limited_and_audited(env):
    client, _, db = env
    for _ in range(5):
        assert login(client, "wrong-password-123").status_code == 401
    r = login(client)  # even the right password is refused while limited
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) > 0
    events = [row[0] for row in audit_rows(db)]
    assert events.count("login_failure") == 5
    assert events[-1] == "login_rate_limited"
    assert "login_success" not in events
    dump = json.dumps([list(map(str, r)) for r in audit_rows(db)])
    assert PASSWORD not in dump and "wrong-password" not in dump
    assert "127.0.0.1" not in dump and "testclient" not in dump


def test_cb19_login_post_refuses_cross_site_and_non_json(env):
    client, _, _ = env
    assert login(client, **{"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert login(client, Origin="https://evil.example").status_code == 403
    r = client.post(
        "/api/auth/login",
        content="password=" + PASSWORD,
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code in (415, 422)
    assert login(client, Origin="http://localhost").status_code == 200


def test_cb19_session_idle_and_absolute_expiry(env, pg_url, password_hash):
    from psycopg_pool import ConnectionPool

    with ConnectionPool(pg_url, min_size=1, max_size=1, open=True) as pool:
        a = AuthStore(pool, UISettings(password_hash=password_hash, idle_minutes=30))
        now = datetime.now(UTC)
        token, th = a.create_session(now)
        assert a.check_session(token, now + timedelta(minutes=10)) == th
        assert a.check_session(token, now + timedelta(minutes=45)) is None  # idle
        token, _ = a.create_session(now)
        for m in range(0, 12 * 60 + 20, 20):
            last = a.check_session(token, now + timedelta(minutes=m))
        assert last is None  # absolute 12 h
        assert a.check_session("not-a-token") is None


# --- R14.2: the API is read-only ------------------------------------------------------------
def test_r14_2_read_pool_is_read_only(env):
    client, _, _ = env
    pool = client.app.state.pools["read"]  # type: ignore[attr-defined]
    with pool.connection() as conn, pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        conn.execute("DELETE FROM cases")


# --- R14.2 endpoints on seeded data -----------------------------------------------------------
def test_r14_2_cases_list_filters_and_sort(env):
    client, _, _ = env
    login(client)
    body = client.get("/api/cases").json()
    assert [c["id"] for c in body["items"]] == [CASE1, CASE2]  # recency
    assert body["total"] == 2 and body["caveats"]
    c1 = body["items"][0]
    assert c1["stars_48h"] == 150 and c1["repo_full_name"] == "org-a/repo-1"
    assert [c["id"] for c in client.get("/api/cases?status=closed").json()["items"]] == [CASE2]
    vel = client.get("/api/cases?sort=velocity").json()["items"]
    assert vel[0]["id"] == CASE1 and vel[1]["stars_48h"] is None
    ranged = client.get("/api/cases", params={"from": "2026-09-19T00:00:00Z"}).json()
    assert [c["id"] for c in ranged["items"]] == [CASE1]
    assert client.get("/api/cases?status=bogus").status_code == 422
    assert client.get("/api/cases?sort=bogus").status_code == 422


def test_r13_2_case_detection_numbers_trace_to_evidence(env):
    client, seed, _ = env
    login(client)
    body = client.get(f"/api/cases/{CASE1}").json()
    assert body["coded"] is False
    assert body["repo"]["full_name"] == "org-a/repo-1"
    assert body["detection"]["stars_48h"] == 150
    assert body["coverage"]["source"] == "gharchive"
    assert any("GH Archive" in c for c in body["caveats"])
    hours = body["detection_hours"]
    assert len(hours) == 48
    # stars_48h is the sum of the 48 hourly buckets, each linked to its GH Archive dump.
    assert sum(h["stars_filtered"] for h in hours) == 150
    linked = {h["evidence_id"] for h in hours if h["stars_filtered"]}
    assert linked == {seed.ev["gh0"], seed.ev["gh1"], seed.ev["gh2"]}
    assert body["evidence_counts"]["gharchive_hour"] == 3
    assert client.get("/api/cases/case_ffffffffffffffffffff").status_code == 404
    assert client.get("/api/cases/NOT-AN-ID").status_code == 404


def test_m1_t28_v1_case_detection_hours_read_count_snapshots(env):
    """M1-T28: a detection-v1 case's hours come from `repo_count_snapshot`, not GH Archive."""
    client, seed, db = env
    start = T0 + timedelta(days=1)
    snaps = [(0, 1000, 50), (1, 1030, 51), (24, 1080, 53), (48, 1150, 55)]  # hours, stars, forks
    evs = []
    for h, stars, forks in snaps:
        evs.append(
            _evidence(
                db,
                seed,
                f"gq{h}",
                source="github",
                url="https://api.github.com/graphql",
                data=f'{{"h": {h}}}'.encode(),
                fetched_at=start + timedelta(hours=h),
            )
        )
        db.conn.execute(
            "INSERT INTO repo_count_snapshot (repo_host_id, observed_at, stars, forks,"
            " evidence_id) VALUES (1000002, %s, %s, %s, %s)",
            (start + timedelta(hours=h), stars, forks, evs[-1]),
        )
    # a snapshot outside the window is not part of the case
    db.conn.execute(
        "INSERT INTO repo_count_snapshot (repo_host_id, observed_at, stars, forks)"
        " VALUES (1000002, %s, 900, 40)",
        (start - timedelta(hours=5),),
    )
    end = start + timedelta(hours=48)
    det = {
        "rule_version": "detection-v1",
        "detected_hour": end.isoformat(),
        "stars_48h": 150,
        "coverage": {
            "source": "github_graphql_counts",
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "observed_stars": 150,
            "reference_stars": 148,
            "reference_source": "github_star_history",
            "ratio": 1.0135,
        },
    }
    case3 = "case_00000000000000000003"
    db.conn.execute(
        "INSERT INTO cases (id, repo_id, opened_at, trigger, status, detection)"
        " VALUES (%s, 'github:1000002', %s, 'velocity', 'live', %s)",
        (case3, end + timedelta(hours=1), json.dumps(det)),
    )
    login(client)
    body = client.get(f"/api/cases/{case3}").json()
    assert body["detection_hours_source"] == "github_counts"
    hours = body["detection_hours"]
    assert [h["stars"] for h in hours] == [1000, 1030, 1080, 1150]
    assert [h["stars_delta"] for h in hours] == [None, 30, 50, 70]
    assert sum(h["stars_delta"] or 0 for h in hours) == body["detection"]["stars_48h"]
    assert [h["evidence_id"] for h in hours] == evs  # every number traces to its snapshot
    # the v0 case still reads GH Archive hours
    v0 = client.get(f"/api/cases/{CASE1}").json()
    assert v0["detection_hours_source"] == "gharchive" and len(v0["detection_hours"]) == 48
    assert client.get(f"/api/cases/{CASE2}").json()["detection_hours_source"] is None


def test_r13_2_timeline_lanes_link_every_point_to_evidence(env):
    client, seed, _ = env
    login(client)
    body = client.get(
        f"/api/cases/{CASE1}/timeline",
        params={"from": "2026-09-20T00:00:00Z", "to": "2026-09-20T06:00:00Z"},
    ).json()
    assert body["range"]["bucket"] == "hour" and body["coded"] is False
    gh = {p["t"][:13]: p for p in body["github"]}
    assert gh["2026-09-20T00"]["stars_raw"] == 60
    assert gh["2026-09-20T00"]["stars_filtered"] == 50
    assert gh["2026-09-20T00"]["evidence_ids"] == [seed.ev["gh0"]]
    assert gh["2026-09-20T03"]["stars_filtered"] == 0  # scanned, no activity
    assert gh["2026-09-20T03"]["evidence_ids"] == [seed.ev["gh3"]]
    assert gh["2026-09-20T04"]["stars_filtered"] is None  # missing hour: unknown, not zero
    assert gh["2026-09-20T04"]["hours_missing"] == 1
    for p in body["github"]:
        assert p["evidence_ids"] or p["stars_filtered"] is None
    hn = body["hn"]
    assert [s["item_id"] for s in hn["stories"]] == [9000001]
    assert [(r["best_rank"], r["evidence_id"]) for r in hn["ranks"]] == [
        (12, seed.ev["poll0"]),
        (5, seed.ev["poll1"]),
    ]
    assert hn["mentions"][0]["evidence_id"] == seed.ev["search"]
    assert "author" not in hn["mentions"][0]  # person-level field not exposed
    kinds = {e["evidence"]["id"] for e in body["events"]}
    assert {seed.ev["search"], seed.ev["page"], seed.ev["deleted"]} <= kinds
    assert seed.ev["poll0"] not in kinds  # polls are shown as rank points, not events
    daily = client.get(f"/api/cases/{CASE1}/timeline?bucket=day").json()
    assert daily["range"]["bucket"] == "day"
    assert sum(p["stars_filtered"] or 0 for p in daily["github"]) == 150
    bad = client.get(
        f"/api/cases/{CASE1}/timeline", params={"from": "2026-01-01T00:00Z", "bucket": "hour"}
    )
    assert bad.status_code == 422


def test_r14_2_evidence_inventory_sort_filter_state(env):
    client, seed, _ = env
    login(client)
    body = client.get(f"/api/cases/{CASE1}/evidence").json()
    ids = {i["id"] for i in body["items"]}
    expected = {
        seed.ev[n]
        for n in (
            "gh0",
            "gh1",
            "gh2",
            "poll0",
            "poll1",
            "story_item",
            "search",
            "deleted",
            "page",
            "tampered",
        )
    }
    assert ids == expected and body["total"] == len(expected)
    by_id = {i["id"]: i for i in body["items"]}
    assert by_id[seed.ev["gh0"]]["snapshot"] == {
        "available": False,
        "href": None,
        "state": "raw_dropped",
    }
    assert by_id[seed.ev["page"]]["snapshot"]["href"].startswith("/api/snapshots/")
    assert by_id[seed.ev["search"]]["roles"] == ["case", "hn_mention"]
    rel = client.get(f"/api/cases/{CASE1}/evidence?sort=reliability&order=asc").json()["items"]
    order = ["high", "medium", "low", "unknown"]
    ranks = [order.index(i["reliability"]) for i in rel]
    assert ranks == sorted(ranks)
    only = client.get(f"/api/cases/{CASE1}/evidence?source=gharchive").json()
    assert only["total"] == 3 and "gharchive" in only["sources"]
    page = client.get(f"/api/cases/{CASE1}/evidence?limit=2&offset=2").json()
    assert len(page["items"]) == 2 and page["total"] == len(expected)
    assert client.get(f"/api/cases/{CASE1}/evidence?sort=url").status_code == 422
    assert client.get(f"/api/cases/{CASE1}/evidence?source=a;drop").status_code == 422


def test_r13_2_evidence_record_links_and_retention(env):
    client, seed, _ = env
    login(client)
    body = client.get(f"/api/evidence/{seed.ev['gh1']}").json()
    ev = body["evidence"]
    assert ev["content_hash"] == seed.hash["gh1"]
    assert ev["snapshot"]["available"] and ev["snapshot"]["in_store"] is True
    assert body["links"]["gharchive_hours"][0]["status"] == "ok"
    assert body["retention"]["raw_drop_due_at"].startswith("2026-10-20")  # 30 days
    story = client.get(f"/api/evidence/{seed.ev['story_item']}").json()
    assert story["links"]["hn_stories"] == [{"item_id": 9000001, "repo_id": "github:1000001"}]
    assert story["evidence"]["deletion_state"] == "raw_dropped"
    assert client.get("/api/evidence/ev_ffffffffffffffffffffffff").status_code == 404


# --- R13.2 / CB-19: snapshots ---------------------------------------------------------------
def test_r13_2_snapshot_served_verified_sandboxed_and_audited(env):
    client, seed, db = env
    login(client)
    h, e = seed.hash["page"], seed.ev["page"]
    r = client.get(f"/api/snapshots/{h}?evidence={e}")
    assert r.status_code == 200
    assert sha256_hex(r.content) == h
    assert r.headers["x-content-sha256"] == h
    assert r.headers["content-security-policy"].startswith("sandbox")
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["cache-control"] == "private, no-store"
    last = audit_rows(db)[-1]
    assert last[:5] == ("snapshot_view", "/api/snapshots/{hash}", 200, e, h)
    assert last[5] is not None and len(last[5]) == 16  # session hash prefix
    gz = client.get(f"/api/snapshots/{seed.hash['gh1']}")
    assert gz.status_code == 200 and gz.headers["content-disposition"].startswith("attachment")


def test_r13_2_snapshot_410_when_raw_dropped_or_deleted_upstream(env):
    client, seed, db = env
    login(client)
    r = client.get(f"/api/snapshots/{seed.hash['gh0']}")
    assert r.status_code == 410
    assert "retention" in r.json()["detail"] and r.json()["deletion_state"] == "raw_dropped"
    r = client.get(f"/api/snapshots/{seed.hash['deleted']}?evidence={seed.ev['deleted']}")
    assert r.status_code == 410
    assert "deleted upstream" in r.json()["detail"]
    assert HANDLE_MARKER not in r.text
    events = [row[0] for row in audit_rows(db)]
    assert events.count("snapshot_gone") == 2


def test_r13_2_snapshot_hash_mismatch_is_not_served(env):
    client, seed, db = env
    login(client)
    r = client.get(f"/api/snapshots/{seed.hash['tampered']}")
    assert r.status_code == 500
    assert "edited after capture" not in r.text
    assert audit_rows(db)[-1][0] == "snapshot_integrity_failure"


def test_r13_2_snapshot_unknown_or_bad_hash_is_404(env):
    client, seed, _ = env
    login(client)
    assert client.get("/api/snapshots/" + "a" * 64).status_code == 404
    assert client.get("/api/snapshots/NOTAHASH").status_code == 404
    # evidence id that does not reference this hash
    r = client.get(f"/api/snapshots/{seed.hash['page']}?evidence={seed.ev['search']}")
    assert r.status_code == 404


# --- privacy: no handles in any JSON response ------------------------------------------------
def test_no_handles_in_any_json_response(env):
    client, seed, _ = env
    login(client)
    paths = [
        "/api/cases",
        "/api/cases?sort=velocity",
        f"/api/cases/{CASE1}",
        f"/api/cases/{CASE1}/timeline",
        f"/api/cases/{CASE1}/timeline?bucket=day",
        f"/api/cases/{CASE1}/evidence?limit=1000",
        f"/api/cases/{CASE2}",
        *[f"/api/evidence/{e}" for e in seed.ev.values()],
        f"/api/snapshots/{seed.hash['deleted']}",
        f"/api/snapshots/{seed.hash['gh0']}",
    ]
    for path in paths:
        r = client.get(path)
        assert r.headers["content-type"].startswith("application/json"), path
        assert HANDLE_MARKER not in r.text, path
    body = client.get(f"/api/cases/{CASE1}/timeline").json()
    story = body["hn"]["stories"][0]
    assert story["url"] == "[profile:github]"
    assert "@[handle]" in story["title"]


# --- UI serving ------------------------------------------------------------------------------
def test_ui_spa_fallback_and_security_headers(env):
    client, _, _ = env
    for path in ("/", "/cases", f"/cases/{CASE1}", "/login"):
        r = client.get(path)
        assert r.status_code == 200 and "root" in r.text, path
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
        assert r.headers["x-content-type-options"] == "nosniff"
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/../pyproject.toml").status_code in (200, 404)
    assert "hatchling" not in client.get("/..%2Fpyproject.toml").text


# --- D1 performance budget (p95 <= 2 s at 5,000 evidence items; smoke, not a benchmark) --------
def test_d1_perf_5000_evidence_items(env):
    client, _, db = env
    db.conn.execute(
        """
        INSERT INTO evidence (id, source, url, fetched_at, content_hash, snapshot_ref, reliability,
                              terms_basis, retention_class, collector_version, case_id, repo_id)
        SELECT 'ev_' || lpad(to_hex(g), 24, '0'), 'synthetic',
               'https://example.org/item/' || g, %s::timestamptz + g * interval '1 minute',
               encode(sha256(g::text::bytea), 'hex'),
               'local:sha256/00/00/' || encode(sha256(g::text::bytea), 'hex'),
               'medium', 'synthetic', 'project_level', 'test/1', %s, 'github:1000001'
        FROM generate_series(1, 5000) g
        """,
        (T0, CASE1),
    )
    db.conn.execute("ANALYZE")
    login(client)
    for path in (
        f"/api/cases/{CASE1}",
        f"/api/cases/{CASE1}/timeline",
        f"/api/cases/{CASE1}/evidence?sort=reliability",
        "/api/cases?sort=velocity",
    ):
        t = time.perf_counter()
        r = client.get(path)
        assert r.status_code == 200, path
        assert time.perf_counter() - t < 2.0, path
    assert client.get(f"/api/cases/{CASE1}/evidence").json()["total"] >= 5000
