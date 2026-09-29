"""Registry downloads as an exploratory secondary outcome of view A (M23b; ADR-083.9, ADR-088.3,
ADR-090).

**Not pre-registered, not used by the sort.** The values are filled for a *stored* selection
after the fact (`pigtail brief downloads`), live in their own table (`brief_secondary_outcome`,
migration 0033), and never enter `brief_selection` / `brief_selection_case`, their hashes, the
percentiles or the matching. Every record says so (`exploratory: true`, `used_by_sort: false`).

**Metrics** (absolute download counts, per case, the same anchor as view A):

- `adopt.npm_downloads_launch@0-2`, `adopt.npm_downloads_follow@3-30` (npm, TM-08)
- `adopt.pypi_downloads_launch@0-2`, `adopt.pypi_downloads_follow@3-30` (PyPI via pypistats.org,
  TM-35)

Days `0..2` are launch size and `3..29` follow-through, as `att.stars_launch@0-2` and
`att.stars_follow@3-30` (ADR-083.1). Registries count per **UTC day**, so day 0 is the UTC day
containing T for an hour-precision anchor and the anchor's own day for a day-precision one
(`download_first_day`); stars use the US-Pacific endpoint day, so for T between 00:00 and
~08:00 UTC the download window starts one day later than the star window (`day_boundary: UTC`
on every record). `pending` until the window's last day + `settle_lag` (3 d); `unknown` with a
reason when a day is outside the source's history (`outside_source_history`: npm before
2015-01-10, pypistats before its retained ~180 days, `coverage_start` on every value), on a
source gap day, or when the mapping is ambiguous; `not_applicable` when the repo has no
package in that registry.

**Repo → package mapping** (`mapping-v1`, conservative; current default branch, not the tree at
T, a known limit):

- npm: `package.json` at the repo root (its `name`, unless `private`), plus the packages of its
  declared `workspaces` (`dir/*` patterns and plain paths, at most `MAX_WORKSPACE_PACKAGES`).
  A package counts only when npm's own latest manifest has a `repository` that points back to
  this repo. The value is the root package's when it is confirmed, else the single confirmed
  workspace package; two or more confirmed workspace packages and no root package →
  `unknown` (`ambiguous_multiple_packages`), since monorepo packages depend on each other and a
  sum double-counts.
- PyPI: `pyproject.toml` (`[project]` name and urls, or `[tool.poetry]` name, repository,
  homepage, documentation and urls), else `setup.cfg` (`[metadata]` name, url and
  project_urls). The name counts only when one of the manifest's own URLs points back to this
  repo (pypistats has no metadata to check against, and pigtail has no PyPI JSON memo yet: a
  name held on PyPI by another project would be read, a known limit). `setup.py` is not
  executed or parsed.

Manifests and npm manifests name authors and maintainers, so their raw bytes are dropped right
after parsing (CB-24); the evidence record and its content hash stay. Download series are
aggregate counts and are kept as snapshots. Every value lists the evidence ids and content
hashes it rests on (snapshot or drop).

**Read API for the report step (M24):** `secondary_outcomes(conn, selection_id)` →
`{candidate_ref: {metric: record}}`; `SECONDARY_METRICS` lists the metric ids.
"""

from __future__ import annotations

import configparser
import json
import tomllib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from functools import partial
from typing import Any, Literal

import psycopg
from psycopg.types.json import Jsonb

from pigtail.briefs.outcomes import SETTLE_LAG_DAYS, first_day
from pigtail.briefs.selection import Anchor
from pigtail.connectors.base import Fetched, NotFound
from pigtail.connectors.downloads import (
    NPM_HISTORY_START,
    NPM_REFERENCE_PACKAGE,
    PYPISTATS_REFERENCE_PACKAGE,
    PYPISTATS_SECONDS_PER_REQUEST,
    DailySeries,
    DownloadsParseError,
    NpmDownloadsConnector,
    PypiStatsConnector,
    chunk_ranges,
    parse_npm_latest,
    parse_npm_range,
    parse_pypistats_overall,
    pep503,
    valid_npm_name,
    valid_pypi_name,
)
from pigtail.connectors.hn import normalize_github_repo

RULE_VERSION = "downloads-v1"
MAPPING_VERSION = "mapping-v1"
VIEW = "follow_through"  # view A (ADR-083.1)
Ecosystem = Literal["npm", "pypi"]
ECOSYSTEMS: tuple[Ecosystem, ...] = ("npm", "pypi")
WINDOWS: dict[str, tuple[int, int]] = {"launch@0-2": (0, 3), "follow@3-30": (3, 30)}
METRICS: dict[Ecosystem, tuple[str, str]] = {
    "npm": ("adopt.npm_downloads_launch@0-2", "adopt.npm_downloads_follow@3-30"),
    "pypi": ("adopt.pypi_downloads_launch@0-2", "adopt.pypi_downloads_follow@3-30"),
}
SECONDARY_METRICS: tuple[str, ...] = (*METRICS["npm"], *METRICS["pypi"])
SOURCES: dict[Ecosystem, tuple[str, str]] = {
    "npm": ("npm_downloads", "TM-08"),
    "pypi": ("pypistats", "TM-35"),
}
ATTRIBUTION: dict[Ecosystem, str | None] = {
    "npm": None,
    "pypi": "PyPI / Linehaul (PSF), CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/), "
    "via pypistats.org",
}
LABEL = (
    "exploratory secondary outcome (ADR-088.3, ADR-090): not pre-registered, not used by the sort"
)
DEFAULT_ROLES = ("winner", "matched_loser", "exemplar", "exemplar_matched_loser")
FINAL = frozenset({"observed", "not_applicable"})
MAX_WORKSPACE_PACKAGES = 10
# planning upper bounds per case for the estimate (actual counts are usually lower)
GITHUB_PER_CASE = 3  # package.json, pyproject.toml, setup.cfg
GITHUB_PER_WORKSPACE_ROOT = 1 + MAX_WORKSPACE_PACKAGES  # a directory listing, the manifests
NPM_PER_CASE = 2  # the registry manifest and one range request
PYPISTATS_PER_CASE = 1

