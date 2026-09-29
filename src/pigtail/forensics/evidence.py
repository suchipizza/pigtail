"""The case-evidence stage of the pilot (PRD F5 R5.1, §5.1, R19.1, R19.8; codebook §2; ADR-073.2,
ADR-086): project-level evidence for each selected case, snapshot or drop, checkpointed per case.

**Collected per case** (each item an evidence record with a content hash):

- `repo_metadata`: description, topics, language, homepage, created, archived/fork, owner
  type; one GitHub GraphQL query (1 point); stored as the API answer (stars and forks are in it
  but never shown to the coders).
- `readme_current`: the README now, `GET /repos/{o}/{r}/readme` (JSON); the API answer.
- `readme_at_anchor`: the README at the last commit touching it before T:
  `GET …/commits?path=&until=T&per_page=1` (dropped right after parsing: it names people),
  then `GET …/readme?ref=<sha>`; the API answer.
- `releases`: releases published in [T - 90 d, T + 30 d] with their notes (cut to 1,500 chars,
  owner login and identifiers scrubbed), from `GET …/releases` pages (dropped after parsing:
  they embed authors); a derived JSON snapshot.
- `launch_events`: the launch events the selection stored, no request: Show HN / Launch HN
  (title, points, time; no comments), Product Hunt (id, dates, votes, comments),
  declared-maintainer Bluesky posts (time only, no text), launch-worded releases, the first
  external HN mention (time), the anchor and relaunch events; a derived JSON snapshot.
- `homepage`: the project's own page, one GET, robots.txt respected
  (`pigtail.connectors.project_page`); the page.

**Recorded as gaps** (never collected here): the person-level sources held until CB-12 and CB-06b
(HN comments and mentions, Bluesky mention text, per-repo event actors; ADR-073.2), Reddit and X
(gap sources), docs pages (the pilot frame needs none), and any item that could not be fetched
(`fetch_failed:<status>`), is missing (`no_readme`, `no_readme_commit_before_anchor`,
`no_homepage`, `not_project_page`, `robots_disallowed`) or has no token (`no_github_token`).

GitHub requests go through the connector's budget and rate limits (`GitHubConnector`, the
existing per-hour caps and a job cap set by the CLI); a `BudgetExhausted` stop pauses the stage
and the next invocation resumes at the first case not done. Per case the stage counts its
requests per bucket (`evidence_stats`), which the cost report and the estimate use.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg

from pigtail.briefs.candidates import Candidate, strip_owner
from pigtail.capture.db import CaptureDB
from pigtail.capture.models import Evidence, evidence_id
from pigtail.capture.snapshots import SnapshotMeta, SnapshotStore, sha256_hex
from pigtail.forensics import store as fstore
from pigtail.forensics.prompts import RenderedItem, blind_obj, blind_text
from pigtail.forensics.store import EvidenceRow, PilotCase
from pigtail.llm.trim import TrimPolicy, trim_text
from pigtail.pseudonymize import scrub_identifiers

EVIDENCE_VERSION = "case-evidence-v1"
DERIVED_SOURCE = "pigtail_derived"
DERIVED_VERSION = "pigtail_derived/0.1.0"
RELEASE_WINDOW_BEFORE = timedelta(days=90)  # codebook §3.5: prep look-back
RELEASE_WINDOW_AFTER = timedelta(days=30)
RELEASE_MAX_PAGES = 3
RELEASE_BODY_CHARS = 1500
README_CHARS = 6000
HOMEPAGE_CHARS = 4000
CASE_MAX_CHARS = 30_000

# Sources recorded as gaps for every case (ADR-073.2; source matrix gaps).
STANDING_GAPS: tuple[tuple[str, str], ...] = (
    ("hn_comments_and_mentions", "held_person_level_adr_073_2"),
    ("bluesky_mention_text", "held_person_level_adr_073_2"),
    ("repo_event_actors", "held_person_level_adr_073_2"),
    ("reddit", "source_gap_no_api_approval"),
    ("x", "source_gap_paid_api_usd0_cap"),
    ("docs_pages", "not_needed_by_pilot_frame"),
)

LAUNCH_SIGNAL_KEYS: dict[str, tuple[str, ...]] = {
    "show_hn": ("hn_item_id", "title", "points", "time"),
    "hn_launch_lookup": ("hn_item_id", "time", "points", "kind", "match"),
    "ph_launch": ("status",),
    "bsky_maintainer_posts": ("status", "posts_without_launch_wording"),
    "gh_releases": ("status", "complete"),
    "hn_first_mention": ("status", "item_id", "time", "kind"),
}
PH_POST_KEYS = ("id", "createdAt", "featuredAt", "votesCount", "commentsCount", "confirmed")
BSKY_POST_KEYS = ("kind", "time", "role", "match")


@dataclass
class CaseResult:
    items: list[str] = field(default_factory=list)
    gaps: dict[str, str] = field(default_factory=dict)
    requests: dict[str, int] = field(default_factory=dict)

    def req(self, bucket: str, n: int = 1) -> None:
        self.requests[bucket] = self.requests.get(bucket, 0) + n

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": sorted(self.items),
            "gaps": dict(sorted(self.gaps.items())),
            "requests": dict(sorted(self.requests.items())),
            "version": EVIDENCE_VERSION,
        }


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(UTC).isoformat() if dt is not None else None


def launch_events_doc(case: PilotCase, cand: Candidate | None) -> dict[str, Any]:
    """The stored launch events of a case as one project-level document (no text of posts, no
    handle, no comment): whitelisted fields of the selection's signals, the anchor and the
    relaunch events."""
    signals: list[dict[str, Any]] = []
    for sig in (cand.sources if cand is not None else []) or []:
        src = str(sig.get("source") or "")
        keys = LAUNCH_SIGNAL_KEYS.get(src)
        if keys is None:
            continue
        rec: dict[str, Any] = {"source": src, **{k: sig.get(k) for k in keys if k in sig}}
        if src == "ph_launch":
            rec["posts"] = [
                {k: p.get(k) for k in PH_POST_KEYS if k in p}
                for p in sig.get("posts") or []
                if isinstance(p, dict)
            ]
        elif src == "bsky_maintainer_posts":
            rec["posts"] = [
                {k: p.get(k) for k in BSKY_POST_KEYS if k in p}
                for p in sig.get("posts") or []
                if isinstance(p, dict)
            ]
        elif src == "gh_releases":
            rels = [r for r in sig.get("releases") or [] if isinstance(r, dict)]
            rec["launch_worded"] = [
                {"tag": r.get("tag"), "published_at": r.get("published_at")}
                for r in rels
                if r.get("launch")
            ]
            rec["releases_total"] = len(rels)
        if isinstance(rec.get("title"), str):
            rec["title"] = scrub_identifiers(strip_owner(rec["title"], case.owner) or "")
        signals.append(rec)
    signals.sort(key=lambda r: (r["source"], str(r.get("time") or ""), str(r.get("hn_item_id"))))
    anchor = {k: v for k, v in case.anchor.items() if k != "relaunch_events"}
    return {
        "repo": f"[owner]/{case.repo_full_name.partition('/')[2]}",
        "anchor": anchor,
        "relaunch_events": list(case.anchor.get("relaunch_events") or []),
        "signals": signals,
        "not_collected": [s for s, _r in STANDING_GAPS if s != "docs_pages"],
    }


def _derived_url(full: str, what: str) -> str:
    """A purge-matchable URL for a derived document (the repo's API prefix, CB-13c)."""
    return f"https://api.github.com/repos/{full}/pigtail-derived/{what}"


def store_derived(
    db: CaptureDB,
    snaps: SnapshotStore,
    *,
    url: str,
    doc: Mapping[str, Any],
    repo_id: str | None,
    now: datetime,
    run_id: str | None,
    terms_basis: str,
) -> Evidence:
    """Snapshot a derived JSON document and record its evidence (snapshot or drop)."""
    data = json.dumps(doc, sort_keys=True, ensure_ascii=False, indent=1).encode()
    meta = SnapshotMeta(
        source=DERIVED_SOURCE,
        url=url,
        fetched_at=now,
        collector_version=DERIVED_VERSION,
        terms_basis=terms_basis,
        content_type="application/json",
    )
    h = snaps.put(data, meta)
    ev = Evidence(
        id=evidence_id(DERIVED_SOURCE, url, h),
        source=DERIVED_SOURCE,
        url=url,
        fetched_at=now,
        content_hash=h,
        snapshot_ref=snaps.ref(h),
        content_type="application/json",
        http_status=None,
        reliability="high",
        terms_basis=terms_basis,
        retention_class="project_level",
        deletion_state="present",
        collector_version=DERIVED_VERSION,
        case_id=None,
        repo_id=repo_id,
        run_id=run_id,
    )
    db.upsert_evidence(ev)
    return ev


class EvidenceStage:
    """Collects the items of each pilot case (module docstring). `github` is a
    `GitHubConnector` (None without GITHUB_TOKEN: its items become gaps); `pages` a
    `ProjectPageConnector` (None: the homepage is a gap)."""

    def __init__(
        self,
        conn: psycopg.Connection[Any],
        *,
        snapshots: SnapshotStore,
        github: Any = None,
        pages: Any = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        run_id: str | None = None,
        code_commit: str | None = None,
        schedule_decay: bool = True,
    ) -> None:
        self.conn = conn
        self.schedule_decay = schedule_decay
        self.db = CaptureDB(conn)
        self.snaps = snapshots
        self.github = github
        self.pages = pages
        self.clock = clock
        self.run_id = run_id
        self.code_commit = code_commit

    def _dlog(self) -> Any:
        from pigtail.privacy.deletion import DeletionLog

        return DeletionLog(self.db, "retention", run_id=self.run_id)

    def run(
        self,
        pilot: dict[str, Any],
        cases: Sequence[PilotCase],
        candidates: Mapping[str, Candidate],
    ) -> dict[str, Any]:
        """Every case not done yet; raises `BudgetExhausted` (resumable) from GitHub."""
        from pigtail.privacy.snapshot_retention import link

        done = 0
        for case in cases:
            if case.evidence_status == "done":
                continue
            res = self.collect(pilot, case, candidates.get(case.candidate_ref))
            link(self.db, pilot["brief_run_id"], res.items)  # R19.9 retention anchor
            fstore.set_case_evidence(self.conn, pilot["brief_run_id"], case.case_key, res.to_dict())
            case.evidence_status = "done"
            case.evidence_stats = res.to_dict()
            done += 1
        return {"cases_collected": done}

    # --- one case ------------------------------------------------------------------------------
    def collect(self, pilot: dict[str, Any], case: PilotCase, cand: Candidate | None) -> CaseResult:
        res = CaseResult()
        now = self.clock()

        def item(row: EvidenceRow) -> None:
            fstore.add_evidence(
                self.conn,
                pilot,
                case,
                row,
                captured_at=now,
                code_commit=self.code_commit,
                schedule_decay=self.schedule_decay,
            )
            res.items.append(row.evidence_id)

        def gap(source: str, reason: str, **detail: Any) -> None:
            fstore.add_gap(self.conn, pilot["brief_run_id"], case, source, reason, detail)
            res.gaps[source] = reason

        for source, reason in STANDING_GAPS:
            gap(source, reason)
        # launch events: stored signals, no request
        doc = launch_events_doc(case, cand)
        ev = store_derived(
            self.db,
            self.snaps,
            url=_derived_url(case.repo_full_name, "launch-events"),
            doc=doc,
            repo_id=case.repo_id,
            now=now,
            run_id=self.run_id,
            terms_basis="derived from the selection's stored launch signals (ADR-083..085)",
        )
        dated = [str(s.get("time")) for s in doc["signals"] if s.get("time")]
        item(
            EvidenceRow(
                "launch_events",
                ev.id,
                ev.content_hash,
                min(dated)[:10] if dated else str(case.anchor.get("at", ""))[:10],
                detail={"signals": len(doc["signals"])},
            )
        )
        if self.github is None:
            for k in ("repo_metadata", "readme_current", "readme_at_anchor", "releases"):
                gap(k, "no_github_token")
            homepage = (cand.metadata.get("homepage") if cand is not None else None) or None
        else:
            homepage = self._github_items(case, res, item, gap)
        self._homepage(case, homepage, res, item, gap)
        return res

    def _github_items(
        self,
        case: PilotCase,
        res: CaseResult,
        item: Callable[[EvidenceRow], None],
        gap: Callable[..., None],
    ) -> str | None:
        from pigtail.connectors.base import FetchError
        from pigtail.connectors.github import (
            parse_commit_refs,
            parse_readme_json,
            parse_release_notes,
            parse_repo_node,
            readme_url,
            releases_url,
            repo_meta_query,
        )
        from pigtail.privacy.deletion import PARSE_ERRORS
        from pigtail.privacy.deletion import drop_after_parse as drop

        gh = self.github
        full = case.repo_full_name
        homepage: str | None = None
        # 1. metadata (GraphQL, project-level; stars and forks are never shown to coders)
        try:
            q, v = repo_meta_query([full])
            r = gh.graphql(q, v, est_cost=1)
            res.req("graphql", int(r.cost or 1))
            node = r.data.get("r0")
            meta = parse_repo_node(node)
            if meta is None:
                gap("repo_metadata", "not_found")
            else:
                homepage = meta.homepage
                item(
                    EvidenceRow(
                        "repo_metadata",
                        r.fetched.evidence.id,
                        r.fetched.content_hash,
                        (meta.created_at.date().isoformat() if meta.created_at else ""),
                        decay_url=f"https://api.github.com/repos/{full}",
                    )
                )
        except FetchError as e:
            gap("repo_metadata", f"fetch_failed:{e.status}")
        # 2. README now (JSON: path and content)
        path: str | None = None
        try:
            res.req("core")
            f = gh.readme_json(full, repo_id=case.repo_id)
            if f is None:
                gap("readme_current", "no_readme")
            else:
                path = parse_readme_json(f.data).path
                item(
                    EvidenceRow(
                        "readme_current",
                        f.evidence.id,
                        f.content_hash,
                        self.clock().date().isoformat(),
                        decay_url=readme_url(full),
                        upstream_hash=f.content_hash,
                    )
                )
        except FetchError as e:
            gap("readme_current", f"fetch_failed:{e.status}")
        except PARSE_ERRORS:
            gap("readme_current", "parse_failed")
        # 3. README at the last commit touching it before T (none without an anchor)
        anchor_at = case.anchor_at
        if anchor_at is None:
            gap("readme_at_anchor", "no_anchor")
        elif path is None:
            gap("readme_at_anchor", "no_readme")
        else:
            try:
                res.req("core")
                c = gh.commits_page(full, path=path, until=anchor_at, repo_id=case.repo_id)
                try:
                    refs = parse_commit_refs(c.data)
                finally:
                    drop(self.db, gh.store, c.evidence.id, c.content_hash, self._dlog())
                if not refs:
                    gap("readme_at_anchor", "no_readme_commit_before_anchor")
                else:
                    sha, when = refs[0]
                    res.req("core")
                    f2 = gh.readme_json(full, ref=sha, repo_id=case.repo_id)
                    if f2 is None:
                        gap("readme_at_anchor", "no_readme_at_commit")
                    else:
                        parse_readme_json(f2.data)
                        item(
                            EvidenceRow(
                                "readme_at_anchor",
                                f2.evidence.id,
                                f2.content_hash,
                                when.date().isoformat() if when else "",
                                decay_url=f"{readme_url(full)}?ref={sha}",
                                upstream_hash=f2.content_hash,
                                detail={"commit_before_anchor": True},
                            )
                        )
            except FetchError as e:
                gap("readme_at_anchor", f"fetch_failed:{e.status}")
            except PARSE_ERRORS:
                gap("readme_at_anchor", "parse_failed")
        # 4. releases around T, with notes (raw pages dropped: they embed authors); without an
        #    anchor, the newest releases of the first pages, no window (ADR-089 addendum 2)
        lo = None if anchor_at is None else anchor_at - RELEASE_WINDOW_BEFORE
        hi = None if anchor_at is None else anchor_at + RELEASE_WINDOW_AFTER
        kept: list[dict[str, Any]] = []
        first_hash: str | None = None
        status = "complete"
        try:
            for page in range(1, RELEASE_MAX_PAGES + 1):
                res.req("core")
                f3 = gh.releases_page(full, page=page, repo_id=case.repo_id)
                first_hash = first_hash or f3.content_hash
                try:
                    notes = parse_release_notes(f3.data, body_chars=RELEASE_BODY_CHARS)
                finally:
                    drop(self.db, gh.store, f3.evidence.id, f3.content_hash, self._dlog())
                for n in notes:
                    if n.published_at is None or (
                        lo is not None and hi is not None and not lo <= n.published_at <= hi
                    ):
                        continue
                    kept.append(
                        {
                            "tag": n.tag,
                            "name": scrub_identifiers(strip_owner(n.name, case.owner) or ""),
                            "published_at": _iso(n.published_at),
                            "prerelease": n.prerelease,
                            "notes": scrub_identifiers(strip_owner(n.body, case.owner) or ""),
                        }
                    )
                dates = [n.published_at for n in notes if n.published_at is not None]
                if len(notes) < 100 or (lo is not None and dates and min(dates) < lo):
                    break
            else:
                status = "truncated"
        except FetchError as e:
            gap("releases", f"fetch_failed:{e.status}")
            status = "failed"
        except PARSE_ERRORS:
            gap("releases", "parse_failed")
            status = "failed"
        if status != "failed":
            kept.sort(key=lambda r: (r["published_at"], r["tag"]))
            doc = {
                "repo": f"[owner]/{full.partition('/')[2]}",
                "window": {"from": _iso(lo), "to": _iso(hi)},
                **({} if anchor_at is not None else {"no_anchor": True}),
                "status": status,
                "releases": kept,
            }
            ev = store_derived(
                self.db,
                self.snaps,
                url=_derived_url(full, "releases"),
                doc=doc,
                repo_id=case.repo_id,
                now=self.clock(),
                run_id=self.run_id,
                terms_basis="derived from GitHub releases pages (TM-02); authors never read",
            )
            item(
                EvidenceRow(
                    "releases",
                    ev.id,
                    ev.content_hash,
                    (kept[-1]["published_at"] or "")[:10]
                    if kept
                    else (anchor_at or self.clock()).date().isoformat(),
                    decay_url=f"{releases_url(full)}?per_page=100&page=1",
                    upstream_hash=first_hash,
                    detail={"releases": len(kept), "status": status},
                )
            )
        return homepage

    def _homepage(
        self,
        case: PilotCase,
        homepage: str | None,
        res: CaseResult,
        item: Callable[[EvidenceRow], None],
        gap: Callable[..., None],
    ) -> None:
        from pigtail.connectors.base import FetchError
        from pigtail.connectors.project_page import project_page_url

        url, why = project_page_url(homepage, case.repo_full_name)
        if url is None:
            gap("homepage", why or "no_homepage")
            return
        if self.pages is None:
            gap("homepage", "connector_off")
            return
        # at most one fetch per page and day (TM-29 c): reuse today's snapshot
        today = self.clock().date()
        row = self.conn.execute(
            "SELECT id, content_hash FROM evidence WHERE source = 'project_page' AND url = %s"
            " AND fetched_at::date = %s AND deletion_state = 'present' ORDER BY fetched_at DESC"
            " LIMIT 1",
            (url, today),
        ).fetchone()
        if row is not None:
            item(
                EvidenceRow(
                    "homepage",
                    row[0],
                    row[1],
                    today.isoformat(),
                    decay_url=url,
                    upstream_hash=row[1],
                    detail={"reused_same_day": True},
                )
            )
            return
        try:
            res.req("other")
            if not self.pages.allowed(url):
                gap("homepage", "robots_disallowed")
                return
            res.req("other")
            f = self.pages.fetch_page(url, repo_id=case.repo_id)
        except FetchError as e:
            gap("homepage", f"fetch_failed:{e.status}")
            return
        item(
            EvidenceRow(
                "homepage",
                f.evidence.id,
                f.content_hash,
                today.isoformat(),
                decay_url=url,
                upstream_hash=f.content_hash,
            )
        )


# --- rendering for the coders ------------------------------------------------------------------
SOURCE_LABEL = {
    "repo_metadata": ("GitHub GraphQL API (project metadata, now)", "api_json"),
    "readme_current": ("GitHub REST API (README, now)", "api_json"),
    "readme_at_anchor": ("GitHub REST API (README at the last commit before T)", "api_json"),
    "releases": ("GitHub releases around T (derived from the API)", "api_json"),
    "launch_events": ("launch events stored by the selection (HN, Product Hunt, ...)", "api_json"),
    "homepage": ("the project's homepage (rendered HTML)", "rendered_html"),
}


def _trim(text: str, chars: int) -> str:
    t = trim_text(
        text,
        policy=TrimPolicy(
            max_item_chars=chars, excerpt_above_chars=10**9, max_total_chars=chars + 10
        ),
    )
    return t.text


def render_text(kind: str, data: bytes) -> str:
    """The text of one stored item as the coders see it (before owner-stripping and redaction):
    blind to outcome-proximal numbers (`prompts.BLIND_KEYS` dropped from structured items,
    counts in text withheld; ADR-086). Deterministic, so a resumed run rebuilds the same input."""
    return blind_text(_render(kind, data))


def _render(kind: str, data: bytes) -> str:
    from pigtail.connectors.github import parse_readme_json, parse_repo_node
    from pigtail.connectors.project_page import visible_text

    if kind == "repo_metadata":
        node = (json.loads(data).get("data") or {}).get("r0")
        m = parse_repo_node(node)
        if m is None:
            return "(no metadata)"
        lines = [
            f"Repository: {m.full_name}",
            f"Description: {m.description or '(none)'}",
            f"Topics: {', '.join(m.topics) or '(none)'}",
            f"Primary language: {m.language or '(none)'}",
            f"Homepage field: {m.homepage or '(none)'}",
            f"Created: {_iso(m.created_at) or 'unknown'}",
            f"Owner type: {m.owner_type or 'unknown'}",
            f"Archived: {m.archived}; fork: {m.fork}",
        ]
        return "\n".join(lines)
    if kind in ("readme_current", "readme_at_anchor"):
        return _trim(parse_readme_json(data).text, README_CHARS)
    if kind == "homepage":
        return _trim(visible_text(data), HOMEPAGE_CHARS)
    doc = json.loads(data)
    if kind == "releases":
        rels = doc.get("releases") or []
        w = doc.get("window") or {}
        span = (
            f"{w['from'][:10]} .. {w['to'][:10]}"
            if w.get("from") and w.get("to")
            else "at any date (the case has no anchor)"
        )
        head = f"Releases published {span}: {len(rels)} (list {doc.get('status')})"
        parts = [head]
        for r in rels:
            pre = " (prerelease)" if r.get("prerelease") else ""
            parts.append(
                f"- {r.get('tag')} — {r.get('name') or ''} — published "
                f"{str(r.get('published_at'))[:16]}{pre}\n  {r.get('notes') or '(no notes)'}"
            )
        return "\n".join(parts)
    if kind == "launch_events":
        return json.dumps(blind_obj(doc), sort_keys=True, ensure_ascii=False, indent=1)
    raise ValueError(f"unknown evidence kind {kind!r}")


def rendered_items(
    conn: psycopg.Connection[Any], snaps: SnapshotStore, brief_run_id: str, case: PilotCase
) -> tuple[list[RenderedItem], list[str]]:
    """The case's items as the coders see them (owner login replaced by `[owner]`; redaction
    happens in the LLM client, on the whole input), and the kinds whose snapshot is gone.
    Report-only kinds (`hn_stories`, `star_trajectory`; M24) are never offered to the coders:
    they carry outcome-proximal numbers (blind-v1) and are not part of the coding frame."""
    from pigtail.capture.snapshots import SnapshotError

    items: list[RenderedItem] = []
    missing: list[str] = []
    total = 0
    for r in fstore.evidence_rows(conn, brief_run_id, case.case_key):
        if r["kind"] not in SOURCE_LABEL:
            continue
        try:
            data = snaps.get(r["content_hash"])
        except (SnapshotError, KeyError):
            missing.append(r["kind"])
            continue
        text = strip_owner(render_text(r["kind"], data), case.owner) or ""
        room = max(0, CASE_MAX_CHARS - total)
        if len(text) > room:
            text = _trim(text, max(room, 200))
        total += len(text)
        label, mode = SOURCE_LABEL[r["kind"]]
        header = f"kind: {r['kind']} | source: {label} | capture: {mode} | dated: " + (
            r["item_date"] or "undated"
        )
        items.append(RenderedItem(r["evidence_id"], r["kind"], r["item_date"] or "", header, text))
    return items, missing


def content_fingerprint(items: Sequence[RenderedItem]) -> str:
    return sha256_hex("\n".join(i.block() for i in items).encode())
