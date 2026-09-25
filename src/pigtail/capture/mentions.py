"""Mention capture for one repo on HN (M1-T4; R1.2).

`capture_hn_mentions()` runs HN Algolia searches for a repo and stores what it finds:

- queries (`build_queries`): the URL `github.com/owner/name` (story URLs), `owner/name` (titles
  and text), and `name owner` (both words) for stories and comments; with `loose=True` also the
  bare repo name (noisy for common words);
- every hit is classified by the strongest rule it meets (`classify_mention`): `url` (links the
  repo), `full_name` (`owner/name` appears), `name_and_owner` (both appear as words), `name` (the
  name only; kept only with `loose=True`). Other hits are not mentions and are not stored;
- each search page is snapshotted before parsing (`person_level_24m` evidence, linked to the
  repo's open case if one exists, else the repo if it is in `repos`); every item on the page is
  registered for deletion sync (`pigtail.privacy.deletion_sync.track_items`), mention or not,
  because its content sits in the snapshot;
- with a Firebase connector, each mention's item is also snapshotted on its own (item-level
  evidence, `deleted` / `dead` flags, and the snapshot that deletion sync drops for that item only);
- mentions go to `hn_mention` (pseudonymized author, no comment text; registered in PERSON_TABLES).

A repo on the refusal list (CB-13) raises `RepoSuppressed` before any request, whether it was
opted out by id or by name (M1-T23: the name also covers repos not yet in `repos`).

A search page or item that fails to parse is dropped at once (CB-23b; the connectors'
`parse_failure_sink`), and only counted in the run record.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from pigtail.capture.db import CaptureDB
from pigtail.capture.repos import RepoLink, link_repo
from pigtail.capture.runs import RunRecorder
from pigtail.connectors.base import FetchError, ParseFailed, Record
from pigtail.connectors.hn import AlgoliaQuery, HNAlgoliaConnector, HNFirebaseConnector
from pigtail.privacy.deletion import DeletionLog, unparseable_sink
from pigtail.privacy.deletion_sync import HN_POLICY, track_items

FULL_NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")
KIND_ORDER = ("url", "full_name", "name_and_owner", "name")


class RepoSuppressed(RuntimeError):
    """The repo is on the refusal list (CB-13): nothing is captured about it."""


def split_full_name(full_name: str) -> tuple[str, str]:
    fn = full_name.strip().removeprefix("https://").removeprefix("github.com/").strip("/")
    if not FULL_NAME_RE.match(fn):
        raise ValueError(f"expected owner/name, got {full_name!r}")
    owner, name = fn.split("/", 1)
    return owner.lower(), name.lower().removesuffix(".git")


def build_queries(owner: str, name: str, *, loose: bool = False) -> list[AlgoliaQuery]:
    qs = [
        AlgoliaQuery("url", f"github.com/{owner}/{name}", tags="story", restrict_to_url=True),
        AlgoliaQuery("full_name", f"{owner}/{name}"),
        AlgoliaQuery("name_and_owner", f"{name} {owner}"),
    ]
    if loose:
        qs.append(AlgoliaQuery("name", name))
    return qs


def _word(term: str, hay: str) -> bool:
    return re.search(rf"(?<![\w.-]){re.escape(term)}(?![\w-])", hay) is not None


def classify_mention(rec: Record, owner: str, name: str) -> str | None:
    """Strongest mention rule the record meets, or None."""
    full = f"{owner}/{name}"
    if full in (rec.get("repo_full_names") or []):
        return "url"
    hay = " ".join(str(rec[k]) for k in ("title", "url", "text") if rec.get(k)).lower()
    if _word(full, hay):
        return "full_name"
    has_name = _word(name, hay)
    if has_name and _word(owner, hay):
        return "name_and_owner"
    return "name" if has_name else None


@dataclass
class MentionResult:
    repo: str
    repo_id: str | None
    case_id: str | None
    pages: int = 0
    hits: int = 0
    items_tracked: int = 0
    mentions: dict[str, int] = field(default_factory=dict)
    name_only_skipped: int = 0
    items_snapshotted: int = 0
    items_missing: int = 0
    items_failed: int = 0
    truncated_windows: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def capture_hn_mentions(
    algolia: HNAlgoliaConnector,
    db: CaptureDB,
    full_name: str,
    *,
    firebase: HNFirebaseConnector | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    loose: bool = False,
    run: RunRecorder | None = None,
) -> MentionResult:
    owner, name = split_full_name(full_name)
    link = link_repo(db, f"{owner}/{name}")
    if link.repo_id and link.repo_id in algolia.suppression.repos:
        raise RepoSuppressed(f"{link.repo_id} is on the refusal list (CB-13)")
    if algolia.suppression.name_suppressed(f"{owner}/{name}"):
        raise RepoSuppressed("the repo is on the refusal list by name (CB-13, M1-T23)")
    dlog = DeletionLog(db, "retention", run_id=run.id if run else None)
    for c in (algolia, firebase):
        if c is not None and c.evidence_sink is None:
            c.evidence_sink = db.upsert_evidence
        if c is not None and c.parse_failure_sink is None:
            c.parse_failure_sink = unparseable_sink(db, c.store, dlog, run=run)
    res = MentionResult(repo=link.full_name, repo_id=link.repo_id, case_id=link.case_id)
    open_case = link.case_id is not None
    found: dict[int, tuple[Record, str, str, datetime]] = {}  # item -> rec, kind, evidence, at
    seen_pages: set[str] = set()
    for q in build_queries(owner, name, loose=loose):
        sr = algolia.search(q, since=since, until=until, case_id=link.case_id, repo_id=link.repo_id)
        res.truncated_windows += sr.truncated_windows
        for page in sr.pages:
            ev = page.fetched.evidence
            if ev.id in seen_pages:
                continue
            seen_pages.add(ev.id)
            res.pages += 1
            res.hits += len(page.records)
            res.items_tracked += track_items(
                db,
                HN_POLICY,
                ev.id,
                [(str(r["item_id"]), r.get("author")) for r in page.records],
                seen_at=ev.fetched_at,
                open_case=open_case,
            )
            for rec in page.records:
                kind = classify_mention(rec, owner, name)
                if kind is None:
                    continue
                if kind == "name" and not loose:
                    res.name_only_skipped += 1
                    continue
                prev = found.get(rec["item_id"])
                if prev is None or KIND_ORDER.index(kind) < KIND_ORDER.index(prev[1]):
                    found[rec["item_id"]] = (rec, kind, ev.id, ev.fetched_at)
    for item_id, (rec, kind, ev_id, at) in sorted(found.items()):
        item_ev = _item_snapshot(db, firebase, item_id, link, res) if firebase else None
        _upsert_mention(db, link, rec, kind, ev_id, item_ev, at)
        res.mentions[kind] = res.mentions.get(kind, 0) + 1
    if run is not None:
        run.incr("hn.pages", res.pages)
        run.incr("hn.hits", res.hits)
        run.incr("hn.mentions", len(found))
        run.incr("hn.items_snapshotted", res.items_snapshotted)
    return res


def _item_snapshot(
    db: CaptureDB, fb: HNFirebaseConnector, item_id: int, link: RepoLink, res: MentionResult
) -> str | None:
    try:
        f, rec = fb.fetch_item(item_id, case_id=link.case_id, repo_id=link.repo_id)
    except (FetchError, ParseFailed):
        res.items_failed += 1
        return None
    if rec is None:
        res.items_missing += 1  # `null`: nothing personal in the snapshot, nothing to track
        return None
    res.items_snapshotted += 1
    track_items(
        db,
        HN_POLICY,
        f.evidence.id,
        [(str(item_id), rec.get("by"))],
        seen_at=f.evidence.fetched_at,
        open_case=link.case_id is not None,
    )
    return f.evidence.id


def _upsert_mention(
    db: CaptureDB,
    link: RepoLink,
    rec: Record,
    kind: str,
    evidence_id: str,
    item_evidence_id: str | None,
    at: datetime,
) -> None:
    typ = rec.get("type") if rec.get("type") in ("story", "comment", "poll", "job") else "other"
    db.conn.execute(
        """
        INSERT INTO hn_mention (repo_full_name, item_id, item_type, author, created_at, story_id,
            parent_id, points, num_comments, title, url, match_kind, front_page_tag, show_hn,
            ask_hn, repo_id, case_id, evidence_id, item_evidence_id, first_seen_at, last_seen_at)
        VALUES (%(repo)s, %(id)s, %(type)s, %(author)s, %(created)s, %(story)s, %(parent)s,
            %(points)s, %(nc)s, %(title)s, %(url)s, %(kind)s, %(fp)s, %(show)s, %(ask)s,
            %(repo_id)s, %(case_id)s, %(ev)s, %(iev)s, %(at)s, %(at)s)
        ON CONFLICT (repo_full_name, item_id) DO UPDATE SET
            author = EXCLUDED.author, points = EXCLUDED.points,
            num_comments = EXCLUDED.num_comments, title = EXCLUDED.title, url = EXCLUDED.url,
            match_kind = EXCLUDED.match_kind, front_page_tag = EXCLUDED.front_page_tag,
            repo_id = COALESCE(EXCLUDED.repo_id, hn_mention.repo_id),
            case_id = COALESCE(EXCLUDED.case_id, hn_mention.case_id),
            evidence_id = EXCLUDED.evidence_id,
            item_evidence_id = COALESCE(EXCLUDED.item_evidence_id, hn_mention.item_evidence_id),
            last_seen_at = GREATEST(hn_mention.last_seen_at, EXCLUDED.last_seen_at)
        """,
        {
            "repo": link.full_name,
            "id": rec["item_id"],
            "type": typ,
            "author": rec.get("author"),
            "created": rec.get("created_at"),
            "story": _int(rec.get("story_id")),
            "parent": _int(rec.get("parent_id")),
            "points": _int(rec.get("points")),
            "nc": _int(rec.get("num_comments")),
            "title": rec.get("title") if typ == "story" else None,
            "url": rec.get("url") if typ == "story" else None,
            "kind": kind,
            "fp": bool(rec.get("front_page_tag")),
            "show": bool(rec.get("show_hn")),
            "ask": bool(rec.get("ask_hn")),
            "repo_id": link.repo_id,
            "case_id": link.case_id,
            "ev": evidence_id,
            "iev": item_evidence_id,
            "at": at,
        },
    )


def _int(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None