Status = Literal["observed", "pending", "unknown", "not_applicable"]


# --- anchor and windows --------------------------------------------------------------------------
def anchor_from_detail(d: Mapping[str, Any] | None) -> Anchor | None:
    if not d or not d.get("at"):
        return None
    at = datetime.fromisoformat(str(d["at"]))
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return Anchor(
        type=d["type"],
        at=at,
        precision=d.get("precision", "hour"),
        source=str(d.get("source", "")),
        via=d.get("via"),
    )


def download_first_day(anchor: Anchor) -> date:
    """Day 0 of the download windows: the UTC day containing T for an hour-precision anchor;
    the anchor's own day for a day-precision one (a dated launch, a burst onset day)."""
    if anchor.precision == "hour":
        return anchor.at.astimezone(UTC).date()
    return first_day(anchor)


@dataclass(frozen=True)
class Coverage:
    """What the source can answer: its first and last day and the days it lost."""

    start: date
    end: date | None
    gap_days: frozenset[date] = frozenset()
    zero_fill: bool = False  # a missing day inside coverage means no downloads (pypistats)

    def to_dict(self) -> dict[str, Any]:
        return {
            "coverage_start": self.start.isoformat(),
            "coverage_end": None if self.end is None else self.end.isoformat(),
            "source_gap_days": sorted(d.isoformat() for d in self.gap_days),
            "missing_day_means_zero": self.zero_fill,
        }


def window_value(
    series: Mapping[date, int] | None,
    first: date,
    lo: int,
    hi: int,
    as_of: date,
    cov: Coverage,
) -> dict[str, Any]:
    """Downloads on the UTC days `first+lo .. first+hi-1` (the §1.1 horizon rule with k = hi)."""
    days = [first + timedelta(days=i) for i in range(lo, hi)]
    win = {
        "first_day": first.isoformat(),
        "start": days[0].isoformat(),
        "end": days[-1].isoformat(),
    }
    base: dict[str, Any] = {"window": win, "coverage": cov.to_dict()}
    if as_of < first + timedelta(days=hi + SETTLE_LAG_DAYS):
        return {
            **base,
            "status": "pending",
            "value": None,
            "tag": "unknown",
            "reason": "horizon_not_reached",
        }
    if days[0] < cov.start:
        return {
            **base,
            "status": "unknown",
            "value": None,
            "tag": "unknown",
            "reason": "outside_source_history",
        }
    if any(d in cov.gap_days for d in days):
        return {
            **base,
            "status": "unknown",
            "value": None,
            "tag": "unknown",
            "reason": "source_gap_day",
        }
    if series is None or cov.end is None or days[-1] > cov.end:
        return {
            **base,
            "status": "unknown",
            "value": None,
            "tag": "unknown",
            "reason": "incomplete_series",
        }
    missing = [d for d in days if d not in series]
    if missing and not cov.zero_fill:
        return {
            **base,
            "status": "unknown",
            "value": None,
            "tag": "unknown",
            "reason": "incomplete_series",
        }
    total = sum(series.get(d, 0) for d in days)
    out = {**base, "status": "observed", "value": float(total), "tag": "verified", "reason": None}
    out["coverage"]["zero_filled_days"] = len(missing)
    return out


# --- manifests (pure) ----------------------------------------------------------------------------
def github_repo_of(url: str | None) -> str | None:
    """`owner/name` for the repository forms npm and Python manifests use: URLs (`git+https`,
    `git://`, `ssh://git@`), scp-style `git@github.com:o/n.git`, `github:o/n` and bare `o/n`."""
    if not url or not isinstance(url, str):
        return None
    u = url.strip()
    if u.startswith("github:"):
        u = "github.com/" + u.removeprefix("github:")
    elif u.startswith("git@github.com:"):
        u = "github.com/" + u.removeprefix("git@github.com:")
    elif "://" not in u and u.count("/") == 1 and ":" not in u and "." not in u.split("/")[0]:
        u = "github.com/" + u  # npm shorthand `owner/name`
    for pre in (
        "git+https://",
        "git+ssh://",
        "git+http://",
        "git://",
        "ssh://",
        "https://",
        "http://",
    ):
        if u.startswith(pre):
            u = u.removeprefix(pre)
            break
    u = u.removeprefix("git@")
    return normalize_github_repo(u)


def points_to(url: str | None, full_name: str) -> bool:
    got = github_repo_of(url)
    return got is not None and got == full_name.lower()


