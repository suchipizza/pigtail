"""CB-13c: a repo opt-out reaches every row keyed to the repo, in every table (synthetic data).

`REPO_TABLES` (`pigtail.privacy.deletion`) is the registry `purge_repo` works from. The schema
test below fails when a table gets a repo key column that is not registered, so a future table
cannot be forgotten.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from psycopg import sql

from pigtail.capture.snapshots import LocalSnapshotStore
from pigtail.llm.store import LLMStore
from pigtail.privacy import requests
from pigtail.privacy.deletion import REPO_TABLES, DeletionLog, RepoTable
from tests.integration.test_privacy_ops import put_ev

pytestmark = pytest.mark.db

NOW = datetime(2026, 9, 20, tzinfo=UTC)
X = (1000009, "org-a/repo-9")  # opts out
Y = (1000003, "org-c/repo-3")  # must stay untouched
API = "https://api.github.com"

# A column whose name says it keys rows to a repository. `repos.id` is the key itself.
REPO_KEY_COLUMN_RE = r"(^|_)repo(_|$)|^full_name$|^host_id$|name_hash|name_key"


def repo_key_columns(db: Any) -> set[tuple[str, str]]:
    rows = db.conn.execute(
        """
        SELECT c.table_name, c.column_name FROM information_schema.columns c
        JOIN information_schema.tables t
          ON t.table_schema = c.table_schema AND t.table_name = c.table_name
        WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'
          AND c.column_name ~ %s
        """,
        (REPO_KEY_COLUMN_RE,),
    ).fetchall()
    return {(str(t), str(c)) for t, c in rows}


def test_cb13c_every_repo_keyed_column_is_registered(capture_db):
    """Schema introspection: every repo key column of every table is in `REPO_TABLES`."""
    found = repo_key_columns(capture_db)
    registered = {(t.table, t.column) for t in REPO_TABLES}
    missing = found - registered
    assert not missing, (
        f"repo-keyed columns not registered in REPO_TABLES (pigtail.privacy.deletion): "
        f"{sorted(missing)}; decide delete / clear / evidence / final for each (CB-13c)"
    )
    # and nothing registered is stale: every registered column exists
    cols = {
        (str(t), str(c))
        for t, c in capture_db.conn.execute(
            "SELECT table_name, column_name FROM information_schema.columns"
            " WHERE table_schema = 'public'"
        ).fetchall()
    }
    assert registered <= cols, sorted(registered - cols)
    assert {t.action for t in REPO_TABLES} <= {"delete", "clear", "evidence", "final"}
    assert all(t.clear_sql for t in REPO_TABLES if t.action == "clear")
    # the guard works: a new table with a repo key column shows up as unregistered
    capture_db.conn.execute("CREATE TABLE future_repo_metric (repo_host_id bigint, v int)")
    assert ("future_repo_metric", "repo_host_id") in repo_key_columns(capture_db) - registered


def seed(db: Any, store: LocalSnapshotStore, repo: tuple[int, str]) -> dict[str, Any]:
    """One row per repo-keyed table for `repo`, and the evidence behind them."""
    hid, name = repo
    key = f"github:{hid}"
    t = NOW - timedelta(days=3)
    db.conn.execute(
        "INSERT INTO repos (id, host, host_id, full_name, first_seen_at)"
        " VALUES (%s, 'github', %s, %s, %s)",
        (key, hid, name, t),
    )
    case = f"case_{hid}"
    db.conn.execute(
        "INSERT INTO cases (id, repo_id, opened_at, trigger, status)"
        " VALUES (%s, %s, %s, 'velocity', 'live')",
        (case, key, t),
    )
    page = put_ev(
        db, store, f"repo page {hid}".encode(), fetched_at=t, retention_class="project_level",
        repo_id=key,
    )  # fmt: skip
    hist_url = f"{API}/repos/{name}/stargazers/history?per_page=100&page=1"
    hist = put_ev(
        db, store, f"history {hid}".encode(), fetched_at=t, retention_class="project_level",
        source="gh_test", url=hist_url,
    )  # fmt: skip
    search = put_ev(
        db, store, f"hn search {hid}".encode(), fetched_at=t, source="hn_algolia",
        url=f"https://hn.algolia.com/api/v1/search_by_date?query={name}",
    )  # fmt: skip
    q = db.conn.execute
    q(
        "INSERT INTO repo_star_daily (repo_host_id, day, stars_net, week_label, day_boundary_tz,"
        " fetched_at, evidence_id) VALUES (%s, %s, 3, 'w', 'UTC', %s, %s)",
        (hid, date(2026, 9, 17), t, hist.id),
    )
    q(
        "INSERT INTO star_history_fetch (repo_host_id, fetched_at, per_page, pages, weeks,"
        " complete) VALUES (%s, %s, 100, 1, 1, true)",
        (hid, t),
    )
    q(
        "INSERT INTO repo_event_actor (repo_host_id, event_id, event_type, actor_pseudonym,"
        " is_bot, created_at, observed_at) VALUES (%s, %s, 'WatchEvent', %s, false, %s, %s)",
        (hid, f"e{hid}", "p_" + f"{hid:016x}", t, t),
    )
    q(
        "INSERT INTO repo_event_poll (repo_host_id, polled_at, status, pages)"
        " VALUES (%s, %s, 200, 1)",
        (hid, t),
    )
    q("INSERT INTO repo_event_daily_agg (repo_host_id, day) VALUES (%s, %s)", (hid, t.date()))
    q(
        "INSERT INTO github_http_cache (url, etag, content_hash, evidence_id, fetched_at)"
        " VALUES (%s, 'W/\"x\"', %s, %s, %s)",
        (hist_url, hist.content_hash, hist.id, t),
    )
    q(
        "INSERT INTO hn_mention (repo_full_name, item_id, item_type, match_kind, repo_id,"
        " case_id, evidence_id, first_seen_at, last_seen_at, title)"
        " VALUES (%s, %s, 'story', 'url', %s, %s, %s, %s, %s, 'Show HN: synthetic')",
        (name, hid, key, case, search.id, t, t),
    )
    q(
        "INSERT INTO hn_story (item_id, title, url, repo_full_name, repo_id, first_seen_at,"
        " last_seen_at) VALUES (%s, 'Show HN: synthetic', %s, %s, %s, %s, %s)",
        (hid, f"https://github.com/{name}", name, key, t, t),
    )
    q(
        "INSERT INTO launch_mode_window (scope, repo_id, starts_at, ends_at)"
        " VALUES ('tracked_project', %s, %s, %s)",
        (key, t, t + timedelta(days=14)),
    )
    q(  # M12: a cached per-repo stage result and a shortlist decision about the repo
        "INSERT INTO brief_stage_cache (key, stage, stage_version, item_ref, repo_id, input_hash,"
        " result, result_hash, first_brief_id, first_brief_version)"
        " VALUES (%s, 'evidence', 'v0', %s, %s, 'h', '{}', %s, 'synthetic-brief', 1)",
        (f"{hid:064x}", key, key, "0" * 64),
    )
    q(
        "INSERT INTO shortlist_decision (brief_id, brief_version, candidate_ref,"
        " candidate_repo_id, decision, reason, reviewer_role)"
        " VALUES ('synthetic-brief', 1, %s, %s, 'accept', 'synthetic', 'user')",
        (key, key),
    )
    return {"key": key, "evidence": [page, hist, search]}


def rows_of(db: Any, t: RepoTable, repo: tuple[int, str]) -> int:
    hid, name = repo
    col = sql.Identifier(t.column)
    if t.match == "id":
        cond, p = sql.SQL("{} = %s").format(col), f"github:{hid}"
    elif t.match == "host_id":
        cond, p = sql.SQL("{} = %s").format(col), hid
    elif t.match == "name":
        cond, p = sql.SQL("lower({}) = %s").format(col), name
    else:
        cond, p = sql.SQL("lower({}) LIKE %s").format(col), f"{API}/repos/{name}%"
    q = sql.SQL("SELECT count(*) FROM {} WHERE ").format(sql.Identifier(t.table)) + cond
    row = db.conn.execute(q, (p,)).fetchone()
    return int(row[0]) if row else 0


def test_cb13c_purge_repo_reaches_every_registered_table(capture_db, tmp_path, pz):
    db = capture_db
    store = LocalSnapshotStore(tmp_path / "snap")
    x = seed(db, store, X)
    seed(db, store, Y)
    # the seed covers every registered column (a new registration needs a seeded row here)
    unseeded = [(t.table, t.column) for t in REPO_TABLES if rows_of(db, t, X) == 0]
    assert not unseeded, f"seed() has no row for {unseeded}"
    before_y = {(t.table, t.column): rows_of(db, t, Y) for t in REPO_TABLES}
    llm = LLMStore(":memory:")
    for ev in x["evidence"]:
        llm.cache_put(f"k{ev.id}", {"q": 1}, "m", evidence_id=ev.id)

    res = requests.optout_repo(
        db, store, platform="github", repo_key=x["key"], pz=pz, llm_store=llm
    )

    left = {(t.table, t.column): rows_of(db, t, X) for t in REPO_TABLES}
    assert left == dict.fromkeys(left, 0), {k: v for k, v in left.items() if v}
    # cleared rows are kept without the repo link (HN rank history keeps the item id)
    assert db.conn.execute(
        "SELECT title, url, repo_full_name, repo_id, content_cleared_at IS NOT NULL"
        " FROM hn_story WHERE item_id = %s",
        (X[0],),
    ).fetchone() == (None, None, None, None, True)
    # evidence: raw bytes and rows gone, derived LLM cache purged
    for ev in x["evidence"]:
        assert not store.exists(ev.content_hash)
        assert db.conn.execute("SELECT 1 FROM evidence WHERE id = %s", (ev.id,)).fetchone() is None
    assert res.counts["evidence_deleted"] == 3
    assert res.counts["llm_cache_rows_deleted"] == 3
    assert res.counts["repo_star_daily_rows_deleted"] == 1 and res.counts["cases_deleted"] == 1
    assert res.counts["launch_mode_window_rows_deleted"] == 1
    # the other repo is untouched
    assert {(t.table, t.column): rows_of(db, t, Y) for t in REPO_TABLES} == before_y
    # every table with deleted rows has a tombstone
    logged = {
        r[0]
        for r in db.conn.execute(
            "SELECT DISTINCT target FROM deletion_log WHERE reason = 'objection'"
        ).fetchall()
    }
    assert {t.table for t in REPO_TABLES if t.action in ("delete", "final")} <= logged
    # re-applying (e.g. after a restore) finds nothing more
    totals = requests.reapply_refusals(db, store, pz, llm_store=llm)
    assert sum(v for k, v in totals.items() if k.startswith("repo_")) == 0


def test_cb13c_shared_evidence_is_kept(capture_db, tmp_path, pz):
    """A snapshot another repo's rows still need (e.g. an HN search page mentioning both) is
    not deleted; only the opted-out repo's rows are."""
    db = capture_db
    store = LocalSnapshotStore(tmp_path / "snap")
    x = seed(db, store, X)
    seed(db, store, Y)
    shared = x["evidence"][2]  # the HN search page
    db.conn.execute(
        "UPDATE hn_mention SET evidence_id = %s WHERE repo_full_name = %s", (shared.id, Y[1])
    )
    log = DeletionLog(db, "objection")
    requests.purge_repo(db, store, x["key"], log)
    assert store.exists(shared.content_hash)
    assert db.conn.execute("SELECT 1 FROM evidence WHERE id = %s", (shared.id,)).fetchone()
