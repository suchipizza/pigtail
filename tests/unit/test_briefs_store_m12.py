"""M12 private brief store and diff (PRD R18.3, R18.4, R18.9; D7 versioning acceptance).

Synthetic briefs only (the repo's example, under throwaway ids in tmp_path).
"""

from __future__ import annotations

import stat
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pigtail.briefs.diff import diff_briefs
from pigtail.briefs.model import Brief, load_brief_text
from pigtail.briefs.store import (
    BriefExists,
    BriefNotFound,
    BriefStore,
    VersionConflict,
)

EXAMPLE = Path(__file__).resolve().parents[2] / "docs" / "examples" / "brief-example.yaml"


def brief(brief_id: str = "synthetic-one", **project: str) -> Brief:
    b = load_brief_text(EXAMPLE.read_text())
    upd = b.project.model_copy(update=project) if project else b.project
    return b.model_copy(update={"brief_id": brief_id, "project": upd})


class Clock:
    def __init__(self) -> None:
        self.t = datetime(2026, 9, 26, 12, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.t += timedelta(minutes=1)
        return self.t


@pytest.fixture
def store(tmp_path: Path) -> BriefStore:
    return BriefStore(tmp_path / "data" / "briefs", clock=Clock())


def mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def test_r18_9_store_is_private_0700_dirs_0600_files(store: BriefStore):
    s = store.create(brief())
    assert mode(store.root) == 0o700
    assert mode(s.path.parent) == 0o700
    assert mode(s.path) == 0o600
    assert s.path == store.root / "synthetic-one" / "v0001.yaml"


def test_r18_9_loose_existing_dir_is_tightened(tmp_path: Path):
    root = tmp_path / "briefs"
    root.mkdir(mode=0o755)
    BriefStore(root).create(brief())
    assert mode(root) == 0o700


def test_r18_4_every_edit_is_a_new_immutable_version(store: BriefStore):
    v1 = store.create(brief())
    v1_bytes = v1.path.read_bytes()
    v2, created = store.save_version(brief(name="Renamed"), base_version=1)
    assert created and v2.version == 2 and v2.brief.supersedes == 1
    assert v2.brief.created_at == v1.brief.created_at
    assert v2.brief.edited_at and v1.brief.edited_at and v2.brief.edited_at > v1.brief.edited_at
    assert v1.path.read_bytes() == v1_bytes  # old version unchanged and readable
    assert store.get("synthetic-one", 1).brief.project.name == "Example Config Linter"
    assert store.get("synthetic-one").brief.project.name == "Renamed"
    assert [v.version for v in store.versions("synthetic-one")] == [1, 2]


def test_r18_4_unchanged_content_creates_no_version(store: BriefStore):
    store.create(brief())
    same, created = store.save_version(brief())
    assert not created and same.version == 1
    assert [v.version for v in store.versions("synthetic-one")] == [1]


def test_r18_4_stale_edit_is_refused(store: BriefStore):
    store.create(brief())
    store.save_version(brief(name="A"), base_version=1)
    with pytest.raises(VersionConflict):
        store.save_version(brief(name="B"), base_version=1)


def test_r18_4_version_files_are_never_overwritten(store: BriefStore):
    s = store.create(brief())
    stamped = brief(name="X").model_copy(update={"version": 1})
    with pytest.raises(VersionConflict):
        store._write(stamped)
    assert load_brief_text(s.path.read_text()).project.name == "Example Config Linter"


def test_r18_3_several_briefs_per_install(store: BriefStore, tmp_path: Path):
    store.create(brief("synthetic-one"))
    store.create(brief("synthetic-two", name="Second"))
    (store.root / "notes.md").write_text("stray file")  # ignored
    (store.root / "not a brief").mkdir()
    listed = {s.brief_id: s for s in store.list()}
    assert set(listed) == {"synthetic-one", "synthetic-two"}
    assert listed["synthetic-two"].name == "Second"
    with pytest.raises(BriefExists):
        store.create(brief("synthetic-one"))


def test_unknown_or_malicious_ids_are_not_found(store: BriefStore):
    for bad in ("../etc", "nope", "A_B", ""):
        with pytest.raises(BriefNotFound):
            store.get(bad)
    assert store.list() == []


def test_d7_diff_between_any_two_versions(store: BriefStore):
    store.create(brief())
    b2 = brief(name="Renamed")
    b2 = b2.model_copy(
        update={
            "window": b2.window.model_copy(update={"months": 12}),
            "field": b2.field.model_copy(
                update={"include": [*b2.field.include, "language servers for config files"]}
            ),
        }
    )
    store.save_version(b2)
    d = diff_briefs(store.get("synthetic-one", 1).brief, store.get("synthetic-one", 2).brief)
    by = {c.path: c for c in d.changes}
    assert set(by) == {"project.name", "window.months", "field.include"}
    assert (by["window.months"].old, by["window.months"].new) == (18, 12)
    assert by["field.include"].items_added == ["language servers for config files"]
    assert "-  months: 18" in d.unified and "+  months: 12" in d.unified
    assert diff_briefs(b2, b2).changes == []


def test_r18_9_exposure_warning_inside_an_unignored_git_tree(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    store = BriefStore(repo / "data" / "briefs")
    assert store.exposure_warning() is not None
    (repo / ".gitignore").write_text("data/\n")
    assert store.exposure_warning() is None
    assert BriefStore(tmp_path / "outside" / "briefs").exposure_warning() is None