@dataclass(frozen=True)
class PackageJson:
    name: str | None
    private: bool
    workspaces: tuple[str, ...]


def parse_package_json(data: bytes) -> PackageJson:
    try:
        doc = json.loads(data)
    except ValueError as e:
        raise DownloadsParseError(str(e)) from e
    if not isinstance(doc, dict):
        raise DownloadsParseError("package.json is not an object")
    name = doc.get("name") if isinstance(doc.get("name"), str) else None
    ws = doc.get("workspaces")
    if isinstance(ws, dict):
        ws = ws.get("packages")
    pats = tuple(p for p in ws if isinstance(p, str)) if isinstance(ws, list) else ()
    return PackageJson(name, doc.get("private") is True, pats)


def workspace_paths(patterns: Iterable[str]) -> tuple[list[str], list[str], list[str]]:
    """(plain package dirs, parent dirs of `dir/*` patterns, unsupported patterns)."""
    plain: list[str] = []
    parents: list[str] = []
    other: list[str] = []
    for raw in patterns:
        p = raw.strip().strip("/").removeprefix("./")
        if not p or p.startswith("!"):
            continue
        if p.endswith("/*") and not any(c in p[:-2] for c in "*?[{"):
            parents.append(p[:-2])
        elif not any(c in p for c in "*?[{"):
            plain.append(p)
        else:
            other.append(raw)
    return sorted(set(plain)), sorted(set(parents)), sorted(set(other))


@dataclass(frozen=True)
class PythonManifest:
    name: str | None
    urls: tuple[str, ...]
    dynamic_name: bool = False


def parse_pyproject(data: bytes) -> PythonManifest:
    try:
        doc = tomllib.loads(data.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise DownloadsParseError(str(e)) from e
    proj_raw = doc.get("project")
    proj: dict[str, Any] = proj_raw if isinstance(proj_raw, dict) else {}
    tool = doc.get("tool")
    poetry_raw = tool.get("poetry") if isinstance(tool, dict) else None
    poetry: dict[str, Any] = poetry_raw if isinstance(poetry_raw, dict) else {}
    urls: list[str] = []
    for tbl in (proj.get("urls"), poetry.get("urls")):
        if isinstance(tbl, dict):
            urls += [v for v in tbl.values() if isinstance(v, str)]
    urls += [
        poetry[k]
        for k in ("repository", "homepage", "documentation")
        if isinstance(poetry.get(k), str)
    ]
    name = proj.get("name") if isinstance(proj.get("name"), str) else None
    if name is None and isinstance(poetry.get("name"), str):
        name = poetry["name"]
    dyn = isinstance(proj.get("dynamic"), list) and "name" in proj["dynamic"]
    return PythonManifest(name, tuple(urls), dyn)


def parse_setup_cfg(data: bytes) -> PythonManifest:
    cp = configparser.ConfigParser(interpolation=None)
    try:
        cp.read_string(data.decode("utf-8"))
    except (configparser.Error, UnicodeDecodeError) as e:
        raise DownloadsParseError(str(e)) from e
    if not cp.has_section("metadata"):
        return PythonManifest(None, ())
    md = cp["metadata"]
    urls = [md[k].strip() for k in ("url", "home_page", "download_url") if md.get(k)]
    for line in (md.get("project_urls") or "").splitlines():
        if "=" in line:
            urls.append(line.split("=", 1)[1].strip())
    name = (md.get("name") or "").strip() or None
    if name is not None and name.startswith("attr:"):
        return PythonManifest(None, tuple(urls), True)
    return PythonManifest(name, tuple(urls))


# --- fetching ------------------------------------------------------------------------------------
@dataclass
class EvidenceRef:
    evidence_id: str
    content_hash: str
    kind: str  # manifest | registry_manifest | directory | downloads | reference_downloads
    raw: Literal["kept", "dropped"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "content_hash": self.content_hash,
            "kind": self.kind,
            "raw": self.raw,
        }


DropRaw = Callable[[Fetched], None]


class RepoFiles:
    """Repo files through the GitHub connector (core bucket, budgeted, rate-limited)."""

    def __init__(self, github: Any, drop_raw: DropRaw) -> None:
        self.github = github
        self.drop_raw = drop_raw

    def _url(self, full_name: str, path: str) -> str:
        from pigtail.connectors.github import API

        return f"{API}/repos/{full_name}/contents/{path}"

    def manifest(
        self, full_name: str, path: str, repo_id: str | None, ev: list[EvidenceRef]
    ) -> bytes | None:
        """A manifest's raw text or None when absent; its raw bytes are dropped once read
        (authors and emails live in manifests), the evidence record stays."""
        try:
            f = self.github.fetch(
                self._url(full_name, path),
                headers={"Accept": "application/vnd.github.raw+json"},
                repo_id=repo_id,
                retention_class="person_level_24m",
            )
        except NotFound:
            return None
        data: bytes = f.data
        self.drop_raw(f)
        ev.append(EvidenceRef(f.evidence.id, f.content_hash, "manifest", "dropped"))
        return data

    def subdirs(
        self, full_name: str, path: str, repo_id: str | None, ev: list[EvidenceRef]
    ) -> list[str] | None:
        try:
            f = self.github.fetch(
                self._url(full_name, path), repo_id=repo_id, retention_class="project_level"
            )
        except NotFound:
            return None
        ev.append(EvidenceRef(f.evidence.id, f.content_hash, "directory", "kept"))
        try:
            items = json.loads(f.data)
        except ValueError:
            return None
        if not isinstance(items, list):
            return None
        return sorted(
            str(i["path"]) for i in items if isinstance(i, dict) and i.get("type") == "dir"
        )


@dataclass
class Mapping_:
    """One ecosystem's mapping of one repo."""

    ecosystem: Ecosystem
    status: Literal["mapped", "not_applicable", "unknown"]
    reason: str | None = None
    package: str | None = None
    packages: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    evidence: list[EvidenceRef] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": MAPPING_VERSION,
            "status": self.status,
            "reason": self.reason,
            "package": self.package,
            "packages": self.packages,
            "notes": self.notes,
            "basis": "current default branch",
        }


