"""Candidates of a brief version (PRD R4.5, R4.6, R4.11; migration 0021).

A candidate is a GitHub repo found by discovery (or added by the user), keyed
`gh:<owner/name>` in lowercase, or a named reference case / distribution exemplar of the brief
that couldn't be resolved to a repo yet, keyed `named:<reference|exemplar>:<i>` by its position
in the brief (never by its name: brief content stays in the brief file, R18.9).

Everything stored is project-level: repo ids and names, public repo metadata (the description is
scrubbed of identifiers with the keyless placeholders of `scrub_identifiers` and cut to 300
characters), the discovery signals of each source, and the relevance verdict with its
provenance. No handles: search and GraphQL results keep only the owner *type* (CB-24).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

import psycopg
from psycopg.types.json import Jsonb

from pigtail.pseudonymize import scrub_identifiers

Panel = Literal["field", "exemplar", "reference"]
Verdict = Literal["relevant", "not_relevant", "uncertain"]
Resolution = Literal["resolved", "unresolved", "confirmed"]
PANELS: tuple[Panel, ...] = ("field", "exemplar", "reference")
VERDICTS: tuple[Verdict, ...] = ("relevant", "not_relevant", "uncertain")

_REPO = re.compile(r"^[a-z0-9][a-z0-9-]{0,38}/[a-z0-9._-]{1,100}$")
_NAMED = re.compile(r"^named:(reference|exemplar):([0-9]{1,2})$")
DESCRIPTION_MAX = 300


class BadCandidate(ValueError):
    pass


def gh_ref(full_name: str) -> str:
    name = full_name.strip().lower()
    if not _REPO.match(name):
        raise BadCandidate(f"not a GitHub owner/name: {full_name!r}")
    return f"gh:{name}"


def named_ref(panel: Literal["reference", "exemplar"], index: int) -> str:
    return f"named:{panel}:{index}"


def parse_named(ref: str) -> tuple[Literal["reference", "exemplar"], int] | None:
    m = _NAMED.match(ref)
    if m is None:
        return None
    panel: Literal["reference", "exemplar"] = (
        "reference" if m.group(1) == "reference" else "exemplar"
    )
    return panel, int(m.group(2))


def normalize_ref(value: str) -> str:
    """`gh:owner/name`, `owner/name`, a github.com URL or `named:<panel>:<i>` -> candidate ref."""
    from pigtail.connectors.hn import normalize_github_repo

    v = value.strip()
    if _NAMED.match(v):
        return v
    if v.lower().startswith("gh:"):
        return gh_ref(v[3:])
    if "github.com" in v.lower():
        repo = normalize_github_repo(v)
        if repo is None:
            raise BadCandidate(f"not a GitHub repository URL: {value!r}")
        return gh_ref(repo)
    return gh_ref(v)


def strip_owner(text: str | None, owner: str) -> str | None:
    """Replace the repo owner's login with `[owner]` (case-insensitive, whole token): an owner
    may be a person, and its login is a handle (Directive §8.1)."""
    if text is None or not owner:
        return text
    # not before `@`: an e-mail's local part is left to the e-mail redaction (`[email]`)
    rx = re.compile(r"(?<![A-Za-z0-9-])" + re.escape(owner) + r"(?![A-Za-z0-9@-])", re.I)
    return rx.sub("[owner]", text)


def clean_description(text: str | None, owner: str = "") -> str | None:
    """Public repo description as stored: the owner login and other identifiers scrubbed (no
    handles), one line, cut to 300 characters."""
    if not text:
        return None
    one = " ".join(scrub_identifiers(strip_owner(text, owner) or "").split())
    if not one:
        return None
    return one if len(one) <= DESCRIPTION_MAX else one[: DESCRIPTION_MAX - 1].rstrip() + "…"


def signal_key(sig: Mapping[str, Any]) -> tuple[str, str]:
    """De-duplication key of a discovery signal: its source and what found it."""
    what = sig.get("term") or sig.get("list") or sig.get("hn_item_id") or sig.get("rule") or ""
    return str(sig.get("source", "")), str(what)


def merge_signals(old: Iterable[Mapping[str, Any]], new: Iterable[Mapping[str, Any]]) -> list[Any]:
    out: dict[tuple[str, str], Any] = {}
    for s in [*old, *new]:
        out.setdefault(signal_key(s), dict(s))
    return list(out.values())


@dataclass
class Candidate:
    ref: str
    repo_full_name: str | None
    panel: Panel = "field"
    repo_host_id: int | None = None
    repo_id: str | None = None
    named_index: int | None = None
    resolution: Resolution = "resolved"
    resolution_rule: str | None = None
    matches: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    verdict: Verdict | None = None
    reason: str | None = None
    distance: int | None = None
    model_panel: Panel | None = None
    rubric_version: str | None = None
    relevance: dict[str, Any] | None = None
    judged_at: datetime | None = None
    carried_from_version: int | None = None  # copied from this version's final shortlist

    @property
    def is_named(self) -> bool:
        return self.named_index is not None and self.panel in ("reference", "exemplar")

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_ref": self.ref,
            "repo_full_name": self.repo_full_name,
            "url": f"https://github.com/{self.repo_full_name}" if self.repo_full_name else None,
            "panel": self.panel,
            "named_index": self.named_index,
            "resolution": self.resolution,
            "resolution_rule": self.resolution_rule,
            "matches": self.matches,
            "sources": self.sources,
            "description": self.metadata.get("description"),
            "stars": self.metadata.get("stars"),
            "created_at": self.metadata.get("created_at"),
            "first_seen_at": self.first_seen_at,
            "verdict": self.verdict,
            "reason": self.reason,
            "distance": self.distance,
            "model_panel": self.model_panel,
            "rubric_version": self.rubric_version,
            "relevance": self.relevance,
            "judged_at": self.judged_at,
            "carried_from_version": self.carried_from_version,
        }


_COLS = (
    "candidate_ref, repo_full_name, panel, repo_host_id, repo_id, named_index, resolution,"
    " resolution_rule, matches, sources, metadata, first_seen_at, last_seen_at, verdict, reason,"
    " distance, model_panel, rubric_version, relevance, judged_at, carried_from_version"
)


def _row(r: Any) -> Candidate:
    return Candidate(
        ref=r[0],
        repo_full_name=r[1],
        panel=r[2],
        repo_host_id=r[3],
        repo_id=r[4],
        named_index=r[5],
        resolution=r[6],
        resolution_rule=r[7],
        matches=list(r[8] or []),
        sources=list(r[9] or []),
        metadata=dict(r[10] or {}),
        first_seen_at=r[11],
        last_seen_at=r[12],
        verdict=r[13],
        reason=r[14],
        distance=r[15],
        model_panel=r[16],
        rubric_version=r[17],
        relevance=r[18],
        judged_at=r[19],
        carried_from_version=r[20],
    )


class CandidateStore:
    """`brief_candidate` rows of one brief version (autocommit or caller-managed conn)."""

    def __init__(self, conn: psycopg.Connection[Any], brief_id: str, brief_version: int) -> None:
        self.conn = conn
        self.brief_id = brief_id
        self.version = brief_version

    # --- reads ---------------------------------------------------------------------------------
    def all(self) -> list[Candidate]:
        rows = self.conn.execute(
            f"SELECT {_COLS} FROM brief_candidate WHERE brief_id = %s AND brief_version = %s"
            " ORDER BY candidate_ref",
            (self.brief_id, self.version),
        ).fetchall()
        return [_row(r) for r in rows]

    def get(self, ref: str) -> Candidate | None:
        r = self.conn.execute(
            f"SELECT {_COLS} FROM brief_candidate WHERE brief_id = %s AND brief_version = %s"
            " AND candidate_ref = %s",
            (self.brief_id, self.version, ref),
        ).fetchone()
        return _row(r) if r else None

    def count(self) -> int:
        row = self.conn.execute(
            "SELECT count(*) FROM brief_candidate WHERE brief_id = %s AND brief_version = %s",
            (self.brief_id, self.version),
        ).fetchone()
        return int(row[0]) if row else 0

    # --- writes --------------------------------------------------------------------------------
    def upsert(
        self,
        c: Candidate,
        *,
        brief_run_id: str | None,
        now: datetime,
    ) -> bool:
        """Insert or merge one candidate; returns True when it is new. Signals are merged and
        de-duplicated; metadata keys are updated; `first_seen_at` never moves; a named panel
        (reference, exemplar) is never downgraded to `field` by a later source."""
        old = self.get(c.ref)
        if old is None:
            self.conn.execute(
                "INSERT INTO brief_candidate (brief_id, brief_version, candidate_ref,"
                " repo_full_name, repo_host_id, repo_id, panel, named_index, resolution,"
                " resolution_rule, matches, sources, metadata, first_seen_at, last_seen_at,"
                " first_brief_run_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
                " %s, %s, %s, %s)",
                (
                    self.brief_id,
                    self.version,
                    c.ref,
                    c.repo_full_name,
                    c.repo_host_id,
                    c.repo_id,
                    c.panel,
                    c.named_index,
                    c.resolution,
                    c.resolution_rule,
                    Jsonb(c.matches),
                    Jsonb(merge_signals([], c.sources)),
                    Jsonb(c.metadata),
                    now,
                    now,
                    brief_run_id,
                ),
            )
            return True
        panel = old.panel if old.panel != "field" else c.panel
        named_index = old.named_index if old.named_index is not None else c.named_index
        meta = {**old.metadata, **{k: v for k, v in c.metadata.items() if v is not None}}
        self.conn.execute(
            "UPDATE brief_candidate SET sources = %s, metadata = %s, panel = %s, named_index = %s,"
            " repo_host_id = COALESCE(%s, repo_host_id), repo_id = COALESCE(%s, repo_id),"
            " matches = CASE WHEN %s THEN %s ELSE matches END,"
            " resolution = CASE WHEN resolution = 'confirmed' THEN resolution ELSE %s END,"
            " resolution_rule = COALESCE(resolution_rule, %s), last_seen_at = %s"
            " WHERE brief_id = %s AND brief_version = %s AND candidate_ref = %s",
            (
                Jsonb(merge_signals(old.sources, c.sources)),
                Jsonb(meta),
                panel,
                named_index,
                c.repo_host_id,
                c.repo_id,
                bool(c.matches),
                Jsonb(c.matches),
                c.resolution,
                c.resolution_rule,
                now,
                self.brief_id,
                self.version,
                c.ref,
            ),
        )
        return False

    def add_sources(self, ref: str, sources: Iterable[Mapping[str, Any]]) -> None:
        """Merge signals into an existing candidate (de-duplicated by `signal_key`); nothing
        else on the row changes."""
        old = self.get(ref)
        if old is None:
            return
        self.conn.execute(
            "UPDATE brief_candidate SET sources = %s"
            " WHERE brief_id = %s AND brief_version = %s AND candidate_ref = %s",
            (Jsonb(merge_signals(old.sources, sources)), self.brief_id, self.version, ref),
        )

    def set_metadata(self, ref: str, updates: Mapping[str, Any]) -> None:
        self.conn.execute(
            "UPDATE brief_candidate SET metadata = metadata || %s"
            " WHERE brief_id = %s AND brief_version = %s AND candidate_ref = %s",
            (Jsonb(dict(updates)), self.brief_id, self.version, ref),
        )

    def set_verdict(
        self,
        ref: str,
        *,
        verdict: Verdict,
        reason: str,
        distance: int,
        model_panel: Panel,
        rubric_version: str,
        provenance: Mapping[str, Any],
        judged_at: datetime,
        brief_run_id: str | None,
    ) -> None:
        self.conn.execute(
            "UPDATE brief_candidate SET verdict = %s, reason = %s, distance = %s,"
            " model_panel = %s, rubric_version = %s, relevance = %s, judged_at = %s,"
            " judged_brief_run_id = %s"
            " WHERE brief_id = %s AND brief_version = %s AND candidate_ref = %s",
            (
                verdict,
                reason,
                distance,
                model_panel,
                rubric_version,
                Jsonb(dict(provenance)),
                judged_at,
                brief_run_id,
                self.brief_id,
                self.version,
                ref,
            ),
        )

    def set_resolution(
        self, ref: str, resolution: Resolution, rule: str, extra: Mapping[str, Any]
    ) -> None:
        self.conn.execute(
            "UPDATE brief_candidate SET resolution = %s, resolution_rule = %s,"
            " metadata = metadata || %s"
            " WHERE brief_id = %s AND brief_version = %s AND candidate_ref = %s",
            (resolution, rule, Jsonb(dict(extra)), self.brief_id, self.version, ref),
        )
