"""M22 D7 `/api/briefs/{id}/shortlist` (R4.7, R4.11): behind the login, same-origin JSON writes,
decisions logged with reason, role `user`, `via: ui` and time, precision shown, reference cases
to confirm, finalize gated and audited by event only. Synthetic brief and candidates.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from pigtail.briefs.candidates import Candidate, CandidateStore, named_ref
from pigtail.briefs.shortlist import Shortlist
from pigtail.briefs.store import BriefStore
from tests.integration.test_api_briefs_m12 import BID, env, example, login, password_hash

pytestmark = pytest.mark.db
__all__ = ["env", "password_hash"]  # fixtures reused from the M12 API tests

NOW = datetime(2026, 9, 25, 18, 0, tzinfo=UTC)
SAME = {"Origin": "http://localhost", "Sec-Fetch-Site": "same-origin"}


def seed(data: Any, db: Any) -> None:
    db.conn.autocommit = True
    brief = BriefStore(data / "briefs").get(BID).brief
    st = CandidateStore(db.conn, BID, 1)
    rows = [
        ("org-s/lint-a", "relevant", 0),
        ("org-s/lint-b", "relevant", 0),
        ("org-s/maybe-c", "uncertain", 1),
        ("org-s/web-d", "not_relevant", 2),
    ]
    for name, verdict, dist in rows:
        ref = f"gh:{name}"
        st.upsert(
            Candidate(
                ref=ref,
                repo_full_name=name,
                sources=[{"source": "github_keyword", "term": "config linter"}],
                metadata={"description": f"synthetic {name}", "stars": 50},
            ),
            brief_run_id=None,
            now=NOW,
        )
        st.set_verdict(
            ref,
            verdict=verdict,
            reason="synthetic reason",
            distance=dist,  # type: ignore[arg-type]
            model_panel="field",
            rubric_version="rubric-v1-test",
            provenance={},
            judged_at=NOW,
            brief_run_id=None,
        )
    st.upsert(
        Candidate(
            ref=named_ref("reference", 0),
            repo_full_name=None,
            panel="reference",
            named_index=0,
            resolution="unresolved",
            resolution_rule="no_launch_post_found",
            matches=[
                {
                    "full_name": "org-x/ref-1",
                    "url": "https://github.com/org-x/ref-1",
                    "description": "one line",
                    "stars": 12,
                }
            ],
            sources=[{"source": "brief_reference", "rule": "no_launch_post_found"}],
        ),
        brief_run_id=None,
        now=NOW,
    )
    Shortlist(db.conn, brief).ensure(None)


def test_d7_shortlist_review_through_the_api(env):
    client, data, db = env
    assert client.get(f"/api/briefs/{BID}/shortlist").status_code == 401
    login(client)
    assert client.post("/api/briefs", json={"brief": example()}).status_code == 201
    assert client.get(f"/api/briefs/{BID}/shortlist").json()["status"] is None
    seed(data, db)
    v = client.get(f"/api/briefs/{BID}/shortlist").json()
    assert v["status"] == "in_review" and v["counts"]["undecided"] == 3
    assert v["reference_cases_to_confirm"][0]["named_as"] == "Example Schema Checker"
    assert v["reference_cases_to_confirm"][0]["matches"][0]["stars"] == 12
    f = client.get(f"/api/briefs/{BID}/shortlist", params={"verdict": "uncertain"}).json()
    assert [c["candidate_ref"] for c in f["candidates"]] == ["gh:org-s/maybe-c"]

    url = f"/api/briefs/{BID}/shortlist/decisions"
    body = {"decision": "accept", "reason": "fits", "candidates": ["org-s/lint-a"]}
    plain = client.post(url, content=b"x", headers={**SAME, "Content-Type": "text/plain"})
    assert plain.status_code in (415, 422)  # refused before any write
    assert client.post(url, json=body, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post(url, json={**body, "reason": ""}, headers=SAME).status_code == 422
    r = client.post(url, json=body, headers=SAME)
    assert r.status_code == 200 and r.json()["precision"]["value"] == 1.0
    r = client.post(
        url,
        json={"decision": "reject", "reason": "not a linter", "candidates": ["gh:org-s/lint-b"]},
        headers=SAME,
    )
    assert r.json()["precision"]["value"] == 0.5 and r.json()["precision"]["meets_target"] is False
    r = client.post(
        url, json={"decision": "reject", "reason": "bulk", "verdict": "uncertain"}, headers=SAME
    )
    assert r.json()["decisions"] == 1
    r = client.post(
        f"/api/briefs/{BID}/shortlist/add",
        headers=SAME,
        json={
            "url": "https://github.com/org-x/ref-1",
            "reason": "the launch repo",
            "resolves": "named:reference:0",
        },
    )
    assert r.status_code == 200 and r.json()["candidate_ref"] == "gh:org-x/ref-1"
    bad = client.post(
        f"/api/briefs/{BID}/shortlist/add",
        headers=SAME,
        json={"url": "https://example.com/x", "reason": "r"},
    )
    assert bad.status_code == 422

    fin = client.post(f"/api/briefs/{BID}/shortlist/finalize", json={}, headers=SAME)
    assert fin.status_code == 200, fin.text
    assert fin.json()["shortlisted"] == 2 and fin.json()["precision"]["value"] == 0.5
    again = client.post(url, json=body, headers=SAME)
    assert again.status_code == 409 and "final" in again.json()["detail"]
    rows = db.conn.execute(
        "SELECT decision, reason, reviewer_role, via, decided_at IS NOT NULL"
        " FROM shortlist_decision ORDER BY id"
    ).fetchall()
    assert [r[0] for r in rows] == ["accept", "reject", "reject", "add"]
    assert all(r[1] and r[2] == "user" and r[3] == "ui" and r[4] for r in rows)
    events = [r[0] for r in db.conn.execute("SELECT event FROM ui_audit_log ORDER BY at, id")]
    assert events.count("shortlist_decision") == 6 and events.count("shortlist_finalize") == 1
    # audited by event only: no repo names or reasons in the audit trail
    audit = str(db.conn.execute("SELECT json_agg(t) FROM ui_audit_log t").fetchone()[0])
    assert "org-s" not in audit and "fits" not in audit