def map_npm(
    files: RepoFiles,
    npm: NpmDownloadsConnector,
    full_name: str,
    repo_id: str | None,
    drop_raw: DropRaw,
) -> Mapping_:
    m = Mapping_("npm", "unknown")
    raw = files.manifest(full_name, "package.json", repo_id, m.evidence)
    if raw is None:
        m.status, m.reason = "not_applicable", "no_manifest"
        return m
    try:
        pj = parse_package_json(raw)
    except DownloadsParseError:
        m.reason = "manifest_unparseable"
        return m
    cands: list[tuple[str, str]] = []  # (name, role)
    if pj.name and not pj.private:
        cands.append((pj.name, "root"))
    plain, parents, other = workspace_paths(pj.workspaces)
    if other:
        m.notes.append(f"workspace patterns not followed: {len(other)}")
    dirs = list(plain)
    for parent in parents:
        sub = files.subdirs(full_name, parent, repo_id, m.evidence)
        dirs += sub or []
    truncated = len(dirs) > MAX_WORKSPACE_PACKAGES
    if truncated:
        m.notes.append(f"workspace packages capped at {MAX_WORKSPACE_PACKAGES} of {len(dirs)}")
    for d in sorted(set(dirs))[:MAX_WORKSPACE_PACKAGES]:
        wraw = files.manifest(full_name, f"{d}/package.json", repo_id, m.evidence)
        if wraw is None:
            continue
        try:
            wp = parse_package_json(wraw)
        except DownloadsParseError:
            m.notes.append("a workspace manifest was unparseable")
            continue
        if wp.name and not wp.private and wp.name not in {c[0] for c in cands}:
            cands.append((wp.name, "workspace"))
    if not cands:
        m.status = "not_applicable"
        m.reason = "no_publishable_package"
        if truncated or other:
            m.status, m.reason = "unknown", "workspaces_not_fully_read"
        return m
    for name, role in cands:
        rec: dict[str, Any] = {"name": name, "role": role}
        if not valid_npm_name(name):
            rec["check"] = "invalid_name"
            m.packages.append(rec)
            continue
        f = npm.latest(name, repo_id=repo_id)
        if f is None:
            rec["check"] = "not_published"
        else:
            try:
                meta = parse_npm_latest(f.data)
            except DownloadsParseError:
                meta = None
            drop_raw(f)
            m.evidence.append(
                EvidenceRef(f.evidence.id, f.content_hash, "registry_manifest", "dropped")
            )
            if meta is None:
                rec["check"] = "registry_manifest_unparseable"
            elif not meta["repository_url"]:
                rec["check"] = "no_repository_field"
            elif points_to(meta["repository_url"], full_name):
                rec["check"] = "confirmed"
            else:
                rec["check"] = "repository_mismatch"
        m.packages.append(rec)
    confirmed = [p for p in m.packages if p["check"] == "confirmed"]
    root = [p for p in confirmed if p["role"] == "root"]
    if root:
        m.status, m.package = "mapped", root[0]["name"]
    elif len(confirmed) == 1 and not truncated:
        m.status, m.package = "mapped", confirmed[0]["name"]
    elif len(confirmed) > 1 or (confirmed and truncated):
        m.status, m.reason = "unknown", "ambiguous_multiple_packages"
    else:
        checks = {p["check"] for p in m.packages}
        if checks == {"not_published"} or checks <= {"not_published", "invalid_name"}:
            m.status, m.reason = "not_applicable", "not_published"
        elif "repository_mismatch" in checks:
            m.status, m.reason = "unknown", "repository_mismatch"
        elif "no_repository_field" in checks:
            m.status, m.reason = "unknown", "no_repository_field"
        else:
            m.status, m.reason = "unknown", "mapping_not_confirmed"
    return m


