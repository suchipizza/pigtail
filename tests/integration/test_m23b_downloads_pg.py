"""M23b (ADR-090; ADR-088.3): `pigtail brief downloads` fills exploratory download outcomes of a
stored selection's view-A cases, without touching the selection. Synthetic repos, packages and
counts; fake APIs, no network."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from psycopg.types.json import Jsonb

from pigtail.briefs import downloads as dl
from pigtail.capture.snapshots import LocalSnapshotStore
from pigtail.privacy.deletion import DeletionLog, drop_after_parse
from tests.downloads_fake import NPM_GAP_DAY, FakeRegistries, npm_latest, package_json

pytestmark = pytest.mark.db

SEL = "sel_" + "ab" * 10
AS_OF = date(2026, 9, 28)
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)

# (repo, role, pair, anchor time, language)
CASES = [
    ("org-x/repo-npm", "winner", 1, datetime(2026, 8, 10, 15, 0, tzinfo=UTC), "TypeScript"),
    ("org-x/repo-py", "matched_loser", 1, datetime(2026, 6, 1, 2, 0, tzinfo=UTC), "Python"),
    ("org-x/repo-late", "winner", 2, datetime(2026, 9, 20, 9, 0, tzinfo=UTC), "JavaScript"),
    ("org-x/repo-old", "matched_loser", 2, datetime(2026, 3, 1, 9, 0, tzinfo=UTC), "Python"),
    ("org-x/repo-none", "exemplar", 3, datetime(2026, 7, 1, 9, 0, tzinfo=UTC), "Go"),
    ("org-x/repo-pool", "loser_pool_unmatched", None, datetime(2026, 7, 1, tzinfo=UTC), "Go"),
]


def _fake() -> FakeRegistries:
    npm_url = "git+https://github.com/org-x/repo-npm.git"
    late_url = "https://github.com/org-x/repo-late"

    def py_series(first: date) -> dict[date, int]:
        # a day without downloads has no row in pypistats
        return {first + timedelta(days=i): 2 for i in range(60) if i != 4}

    return FakeRegistries(
        files={
            "org-x/repo-npm": {"package.json": package_json("fake-npm-a")},
            "org-x/repo-py": {"pyproject.toml": (
                '[project]\nname = "fake_py_b"\n[project.urls]\n'
                'Source = "https://github.com/org-x/repo-py"\n')},
            "org-x/repo-late": {"package.json": package_json("fake-late")},
            "org-x/repo-old": {"setup.cfg": (
                "[metadata]\nname = fake-old\nurl = https://github.com/org-x/repo-old\n")},
            "org-x/repo-pool": {"package.json": package_json("fake-pool")},
        },
        npm_latest={
            "fake-npm-a": npm_latest("fake-npm-a", npm_url),
            "fake-late": npm_latest("fake-late", late_url),
            "fake-pool": npm_latest("fake-pool", "https://github.com/org-x/repo-pool"),
        },
        npm_daily={
            "fake-npm-a": lambda d: 100,
            "fake-late": lambda d: 7,
            "fake-pool": lambda d: 1,
        },
        pypi={"fake-py-b": py_series(date(2026, 6, 1)), "fake-old": py_series(date(2026, 4, 1))},
    )  # fmt: skip


def _seed(conn: Any) -> None:
    conn.execute(
        "INSERT INTO brief_selection (id, brief_id, brief_version, brief_hash, data_version, as_of,"
        " selection_version, outcome_model_version, params_version, inputs_hash, result_hash,"
        " params, summary, balance, sensitivity) VALUES (%s, 'brief_synthetic', 1, 'h', 'dv1-x',"
        " %s, 'selection-v13', '2.1', '1.1.0', %s, %s, '{}', '{}', '{}', '{}')",
        (SEL, AS_OF, "1" * 64, "2" * 64),
    )
    for i, (name, role, pair, at, lang) in enumerate(CASES):
        detail = {
            "anchor": {"type": "launch", "at": at.isoformat(), "precision": "hour",
                       "source": "show_hn", "via": "discovery"},
            "covariates": {"language": lang},
            "values": {"att.stars_launch@0-2": {"status": "observed", "value": 10.0}},
        }  # fmt: skip
        for view in ("follow_through", "launch"):
            conn.execute(
                "INSERT INTO brief_selection_case (selection_id, view, candidate_ref,"
                " repo_full_name, repo_host_id, repo_id, panel, role, pair_id, detail)"
                " VALUES (%s, %s, %s, %s, %s, %s, 'field', %s, %s, %s)",
                (SEL, view, "gh:" + name, name, 8000 + i, f"github:{8000 + i}", role, pair,
                 Jsonb(detail)),
            )  # fmt: skip


def _deps(db: Any, store: LocalSnapshotStore, fake: FakeRegistries) -> dl.FillDeps:
    dlog = DeletionLog(db, "retention")

    def drop(f: Any) -> None:
        drop_after_parse(db, store, f.evidence.id, f.content_hash, dlog)

    sink = db.upsert_evidence
    return dl.FillDeps(
        conn=db.conn,
        files=dl.RepoFiles(fake.github(store, sink), drop),
        npm=fake.npm(store, sink),
        pypi=fake.pypistats(store, sink),
        drop_raw=drop,
        reuse=dl.same_day_reuse(db.conn, store, NOW.date()),
    )


def _selection_state(conn: Any) -> Any:
    return (
        conn.execute("SELECT result_hash, summary FROM brief_selection WHERE id = %s", (SEL,))
        .fetchone(),
        conn.execute(
            "SELECT view, candidate_ref, detail FROM brief_selection_case"
            " WHERE selection_id = %s ORDER BY 1, 2", (SEL,)
        ).fetchall(),
    )  # fmt: skip


def test_fill_writes_exploratory_values_for_view_a_pairs_m23b(capture_db, tmp_path: Path) -> None:
    db = capture_db
    _seed(db.conn)
    store = LocalSnapshotStore(tmp_path / "snap")
    fake = _fake()
    before = _selection_state(db.conn)

    p = dl.plan(db.conn, SEL, dl.DEFAULT_ROLES)
    assert p["cases"] == 5 and p["cases_to_fetch"] == {"npm": 5, "pypi": 5}
    assert p["cost_usd"] == 0.0 and p["llm_calls"] == 0

    res = dl.fill(_deps(db, store, fake), SEL, roles=dl.DEFAULT_ROLES, as_of=AS_OF, now=NOW)
    assert res.cases == 5
    got = dl.secondary_outcomes(db.conn, SEL)
    assert "gh:org-x/repo-pool" not in got  # not a pair role: not filled by default
    assert set(got) == {f"gh:{c[0]}" for c in CASES[:5]}
    assert all(set(v) == set(dl.SECONDARY_METRICS) for v in got.values())

    npm_a = got["gh:org-x/repo-npm"]
    launch = npm_a["adopt.npm_downloads_launch@0-2"]
    assert (launch["status"], launch["value"], launch["tag"]) == ("observed", 300.0, "verified")
    assert launch["exploratory"] is True and launch["used_by_sort"] is False
    assert launch["preregistered"] is False and "not used by the sort" in launch["label"]
    assert launch["window"] == {"first_day": "2026-08-10", "start": "2026-08-10",
                                "end": "2026-08-12"}  # fmt: skip
    assert launch["package"] == "fake-npm-a" and launch["terms_basis"] == "TM-08"
    assert launch["mapping"]["status"] == "mapped" and launch["day_boundary"] == "UTC"
    kinds = {e["kind"] for e in launch["evidence"]}
    assert kinds == {"manifest", "registry_manifest", "downloads", "reference_downloads"}
    # the follow-through window holds the registry-wide outage day: unknown, not a zero
    assert NPM_GAP_DAY.isoformat() > "2026-08-12"
    follow = npm_a["adopt.npm_downloads_follow@3-30"]
    assert (follow["status"], follow["reason"]) == ("unknown", "source_gap_day")
    assert npm_a["adopt.pypi_downloads_launch@0-2"]["status"] == "not_applicable"

    py = got["gh:org-x/repo-py"]
    pl, pf = py["adopt.pypi_downloads_launch@0-2"], py["adopt.pypi_downloads_follow@3-30"]
    assert (pl["status"], pl["value"]) == ("observed", 6.0)
    assert (pf["status"], pf["value"]) == ("observed", 52.0)  # day 4 has no row: 0
    assert pf["coverage"]["zero_filled_days"] == 1
    assert pf["coverage"]["coverage_start"] == "2026-04-01"
    assert pl["terms_basis"] == "TM-35" and "CC BY 4.0" in pl["attribution"]
    assert py["adopt.npm_downloads_launch@0-2"]["reason"] == "no_manifest"

    late = got["gh:org-x/repo-late"]
    assert late["adopt.npm_downloads_launch@0-2"]["value"] == 21.0
    assert late["adopt.npm_downloads_follow@3-30"]["status"] == "pending"

    old = got["gh:org-x/repo-old"]["adopt.pypi_downloads_launch@0-2"]
    assert (old["status"], old["reason"]) == ("unknown", "outside_source_history")
    assert old["coverage"]["coverage_start"] == "2026-04-01"
    none = got["gh:org-x/repo-none"]
    assert {r["status"] for r in none.values()} == {"not_applicable"}

    # snapshot or drop: every cited evidence id exists; manifests lost their raw bytes
    for ev in launch["evidence"]:
        row = db.conn.execute(
            "SELECT content_hash, deletion_state FROM evidence WHERE id = %s",
            (ev["evidence_id"],),
        ).fetchone()
        assert row is not None and row[0] == ev["content_hash"]
        want = "raw_dropped" if ev["raw"] == "dropped" else "present"
        assert row[1] == want
        assert store.exists(row[0]) == (want == "present")

    # the stored selection is untouched
    assert _selection_state(db.conn) == before


def test_fill_is_idempotent_and_resumes_m23b(capture_db, tmp_path: Path) -> None:
    db = capture_db
    _seed(db.conn)
    store = LocalSnapshotStore(tmp_path / "snap")
    fake = _fake()
    dl.fill(_deps(db, store, fake), SEL, roles=dl.DEFAULT_ROLES, as_of=AS_OF, now=NOW)
    first = dl.secondary_outcomes(db.conn, SEL)
    n_requests = len(fake.log)

    # counts change upstream; a re-run the same day re-fetches only non-final ecosystems and
    # never overwrites an observed value
    fake.npm_daily["fake-npm-a"] = lambda d: 999
    fake.npm_daily["fake-late"] = lambda d: 8
    p = dl.plan(db.conn, SEL, dl.DEFAULT_ROLES)
    assert p["cases_to_fetch"] == {"npm": 2, "pypi": 1}  # repo-npm (gap), repo-late, repo-old
    dl.fill(_deps(db, store, fake), SEL, roles=dl.DEFAULT_ROLES, as_of=AS_OF, now=NOW)
    again = dl.secondary_outcomes(db.conn, SEL)
    assert again["gh:org-x/repo-npm"]["adopt.npm_downloads_launch@0-2"]["value"] == 300.0
    assert again["gh:org-x/repo-late"]["adopt.npm_downloads_launch@0-2"]["value"] == 21.0
    assert again["gh:org-x/repo-py"] == first["gh:org-x/repo-py"]
    new = fake.log[n_requests:]
    assert not any("fake-py-b" in u for u in new)  # final: not fetched again
    # pypistats same-day reuse: the reference series is not requested twice the same day
    assert sum("/api/packages/pip/" in u for u in fake.log) == 1

    # later, the pending follow-through window settles
    dl.fill(_deps(db, store, fake), SEL, roles=dl.DEFAULT_ROLES, as_of=date(2026, 10, 25),
            now=NOW + timedelta(days=27))  # fmt: skip
    later = dl.secondary_outcomes(db.conn, SEL)["gh:org-x/repo-late"]
    assert later["adopt.npm_downloads_follow@3-30"]["status"] == "observed"
    assert later["adopt.npm_downloads_launch@0-2"]["value"] == 21.0  # kept, not re-read


def test_all_roles_and_missing_connector_m23b(capture_db, tmp_path: Path) -> None:
    db = capture_db
    _seed(db.conn)
    store = LocalSnapshotStore(tmp_path / "snap")
    deps = _deps(db, store, _fake())
    deps.pypi = None  # e.g. PIGTAIL_CONNECTOR_PYPISTATS_ENABLED=false
    dl.fill(deps, SEL, roles=None, as_of=AS_OF, now=NOW)
    got = dl.secondary_outcomes(db.conn, SEL)
    assert "gh:org-x/repo-pool" in got
    assert got["gh:org-x/repo-pool"]["adopt.npm_downloads_launch@0-2"]["value"] == 3.0
    r = got["gh:org-x/repo-py"]["adopt.pypi_downloads_launch@0-2"]
    assert (r["status"], r["reason"]) == ("unknown", "connector_off")


def test_cli_dry_run_shows_estimate_and_writes_nothing_m23b(
    capture_db, pg_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from pigtail.cli import main

    _seed(capture_db.conn)
    monkeypatch.setenv("DATABASE_URL", pg_url)
    assert main(["brief", "downloads", "brief_synthetic", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "requests (upper bound)" in out and "USD 0.00" in out and "DRY RUN" in out
    n = capture_db.conn.execute("SELECT count(*) FROM brief_secondary_outcome").fetchone()
    assert n == (0,)
    assert main(["brief", "downloads", "no-such-brief", "--dry-run"]) == 1
