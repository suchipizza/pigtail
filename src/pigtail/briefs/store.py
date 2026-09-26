"""Private brief store (PRD R18.3, R18.4, R18.9; D7).

Layout, under `PIGTAIL_DATA_DIR/briefs` (default `data/briefs`, gitignored):

    briefs/                 mode 0700
      <brief_id>/           mode 0700, one directory per brief (several briefs per install)
        v0001.yaml          mode 0600, immutable: written once, never overwritten
        v0002.yaml

Every save of changed content creates the next version; saving unchanged content is a no-op
that returns the latest version. A version file is created with `link()` from a private temp
file, so two concurrent saves can't both claim the same number and a reader never sees a
half-written file. Files that don't match the layout (for example notes kept next to a brief)
are ignored.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pigtail.briefs.model import Brief, BriefInvalid, BriefProblem, dump_yaml, load_brief_text

DIR_MODE = 0o700
FILE_MODE = 0o600
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")
_VERSION_FILE = re.compile(r"^v(\d{4,})\.yaml$")


def utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


class BriefNotFound(LookupError):
    pass


class BriefExists(ValueError):
    pass


class VersionConflict(ValueError):
    """The edit was based on an older version than the latest (someone saved in between)."""

    def __init__(self, base: int, latest: int) -> None:
        super().__init__(f"edit based on version {base}, but the latest version is {latest}")
        self.base = base
        self.latest = latest


@dataclass(frozen=True)
class StoredBrief:
    brief: Brief
    yaml_text: str
    path: Path

    @property
    def version(self) -> int:
        assert self.brief.version is not None
        return self.brief.version


@dataclass(frozen=True)
class VersionInfo:
    version: int
    edited_at: datetime | None
    supersedes: int | None
    content_hash: str


@dataclass(frozen=True)
class BriefSummary:
    brief_id: str
    name: str
    latest_version: int
    versions: int
    created_at: datetime | None
    edited_at: datetime | None
    status: str
    content_hash: str


def check_id(brief_id: str) -> str:
    if not _ID.match(brief_id):
        raise BriefNotFound(f"not a brief id: {brief_id!r}")
    return brief_id


class BriefStore:
    def __init__(self, root: Path, *, clock: Callable[[], datetime] = utcnow) -> None:
        self.root = Path(root)
        self.clock = clock

    @classmethod
    def from_data_dir(cls, data_dir: Path) -> BriefStore:
        return cls(Path(data_dir) / "briefs")

    # --- private directories --------------------------------------------------------------------
    def _ensure_dir(self, path: Path) -> None:
        path.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
        os.chmod(path, DIR_MODE)  # also tightens a directory created earlier with a looser mode

    def exposure_warning(self) -> str | None:
        """A warning if the store sits inside a git work tree and isn't git-ignored (R18.9)."""
        # Resolve through the first existing ancestor (the store may not exist yet).
        probe, rest = self.root.absolute(), list[str]()
        while not probe.exists() and probe != probe.parent:
            rest.insert(0, probe.name)
            probe = probe.parent
        probe = probe.resolve()
        root = probe.joinpath(*rest)
        try:
            top = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=probe,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if top.returncode != 0:
                return None
            ignored = subprocess.run(
                ["git", "check-ignore", "-q", str(root / "x.yaml")],
                cwd=top.stdout.strip(),
                capture_output=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if ignored.returncode == 0:
            return None
        return (
            f"brief store {root} is inside a git work tree and not git-ignored; set "
            "PIGTAIL_DATA_DIR outside the repo or ignore it (briefs are private, R18.9)"
        )

    # --- reading ----------------------------------------------------------------------------------
    def _dir(self, brief_id: str) -> Path:
        return self.root / check_id(brief_id)

    def _version_numbers(self, brief_id: str) -> list[int]:
        d = self._dir(brief_id)
        if not d.is_dir():
            return []
        return sorted(
            int(m.group(1))
            for p in d.iterdir()
            if (m := _VERSION_FILE.match(p.name)) and p.is_file()
        )

    def exists(self, brief_id: str) -> bool:
        try:
            return bool(self._version_numbers(brief_id))
        except BriefNotFound:
            return False

    def latest_version(self, brief_id: str) -> int:
        nums = self._version_numbers(brief_id)
        if not nums:
            raise BriefNotFound(f"no brief {brief_id!r}")
        return nums[-1]

    def path_for(self, brief_id: str, version: int) -> Path:
        return self._dir(brief_id) / f"v{version:04d}.yaml"

    def get(self, brief_id: str, version: int | None = None) -> StoredBrief:
        v = version if version is not None else self.latest_version(brief_id)
        path = self.path_for(brief_id, v)
        if not path.is_file():
            raise BriefNotFound(f"no version {v} of brief {brief_id!r}")
        text = path.read_text(encoding="utf-8")
        brief = load_brief_text(text)
        if brief.brief_id != brief_id or brief.version != v:
            raise BriefInvalid(
                [BriefProblem("(file)", f"{path.name} holds {brief.brief_id} v{brief.version}")]
            )
        return StoredBrief(brief, text, path)

    def versions(self, brief_id: str) -> list[VersionInfo]:
        out = []
        for v in self._version_numbers(brief_id):
            b = self.get(brief_id, v).brief
            out.append(VersionInfo(v, b.edited_at, b.supersedes, b.content_hash()))
        if not out:
            raise BriefNotFound(f"no brief {brief_id!r}")
        return out

    def list(self) -> list[BriefSummary]:
        if not self.root.is_dir():
            return []
        out = []
        for d in sorted(self.root.iterdir()):
            if not d.is_dir() or not _ID.match(d.name):
                continue
            nums = self._version_numbers(d.name)
            if not nums:
                continue
            b = self.get(d.name, nums[-1]).brief
            out.append(
                BriefSummary(
                    brief_id=b.brief_id,
                    name=b.project.name,
                    latest_version=nums[-1],
                    versions=len(nums),
                    created_at=b.created_at,
                    edited_at=b.edited_at,
                    status=b.status,
                    content_hash=b.content_hash(),
                )
            )
        return out

    # --- writing ----------------------------------------------------------------------------------
    def create(self, brief: Brief) -> StoredBrief:
        """Store version 1 of a new brief."""
        if self.exists(brief.brief_id):
            raise BriefExists(
                f"brief {brief.brief_id!r} already exists; edit it to make a new version, or "
                "choose another brief_id"
            )
        now = self.clock()
        stamped = brief.model_copy(
            update={"version": 1, "supersedes": None, "created_at": now, "edited_at": now}
        )
        return self._write(stamped)

    def save_version(
        self, brief: Brief, *, base_version: int | None = None
    ) -> tuple[StoredBrief, bool]:
        """Store an edit as the next version. Returns (stored, created).

        Unchanged content returns the latest version with `created=False`. `base_version`
        (the version the edit started from) guards against overwriting a concurrent edit.
        """
        latest = self.get(brief.brief_id)
        if base_version is not None and base_version != latest.version:
            raise VersionConflict(base_version, latest.version)
        if brief.content_hash() == latest.brief.content_hash():
            return latest, False
        stamped = brief.model_copy(
            update={
                "version": latest.version + 1,
                "supersedes": latest.version,
                "created_at": latest.brief.created_at,
                "edited_at": self.clock(),
            }
        )
        return self._write(stamped), True

    def _write(self, brief: Brief) -> StoredBrief:
        assert brief.version is not None
        self._ensure_dir(self.root)
        d = self._dir(brief.brief_id)
        self._ensure_dir(d)
        text = dump_yaml(brief)
        load_brief_text(text)  # round-trip check: what is stored always validates
        final = self.path_for(brief.brief_id, brief.version)
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".yaml", dir=d)
        try:
            os.fchmod(fd, FILE_MODE)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            try:
                os.link(tmp, final)  # fails if the version exists: versions are immutable
            except FileExistsError:
                raise VersionConflict(brief.version - 1, brief.version) from None
        finally:
            Path(tmp).unlink(missing_ok=True)
        return StoredBrief(brief, text, final)