def map_pypi(
    files: RepoFiles, full_name: str, repo_id: str | None, language: str | None
) -> Mapping_:
    m = Mapping_("pypi", "unknown")
    manifest: PythonManifest | None = None
    for path, parse in (("pyproject.toml", parse_pyproject), ("setup.cfg", parse_setup_cfg)):
        raw = files.manifest(full_name, path, repo_id, m.evidence)
        if raw is None:
            continue
        try:
            got = parse(raw)
        except DownloadsParseError:
            m.notes.append(f"{path} unparseable")
            continue
        if got.name or got.dynamic_name:
            manifest = got
            m.notes.append(f"name from {path}")
            break
    if manifest is None:
        if (language or "").lower() == "python":
            # setup.py-only projects exist; pigtail neither runs nor parses setup.py
            m.status, m.reason = "unknown", "no_static_python_manifest"
        else:
            m.status, m.reason = "not_applicable", "no_manifest"
        return m
    if manifest.name is None:
        m.reason = "dynamic_name"
        return m
    name = pep503(manifest.name)
    rec: dict[str, Any] = {"name": name, "role": "root"}
    if not valid_pypi_name(name):
        rec["check"] = "invalid_name"
    elif any(points_to(u, full_name) for u in manifest.urls):
        rec["check"] = "confirmed_by_manifest_urls"
    else:
        rec["check"] = "no_url_to_repo"
    m.packages.append(rec)
    if rec["check"] == "confirmed_by_manifest_urls":
        m.status, m.package = "mapped", name
    else:
        m.reason = rec["check"]
    return m


# --- stored values -------------------------------------------------------------------------------
DDL_TABLE = "brief_secondary_outcome"


def secondary_outcomes(
    conn: psycopg.Connection[Any], selection_id: str, metrics: Sequence[str] | None = None
) -> dict[str, dict[str, dict[str, Any]]]:
    """Read API for the report step: `{candidate_ref: {metric: record}}` of a selection's
    exploratory secondary outcomes (`SECONDARY_METRICS` by default). A record has `value`,
    `status`, `tag`, `reason`, `group` (the ecosystem) like a selection value (`Value.to_dict`),
    plus `exploratory`, `used_by_sort`, `label`, `window`, `coverage`, `mapping`, `evidence`
    (ids and content hashes), `source`, `terms_basis`, `attribution`, `rule` and `as_of`."""
    ms = list(metrics or SECONDARY_METRICS)
    rows = conn.execute(
        "SELECT candidate_ref, metric, record FROM brief_secondary_outcome"
        " WHERE selection_id = %s AND metric = ANY(%s) ORDER BY candidate_ref, metric",
        (selection_id, ms),
    ).fetchall()
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for ref, metric, rec in rows:
        out.setdefault(str(ref), {})[str(metric)] = dict(rec)
    return out


def _existing(conn: psycopg.Connection[Any], selection_id: str) -> dict[tuple[str, str], str]:
    rows = conn.execute(
        "SELECT candidate_ref, metric, status FROM brief_secondary_outcome WHERE selection_id = %s",
        (selection_id,),
    ).fetchall()
    return {(str(r[0]), str(r[1])): str(r[2]) for r in rows}


@dataclass(frozen=True)
class Case:
    candidate_ref: str
    repo_full_name: str
    repo_host_id: int | None
    repo_id: str | None
    role: str
    pair_id: int | None
    anchor: Anchor | None
    language: str | None
    # the `repos` id evidence may link to (evidence.repo_id references repos); None when the
    # repo has no `repos` row: the value's evidence list still links the items to the case
    evidence_repo_id: str | None = None


def load_cases(
    conn: psycopg.Connection[Any], selection_id: str, roles: Sequence[str] | None
) -> list[Case]:
    rows = conn.execute(
        "SELECT c.candidate_ref, c.repo_full_name, c.repo_host_id, c.repo_id, c.role, c.pair_id,"
        " c.detail, r.id FROM brief_selection_case c LEFT JOIN repos r ON r.id = c.repo_id"
        " WHERE c.selection_id = %s AND c.view = %s"
        " AND (%s::text[] IS NULL OR c.role = ANY(%s))"
        " ORDER BY c.pair_id NULLS LAST, c.candidate_ref",
        (selection_id, VIEW, list(roles) if roles else None, list(roles) if roles else None),
    ).fetchall()
    out = []
    for ref, name, hid, rid, role, pid, detail, ev_rid in rows:
        d = dict(detail or {})
        out.append(
            Case(
                str(ref),
                str(name),
                hid,
                rid,
                str(role),
                pid,
                anchor_from_detail(d.get("anchor")),
                (d.get("covariates") or {}).get("language"),
                ev_rid,
            )
        )
    return out


def resolve_selection(
    conn: psycopg.Connection[Any], brief_id: str, version: int | None, selection_id: str | None
) -> dict[str, Any] | None:
    if selection_id:
        row = conn.execute(
            "SELECT id, brief_id, brief_version, selection_version, as_of FROM brief_selection"
            " WHERE id = %s AND brief_id = %s",
            (selection_id, brief_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT id, brief_id, brief_version, selection_version, as_of FROM brief_selection"
            " WHERE brief_id = %s AND (%s::int IS NULL OR brief_version = %s)"
            " ORDER BY brief_version DESC, created_at DESC, id DESC LIMIT 1",
            (brief_id, version, version),
        ).fetchone()
    if row is None:
        return None
    return dict(
        zip(("id", "brief_id", "brief_version", "selection_version", "as_of"), row, strict=True)
    )


# --- plan (estimate before any request) ----------------------------------------------------------
def plan(
    conn: psycopg.Connection[Any], selection_id: str, roles: Sequence[str] | None
) -> dict[str, Any]:
    """Counts and request upper bounds; no network. Cases whose two metrics of an ecosystem are
    final (observed or not_applicable) are not fetched again for that ecosystem."""
    cases = load_cases(conn, selection_id, roles)
    have = _existing(conn, selection_id)
    todo = {e: 0 for e in ECOSYSTEMS}
    anchored = [c for c in cases if c.anchor is not None]
    for c in anchored:
        for e in ECOSYSTEMS:
            if not all(have.get((c.candidate_ref, m)) in FINAL for m in METRICS[e]):
                todo[e] += 1
    repos = max(todo.values()) if anchored else 0
    gh = repos * GITHUB_PER_CASE
    npm = todo["npm"] * NPM_PER_CASE + (1 if todo["npm"] else 0)
    pps = todo["pypi"] * PYPISTATS_PER_CASE + (1 if todo["pypi"] else 0)
    return {
        "selection_id": selection_id,
        "view": VIEW,
        "roles": list(roles) if roles else "all",
        "cases": len(cases),
        "cases_without_anchor": len(cases) - len(anchored),
        "cases_to_fetch": dict(todo),
        "requests_upper_bound": {
            "github_core": gh,
            "github_core_per_npm_workspace_root": GITHUB_PER_WORKSPACE_ROOT,
            "npm": npm,
            "pypistats": pps,
        },
        "pypistats_minutes_at_most": round(pps * PYPISTATS_SECONDS_PER_REQUEST / 60, 1),
        "cost_usd": 0.0,
        "llm_calls": 0,
        "note": "free official APIs (TM-08, TM-35, TM-02); upper bounds: repos without a "
        "manifest cost fewer requests",
    }


# --- fill ----------------------------------------------------------------------------------------
@dataclass
class FillDeps:
    conn: psycopg.Connection[Any]
    files: RepoFiles
    npm: NpmDownloadsConnector | None
    pypi: PypiStatsConnector | None
    drop_raw: DropRaw
    reuse: Callable[[str, str], Fetched | None]  # (source, url) -> a same-day snapshot
    run_id: str | None = None


@dataclass
class _Refs:
    npm: tuple[dict[date, int], Coverage, list[EvidenceRef]] | None = None
    pypi: tuple[dict[date, int], Coverage, list[EvidenceRef]] | None = None


def _npm_reference(
    deps: FillDeps, lo: date, hi: date
) -> tuple[dict[date, int], Coverage, list[EvidenceRef]]:
    """The reference package over `[lo, hi]`: a day on which it has 0 (or no row) is a source
    gap for every package (registry-wide outage days are reported as 0)."""
    assert deps.npm is not None
    days: dict[date, int] = {}
    ev: list[EvidenceRef] = []
    lo = max(lo, NPM_HISTORY_START)
    for a, b in chunk_ranges(lo, hi):
        url = deps.npm.range_url(NPM_REFERENCE_PACKAGE, a, b)
        f = _fetch_or_reuse(
            deps,
            "npm_downloads",
            url,
            partial(deps.npm.downloads_range, NPM_REFERENCE_PACKAGE, a, b),
        )
        if f is None:
            continue
        ev.append(EvidenceRef(f.evidence.id, f.content_hash, "reference_downloads", "kept"))
        days.update(parse_npm_range(f.data).days)
    end = max(days) if days else None
    gaps = frozenset(d for d in _span(lo, end) if days.get(d, 0) == 0) if end else frozenset()
    return days, Coverage(NPM_HISTORY_START, end, gaps), ev


def _pypi_reference(deps: FillDeps) -> tuple[dict[date, int], Coverage, list[EvidenceRef]] | None:
    """The source's coverage from a package downloaded every day: its first and last retained
    day; a day missing inside that range is a gap for every package."""
    assert deps.pypi is not None
    url = deps.pypi.overall_url(PYPISTATS_REFERENCE_PACKAGE)
    f = _fetch_or_reuse(
        deps, "pypistats", url, partial(deps.pypi.overall, PYPISTATS_REFERENCE_PACKAGE)
    )
    if f is None:
        return None
    s = parse_pypistats_overall(f.data)
    if not s.days:
        return None
    lo, hi = min(s.days), max(s.days)
    gaps = frozenset(d for d in _span(lo, hi) if d not in s.days)
    ev = [EvidenceRef(f.evidence.id, f.content_hash, "reference_downloads", "kept")]
    return s.days, Coverage(lo, hi, gaps, zero_fill=True), ev


def _span(lo: date, hi: date | None) -> list[date]:
    if hi is None or hi < lo:
        return []
    return [lo + timedelta(days=i) for i in range((hi - lo).days + 1)]


def _fetch_or_reuse(
    deps: FillDeps, source: str, url: str, fetch: Callable[[], Fetched | None]
) -> Fetched | None:
    """At most one request per endpoint and day (pypistats etiquette; harmless for npm)."""
    got = deps.reuse(source, url)
    return got if got is not None else fetch()


def _records(
    case: Case,
    eco: Ecosystem,
    mapping: Mapping_ | None,
    series: DailySeries | None,
    cov: Coverage | None,
    extra_ev: list[EvidenceRef],
    as_of: date,
    fixed_reason: tuple[Status, str] | None = None,
) -> dict[str, dict[str, Any]]:
    source, tm = SOURCES[eco]
    first = download_first_day(case.anchor) if case.anchor is not None else None
    out: dict[str, dict[str, Any]] = {}
    for metric, (lo, hi) in zip(METRICS[eco], WINDOWS.values(), strict=True):
        if fixed_reason is not None or first is None or cov is None:
            st, why = fixed_reason or ("unknown", "no_anchor" if first is None else "no_source")
            rec: dict[str, Any] = {"status": st, "value": None, "tag": "unknown", "reason": why}
            if first is not None:
                days = [first + timedelta(days=i) for i in (lo, hi - 1)]
                rec["window"] = {
                    "first_day": first.isoformat(),
                    "start": days[0].isoformat(),
                    "end": days[1].isoformat(),
                }
        else:
            rec = window_value(series.days if series else None, first, lo, hi, as_of, cov)
        ev = [e.to_dict() for e in (mapping.evidence if mapping else [])] + [
            e.to_dict() for e in extra_ev
        ]
        rec.update(
            {
                "metric": metric,
                "group": eco,
                "package": mapping.package if mapping else None,
                "mapping": mapping.to_dict() if mapping else None,
                "evidence": ev,
                "source": source,
                "terms_basis": tm,
                "attribution": ATTRIBUTION[eco],
                "day_boundary": "UTC",
                "anchor": None if case.anchor is None else case.anchor.to_dict(),
                "exploratory": True,
                "preregistered": False,
                "used_by_sort": False,
                "label": LABEL,
                "rule": RULE_VERSION,
                "as_of": as_of.isoformat(),
                "unit": "downloads",
            }
        )
        out[metric] = rec
    return out


def _write(
    conn: psycopg.Connection[Any],
    selection_id: str,
    case: Case,
    recs: Mapping[str, Mapping[str, Any]],
    have: Mapping[tuple[str, str], str],
    run_id: str | None,
    now: datetime,
) -> int:
    n = 0
    with conn.transaction():
        for metric, rec in recs.items():
            if have.get((case.candidate_ref, metric)) == "observed":
                continue  # §1.1: an observed value is not overwritten by a later fetch
            conn.execute(
                "INSERT INTO brief_secondary_outcome (selection_id, candidate_ref, repo_full_name,"
                " repo_host_id, repo_id, metric, status, value, record, rule_version, as_of,"
                " run_id, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (selection_id, candidate_ref, metric) DO UPDATE SET"
                " status = EXCLUDED.status, value = EXCLUDED.value, record = EXCLUDED.record,"
                " rule_version = EXCLUDED.rule_version, as_of = EXCLUDED.as_of,"
                " run_id = EXCLUDED.run_id, updated_at = EXCLUDED.updated_at"
                " WHERE brief_secondary_outcome.status <> 'observed'",
                (
                    selection_id,
                    case.candidate_ref,
                    case.repo_full_name,
                    case.repo_host_id,
                    case.repo_id,
                    metric,
                    rec["status"],
                    rec["value"],
                    Jsonb(dict(rec)),
                    RULE_VERSION,
                    date.fromisoformat(str(rec["as_of"])),
                    run_id,
                    now,
                ),
            )
            n += 1
    return n


@dataclass
class FillResult:
    cases: int = 0
    written: int = 0
    skipped_final: int = 0
    by_metric_status: dict[str, dict[str, int]] = field(default_factory=dict)
    mapping: dict[str, dict[str, int]] = field(default_factory=dict)

    def count(self, metric: str, status: str) -> None:
        d = self.by_metric_status.setdefault(metric, {})
        d[status] = d.get(status, 0) + 1

    def count_mapping(self, eco: str, key: str) -> None:
        d = self.mapping.setdefault(eco, {})
        d[key] = d.get(key, 0) + 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "cases": self.cases,
            "rows_written": self.written,
            "ecosystems_skipped_final": self.skipped_final,
            "by_metric_status": self.by_metric_status,
            "mapping": self.mapping,
        }


def fill(
    deps: FillDeps,
    selection_id: str,
    *,
    roles: Sequence[str] | None,
    as_of: date,
    now: datetime,
) -> FillResult:
    """Fill the secondary download outcomes of a stored selection's view-A cases. Idempotent and
    resumable: each case is written in its own transaction; an ecosystem whose two metrics are
    final for a case is skipped; an observed value is never overwritten."""
    conn = deps.conn
    cases = load_cases(conn, selection_id, roles)
    have = _existing(conn, selection_id)
    res = FillResult(cases=len(cases))
    refs = _Refs()
    firsts = [download_first_day(c.anchor) for c in cases if c.anchor is not None]
    ref_hi = min(max(firsts) + timedelta(days=29), as_of - timedelta(days=1)) if firsts else None
    npm_ref_loaded = pypi_ref_loaded = False
    for case in cases:
        recs: dict[str, dict[str, Any]] = {}
        todo = [
            e
            for e in ECOSYSTEMS
            if not all(have.get((case.candidate_ref, m)) in FINAL for m in METRICS[e])
        ]
        res.skipped_final += len(ECOSYSTEMS) - len(todo)
        if case.anchor is None:
            for e in todo:
                recs.update(_records(case, e, None, None, None, [], as_of))
        for eco in todo if case.anchor is not None else []:
            assert case.anchor is not None
            first = download_first_day(case.anchor)
            if as_of < first + timedelta(days=3 + SETTLE_LAG_DAYS):
                # both windows pending: no request at all
                recs.update(
                    _records(
                        case, eco, None, None, None, [], as_of, ("pending", "horizon_not_reached")
                    )
                )
                res.count_mapping(eco, "not_fetched_pending")
                continue
            if eco == "npm":
                if deps.npm is None:
                    recs.update(
                        _records(
                            case, eco, None, None, None, [], as_of, ("unknown", "connector_off")
                        )
                    )
                    continue
                m = map_npm(
                    deps.files, deps.npm, case.repo_full_name, case.evidence_repo_id, deps.drop_raw
                )
            else:
                if deps.pypi is None:
                    recs.update(
                        _records(
                            case, eco, None, None, None, [], as_of, ("unknown", "connector_off")
                        )
                    )
                    continue
                m = map_pypi(deps.files, case.repo_full_name, case.evidence_repo_id, case.language)
            res.count_mapping(eco, m.status if m.status == "mapped" else f"{m.status}:{m.reason}")
            if m.status != "mapped" or m.package is None:
                st: Status = "not_applicable" if m.status == "not_applicable" else "unknown"
                recs.update(_records(case, eco, m, None, None, [], as_of, (st, m.reason or "")))
                continue
            ev: list[EvidenceRef] = []
            if eco == "npm":
                assert deps.npm is not None
                if not npm_ref_loaded and ref_hi is not None:
                    refs.npm = _npm_reference(deps, min(firsts), ref_hi)
                    npm_ref_loaded = True
                assert refs.npm is not None
                _rdays, cov, rev = refs.npm
                hi = min(first + timedelta(days=29), as_of - timedelta(days=1))
                series: DailySeries | None = None
                if first >= NPM_HISTORY_START:
                    url = deps.npm.range_url(m.package, first, hi)
                    get = partial(
                        deps.npm.downloads_range,
                        m.package,
                        first,
                        hi,
                        repo_id=case.evidence_repo_id,
                    )
                    f = _fetch_or_reuse(deps, "npm_downloads", url, get)
                    if f is not None:
                        ev.append(EvidenceRef(f.evidence.id, f.content_hash, "downloads", "kept"))
                        series = parse_npm_range(f.data)
                recs.update(_records(case, eco, m, series, cov, ev + rev, as_of))
            else:
                assert deps.pypi is not None
                if not pypi_ref_loaded:
                    refs.pypi = _pypi_reference(deps)
                    pypi_ref_loaded = True
                if refs.pypi is None:
                    recs.update(
                        _records(
                            case,
                            eco,
                            m,
                            None,
                            None,
                            [],
                            as_of,
                            ("unknown", "source_coverage_unknown"),
                        )
                    )
                    continue
                _rdays, cov, rev = refs.pypi
                url = deps.pypi.overall_url(m.package)
                get = partial(deps.pypi.overall, m.package, repo_id=case.evidence_repo_id)
                f = _fetch_or_reuse(deps, "pypistats", url, get)
                if f is None:
                    recs.update(
                        _records(case, eco, m, None, cov, rev, as_of, ("unknown", "not_in_source"))
                    )
                    continue
                ev.append(EvidenceRef(f.evidence.id, f.content_hash, "downloads", "kept"))
                recs.update(
                    _records(case, eco, m, parse_pypistats_overall(f.data), cov, ev + rev, as_of)
                )
        for metric, rec in recs.items():
            res.count(metric, str(rec["status"]))
        res.written += _write(conn, selection_id, case, recs, have, deps.run_id, now)
    return res


def same_day_reuse(
    conn: psycopg.Connection[Any], store: Any, today: date
) -> Callable[[str, str], Fetched | None]:
    """A same-day snapshot of `url` from `source` whose raw bytes are still present, as a
    `Fetched`; None when there is none (then the caller makes the request)."""
    from pigtail.capture.models import Evidence
    from pigtail.capture.snapshots import SnapshotMeta

    def reuse(source: str, url: str) -> Fetched | None:
        row = conn.execute(
            "SELECT id, content_hash, fetched_at, terms_basis, content_type, snapshot_ref,"
            " http_status, reliability, retention_class, collector_version, run_id"
            " FROM evidence WHERE source = %s AND url = %s AND fetched_at >= %s"
            " AND deletion_state = 'present' ORDER BY fetched_at DESC LIMIT 1",
            (source, url, datetime.combine(today, datetime.min.time(), UTC)),
        ).fetchone()
        if row is None:
            return None
        try:
            data = store.get(row[1])
        except Exception:  # a missing blob just means: fetch again
            return None
        meta = SnapshotMeta(
            source=source,
            url=url,
            fetched_at=row[2],
            collector_version=row[9],
            terms_basis=row[3],
            content_type=row[4],
        )
        ev = Evidence(
            id=row[0],
            source=source,
            url=url,
            fetched_at=row[2],
            content_hash=row[1],
            snapshot_ref=row[5],
            content_type=row[4],
            http_status=row[6],
            reliability=row[7],
            terms_basis=row[3],
            retention_class=row[8],
            deletion_state="present",
            collector_version=row[9],
            case_id=None,
            repo_id=None,
            run_id=row[10],
        )
        return Fetched(data=data, content_hash=row[1], evidence=ev, meta=meta)

    return reuse
