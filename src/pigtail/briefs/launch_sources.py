"""View B's launch events from Product Hunt and from declared maintainers' Bluesky posts (the
owner's decisions of 2026-09-27; ADR-085). Both are steps of the selection's fetch
(`pigtail.briefs.outcomes.fetch_outcome_data`), after the HN launch lookup and before the
releases and the first-mention search; both are checkpointed per repo and project-level in what
they store.

**Product Hunt** (`run_product_hunt`; connector `pigtail.connectors.producthunt`, TM-16). The API
has no text or URL search, so a shortlisted repo is matched to a Product Hunt post by name, two
routes:

- (a) **slug lookup**: `post(slug: …)` for at most `PH_SLUG_CANDIDATES` (2) slug candidates
  derived from the GitHub name (`ph_slug_candidates`: lowercased, `_` and `.` turned into `-`;
  then the same without hyphens, when that differs);
- (b) **topic scan**: every page of `posts(topic: <t>, postedAfter: window start, postedBefore:
  window end, order: NEWEST)` for each topic of the parameters (`PH_TOPICS`: `open-source`,
  `developer-tools`), matched in memory against the shortlist by normalized name
  (`ph_name_key`: casefolded, every character other than a-z and 0-9 removed, so the repo name
  and its name parts joined give the same key). Since anchor-v9 (ADR-085 addendum 4) the
  listing is a **shared, instance-level cache** (`pigtail.briefs.ph_cache`, tables
  `ph_topic_post` and `ph_topic_scan`): only the gaps of the window that no complete scan of
  the last `PH_TOPIC_CACHE_MAX_AGE_DAYS` (14) days covers are read, every post of every page is
  cached (id, dates, the hash of the name), the cursor stored after each page
  (resumable), each gap scanned as calendar-month intervals with their own page cap; only the
  SHA-256 of a post's product-slot key is stored, never its name; the brief's name index is
  then built from the cached rows of the window, looked up by the hash of each repo's key. The
  scan rows a run uses are fixed in its checkpoint (`ph_cache`) and recorded in the result
  (`topic_cache`). Since anchor-v11 (addendum 5) the topic hits of every shortlisted repo are
  computed once, by the first invocation that completes every topic, and frozen in the
  checkpoint (`ph_topic_hits`, `PH_HITS_FROZEN`); a resume never reads the index again. A
  planned scan row gone before then re-plans its topic; one found missing while the index is
  read leaves the topic incomplete (`topic_plan_rows_missing`). A topic hit is read again by
  `post(id:)` before it is confirmed. A page that can't be parsed fails the topic's scan: every
  repo not done yet is stored `incomplete` (`topic_scan_failed`: no view-B anchor, counted,
  retried), and more than `PH_INCOMPLETE_MAX_SHARE` (10 %) of the repos refuses the selection
  (exit 9, resumable from the stored cursor). A month cut at `PH_TOPIC_MAX_PAGES` is
  `truncated`: never coverage, and it makes the topic incomplete the same way
  (`topic_scan_truncated`).

A post found either way is kept when its name fills the **product slot** (`ph_name_key(name)`
equals the repo's)
and it is **confirmed** (`confirm.ph_confirm_by_rules`, then the Product Hunt Haiku check,
fail-closed; repo names shorter than 5 characters or made of stop-list words confirm by the URL
and domain rules only). Stored per repo (signal `ph_launch`): per post the id, createdAt,
featuredAt, votesCount, commentsCount, route (`slug`, `topic`, `slug+topic`), confirmed,
confirmation (method or `unconfirmed:<reason>`) and the Haiku provenance; never a name, tagline or
description (they are held in memory while the repo's group is processed). Posts whose name
doesn't fill the slot are counted, not stored. Since anchor-v8 (ADR-085 addendum 3) posts of any
date are kept (the slug route has no date bound): a confirmed post before the window is a launch
before the window (view B's pre-window rule), and one after the window's end can still give the
votes of an anchor near its end. Its launch time (`featuredAt`, else `createdAt`) decides whether it
is a launch event of the window. The topic scan stays bounded to the window, so a launch before the
window that only the topic scan would find is missed. A failed request propagates (the run fails,
resumable), as in the HN lookup.

**Bluesky** (`run_bluesky`; connector `pigtail.connectors.bluesky`, TM-34). Per repo, in memory
only: the **declared accounts** (`declared_accounts`) in, in this order, the owner's GitHub
profile social accounts (`GET /users/{owner}/social_accounts`, provider `bluesky` or a bsky.app
URL; owner type User), the org page (`GET /orgs/{org}`: `blog` and `description`; owner type
Organization), the repo's homepage field (GraphQL `homepageUrl`, 50 repos per query) and the
README (from the snapshot store when present, else fetched), at most `BSKY_MAX_ACCOUNTS` (3). The
profile, org page and homepage field always count; the README counts only when it names exactly
one account (`README_MAX_ACCOUNTS`, distinct after normalization), else it declares nothing and is
counted `readme_ambiguous` (ADR-085 addendum 3; `declared_account_sources`). Then, per account,
`searchPosts` with `author` and `url` = the repo's GitHub URL, then its homepage URL when it has
one, newest first, with no date filter (the window is applied in memory), every page until the
list ends (addendum 5: to find the earliest launch-worded post), up to `BSKY_MAX_PAGES` pages each;
each post is kept only when one of its links really is that URL. A post is a **launch event only
when its text is worded as a launch** (`bsky_post_is_launch`: the release rule's pattern,
`outcomes.RELEASE_LAUNCH_RE`, on the text, in memory only); posts that link the repo without it
are counted (a number, in the window), never events or relaunches. Every raw answer (GitHub and
Bluesky) is dropped right after parsing. Stored per repo (signal `bsky_maintainer_posts`): the
status (`complete`, `no_declared_account`, or `incomplete` with a reason code), per
launch-worded post up to the window's end (before the window included) the kind
`bluesky_maintainer_post`, the time, the role `maintainer` and the match (`repo_url` |
`homepage_url`), the number of in-window posts without launch wording, and whether the README was
ambiguous; never the handle, the DID, the post URI, text, or a post's like/repost/reply counts.

**Incomplete Bluesky data** (a failed or capped search, or declared-account sources that could not
be read) does not block the selection: the repo is stored `incomplete`, its view-B anchor is
unknown (`launch_source_incomplete:bluesky`, counted), and it is tried again on the next run.
When more than `BSKY_INCOMPLETE_MAX_SHARE` (10 %) of the repos are incomplete, the selection is
refused (`LaunchSourceIncomplete`, `pigtail run` exit 9, resumable).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import psycopg

from pigtail.briefs.candidates import Candidate, CandidateStore
from pigtail.briefs.confirm import (
    PH_JOB,
    PH_NAMESPACE,
    PH_PROMPT,
    Check,
    Confirmer,
    ph_confirm_by_rules,
    ph_confirmation_params,
    ph_haiku_input,
)
from pigtail.briefs.model import Brief
from pigtail.briefs.selection import ANCHOR_RULE_VERSION, PH_TOPICS, SelectionError

PH_SOURCE = "ph_launch"
PH_SLUG_CANDIDATES = 2
PH_TOPIC_MAX_PAGES = 1000  # 20 posts per page: 20,000 posts per topic and window
PH_GROUP = 25  # repos per checkpoint group and per Haiku batch
PH_INCOMPLETE_MAX_SHARE = 0.10  # as Bluesky's: more incomplete repos refuse the selection
PH_HITS_FROZEN = "ph_topic_hits_frozen"  # checkpoint marker: the topic hits are frozen
BSKY_SOURCE = "bsky_maintainer_posts"
BSKY_KIND = "bluesky_maintainer_post"
BSKY_ROLE = "maintainer"
BSKY_MAX_ACCOUNTS = 3
BSKY_MAX_PAGES = 5  # 100 posts per page, per account and URL
BSKY_INCOMPLETE_MAX_SHARE = 0.10
README_CHARS = 200_000
README_MAX_ACCOUNTS = 1  # a README declares an account only when it names exactly one

PH_NEEDS_TOKEN = (
    "the selection's Product Hunt launch lookup needs PH_API_TOKEN (a Product Hunt developer "
    "token with the read-only public scope, in the instance's environment), and the connector is "
    "off without it. The pre-registered selection parameters say Product Hunt applies "
    "(launch_sources.product_hunt), so the selection is refused; nothing was fetched, computed "
    "or stored and the run can be resumed. Set PH_API_TOKEN, or pre-register again with "
    "PIGTAIL_SELECTION_PRODUCT_HUNT=false (ADR-085)"
)
BSKY_NEEDS_CONNECTOR = (
    "the selection's Bluesky maintainer-post search needs the Bluesky search connector "
    "(bluesky_search), which is off (PIGTAIL_CONNECTOR_BLUESKY_SEARCH_ENABLED=false) or not "
    "configured. The pre-registered selection parameters say Bluesky applies "
    "(launch_sources.bluesky), so the selection is refused; nothing was fetched, computed or "
    "stored and the run can be resumed. Turn the connector on, or pre-register again with "
    "PIGTAIL_SELECTION_BLUESKY=false (ADR-085)"
)


class LaunchSourceUnavailable(SelectionError):
    """ADR-085: a launch source the pre-registered parameters apply can't run (Product Hunt
    without its token, Bluesky connector off), so the selection is refused before anything is
    fetched, computed or stored (`pigtail run` exit 8, like the HN lookup's refusal)."""


class LaunchSourceIncomplete(SelectionError):
    """ADR-085: more than 10 % of the shortlisted repos' Bluesky data is incomplete, so the
    selection is refused (`pigtail run` exit 9); the next run retries the incomplete repos."""


def ph_blocked(ph: Any) -> str | None:
    """Why the Product Hunt step can't run (no connector, or no token), or None."""
    if ph is None or not getattr(ph, "enabled", False) or not getattr(ph, "has_token", True):
        return PH_NEEDS_TOKEN
    return None


def bsky_blocked(bsky: Any) -> str | None:
    """Why the Bluesky step can't run (no connector, or off), or None."""
    if bsky is None or not getattr(bsky, "enabled", True):
        return BSKY_NEEDS_CONNECTOR
    return None


def blocked(ph: Any, bsky: Any, *, product_hunt: bool, bluesky: bool) -> str | None:
    """The first refusal for the launch sources that apply, or None."""
    if product_hunt and (why := ph_blocked(ph)) is not None:
        return why
    if bluesky and (why := bsky_blocked(bsky)) is not None:
        return why
    return None


# --- Product Hunt: matching ---------------------------------------------------------------------
def ph_name_key(name: str | None) -> str:
    """A name as the product slot compares it: casefolded, every character other than a-z and
    0-9 removed ("Acme CLI", "acme-cli" and "acme_cli" are all `acmecli`)."""
    return re.sub(r"[^a-z0-9]+", "", (name or "").casefold())


def ph_repo_key(full_name: str) -> str:
    """The product-slot key of a repo: its GitHub name (equal to its name parts joined)."""
    return ph_name_key(full_name.split("/", 1)[-1])


def ph_slug_candidates(full_name: str) -> list[str]:
    """At most `PH_SLUG_CANDIDATES` slugs to look up: the GitHub name lowercased with `_` and
    `.` turned into `-`, then the same without hyphens when that differs."""
    s1 = re.sub(r"[_.]+", "-", full_name.split("/", 1)[-1].lower()).strip("-")
    s2 = s1.replace("-", "")
    return list(dict.fromkeys(x for x in (s1, s2) if x))[:PH_SLUG_CANDIDATES]


def ph_urls_only(full_name: str) -> bool:
    """A repo name shorter than 5 characters, or made only of stop-list words, is confirmed by
    the URL and domain rules only (the ADR-082 rule (d) thresholds)."""
    from pigtail.briefs.outcomes import TITLE_MIN_CHARS, TITLE_STOPLIST, _name_parts

    name = full_name.split("/", 1)[-1]
    parts = _name_parts(full_name)
    return len(name) < TITLE_MIN_CHARS or all(p.lower() in TITLE_STOPLIST for p in parts)


@dataclass
class PHResult:
    repos: int = 0
    looked_up: int = 0
    already_done: int = 0
    requests: int = 0
    topic_pages: dict[str, int] = field(default_factory=dict)  # pages of this run's scans
    topic_status: dict[str, str] = field(default_factory=dict)
    topic_requests: int = 0  # topic pages requested by this invocation
    # the listing snapshot used (ADR-085 addendum 4): per topic and interval, reused or scanned
    topic_cache: list[dict[str, Any]] = field(default_factory=list)
    topic_cache_days: dict[str, dict[str, float]] = field(default_factory=dict)
    # the topic hits come from the checkpoint, or were just frozen there (addendum 5)
    topic_hits_frozen: bool = False
    # topics whose planned scan rows were purged before the hits were frozen: planned again
    topic_replanned: list[str] = field(default_factory=list)
    incomplete: int = 0
    incomplete_reasons: dict[str, int] = field(default_factory=dict)
    posts: int = 0
    by_route: dict[str, int] = field(default_factory=dict)
    not_product_slot: int = 0
    no_time: int = 0
    before_window: int = 0  # kept (anchor-v8): a launch before the window when confirmed
    after_window: int = 0  # kept (anchor-v8): votes of an anchor near the window's end
    confirmed: dict[str, int] = field(default_factory=dict)
    unconfirmed: dict[str, int] = field(default_factory=dict)
    haiku_checks: int = 0
    failed: dict[str, int] = field(default_factory=dict)
    rule: str = ANCHOR_RULE_VERSION
    evidence_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d.pop("evidence_ids")
        return d


def run_product_hunt(
    conn: psycopg.Connection[Any],
    brief: Brief,
    ph: Any,
    cands: Sequence[Candidate],
    *,
    window: tuple[datetime, datetime],
    checkpoint: dict[str, Any],
    save: Callable[[dict[str, Any]], None],
    topics: Sequence[str] = PH_TOPICS,
    recorder: Any = None,
    confirmer: Confirmer | None = None,
    group_size: int = PH_GROUP,
    clock: Callable[[], datetime] | None = None,
) -> PHResult:
    """The Product Hunt step (module docstring). `LaunchSourceUnavailable` without the
    connector or its token; a failed request propagates (resumable); a Haiku batch still running
    raises `BatchPending` (the group's repos are not checkpointed, so a resume redoes them and
    collects the batch)."""
    from pigtail.briefs.ph_cache import (
        ScanRow,
        end_scan,
        gaps,
        month_intervals,
        ph_key_hash,
        record_density,
        save_page,
        scan_rows,
        start_scan,
        usable_scans,
        window_posts,
    )
    from pigtail.briefs.ph_cache import days as ph_days
    from pigtail.capture.db import CaptureDB
    from pigtail.connectors.producthunt import (
        parse_post,
        parse_posts_page,
        ph_evidence_url,
    )
    from pigtail.privacy.deletion import PARSE_ERRORS, DeletionLog
    from pigtail.privacy.deletion import drop_after_parse as drop

    assert brief.version is not None
    if (why := ph_blocked(ph)) is not None:
        raise LaunchSourceUnavailable(why)
    clock = clock or (lambda: datetime.now(UTC))
    if checkpoint.get("ph_rule") != ANCHOR_RULE_VERSION:  # an older rule's progress
        for k in ("ph_done", "ph_topics", "ph_topic_hits", "ph_counts", "ph_cache",
                  PH_HITS_FROZEN, "ph_topic_provenance"):  # fmt: skip
            checkpoint.pop(k, None)
        checkpoint["ph_rule"] = ANCHOR_RULE_VERSION
    start, end = window
    todo = sorted((c for c in cands if c.repo_full_name), key=lambda c: c.ref)
    res = PHResult(repos=len(todo))
    db = CaptureDB(conn)
    store = CandidateStore(conn, brief.brief_id, brief.version)
    dlog = DeletionLog(db, "retention", run_id=getattr(recorder, "id", None))
    done: set[str] = set(checkpoint.get("ph_done") or [])
    counts: dict[str, Any] = checkpoint.setdefault(
        "ph_counts", {"confirmed": {}, "unconfirmed": {}, "haiku_checks": 0, "not_slot": 0}
    )
    pending = [c for c in todo if c.ref not in done]
    res.already_done = len(todo) - len(pending)

    def fetch_drop(f: Any, parse: Callable[[bytes], Any]) -> Any:
        res.requests += 1
        res.evidence_ids.append(f.evidence.id)
        try:
            return parse(f.data)
        except PARSE_ERRORS:
            res.failed["parse_failed"] = res.failed.get("parse_failed", 0) + 1
            return None
        finally:
            drop(db, ph.store, f.evidence.id, f.content_hash, dlog)

    # (b) the topic scan (ADR-085 addendum 4): the shared, instance-level listing cache; only
    # the gaps of the window no fresh complete scan covers are read, as calendar-month intervals
    # (one scan row and page cap each), every post of every page cached (its name only as a
    # hash), the cursor stored after each page. The plan (the scan rows used per topic) is
    # fixed in the checkpoint at the run's first invocation. The topic hits are computed once,
    # by the first invocation that completes every topic, for every shortlisted repo, and
    # frozen in the checkpoint (addendum 5): a resume never reads the cache's index again, so
    # another run's newer listing, re-hashed rows or purged rows can't change what it matches
    hits: dict[str, list[str]] = {}
    failed_topics: list[str] = []
    why = "topic_scan_truncated"
    frozen = checkpoint.get(PH_HITS_FROZEN) is True and isinstance(
        checkpoint.get("ph_topic_hits"), dict
    )
    if frozen:
        hits = {str(k): [str(x) for x in v] for k, v in checkpoint["ph_topic_hits"].items()}
        prov = checkpoint.get("ph_topic_provenance") or {}
        res.topic_cache = list(prov.get("topic_cache") or [])
        res.topic_status = dict(prov.get("topic_status") or {})
        res.topic_pages = dict(prov.get("topic_pages") or {})
        res.topic_cache_days = dict(prov.get("topic_cache_days") or {})
        res.topic_hits_frozen = True
    plan: dict[str, dict[str, list[int]]] = checkpoint.setdefault("ph_cache", {})

    def planned(topic: str) -> tuple[list[ScanRow], list[ScanRow], bool]:
        """(reused rows, scanned rows, whether a planned row is missing)."""
        ids_r, ids_s = plan[topic]["reused"], plan[topic]["scans"]
        reused, scanned = scan_rows(conn, ids_r), scan_rows(conn, ids_s)
        return reused, scanned, len(reused) + len(scanned) < len({*ids_r, *ids_s})

    for topic in topics if pending and not frozen else ():
        if topic in plan and planned(topic)[2]:
            # a planned scan row no longer exists (the retention purge): the hits aren't frozen
            # yet, so no repo was matched against this plan; plan the topic again
            del plan[topic]
            res.topic_replanned.append(topic)
        if topic not in plan:
            use = usable_scans(conn, topic, now=clock())
            s0 = [u for u in use if u.posted_after <= end and u.posted_before >= start]
            parts = [m for g in gaps(window, [u.covered for u in s0]) for m in month_intervals(g)]
            new_scans = [start_scan(conn, topic, m, now=clock()) for m in parts]
            plan[topic] = {"reused": [u.id for u in s0], "scans": [x.id for x in new_scans]}
            save(checkpoint)
        for scan in scan_rows(conn, plan[topic]["scans"]):
            if scan.status == "failed":  # a failed page is tried again from its cursor
                scan = end_scan(conn, scan, "running", now=clock())
            while scan.status == "running":
                if scan.pages >= PH_TOPIC_MAX_PAGES:
                    scan = end_scan(conn, scan, "truncated", now=clock())
                    break
                f = ph.topic_page(
                    topic,
                    posted_after=scan.posted_after,
                    posted_before=scan.posted_before,
                    after=scan.cursor,
                    evidence_url=ph_evidence_url(
                        f"topic:{topic}",
                        after=f"{scan.posted_after.astimezone(UTC):%Y%m%dT%H%M%SZ}",
                        before=f"{scan.posted_before.astimezone(UTC):%Y%m%dT%H%M%SZ}",
                        page=scan.pages,
                    ),
                )
                res.topic_requests += 1
                got = fetch_drop(f, parse_posts_page)
                if got is None:
                    scan = end_scan(conn, scan, "failed", now=clock())
                    break
                posts, more, cursor = got
                last = not more or not cursor
                scan = save_page(
                    conn, scan, posts, cursor=cursor,
                    status="complete" if last else "running", now=clock(),
                )  # fmt: skip
    # the brief's name index, from the cache: the posts of the window the plan's rows saw, by
    # the hash of the product-slot key (the cache stores no name); read once, then frozen
    index: dict[str, list[str]] = {}
    for c in todo:
        index.setdefault(ph_key_hash(ph_repo_key(str(c.repo_full_name))), []).append(c.ref)
    used: dict[str, list[ScanRow]] = {}
    for topic in topics if not frozen else ():
        if topic not in plan:
            continue
        reused, scanned, missing = planned(topic)
        res.topic_cache += [u.to_dict(reused=True) for u in reused]
        res.topic_cache += [u.to_dict(reused=False) for u in scanned]
        statuses = {u.status for u in scanned}
        status = (
            "failed:plan_rows_missing" if missing
            else "failed:parse" if statuses & {"failed", "running"}
            else "truncated" if "truncated" in statuses else "complete"
        )  # fmt: skip
        res.topic_status[topic] = status
        res.topic_pages[topic] = sum(u.pages for u in scanned)
        total = ph_days(start, end)
        left = sum(ph_days(a, b) for a, b in gaps(window, [u.covered for u in reused]))
        res.topic_cache_days[topic] = {
            "window_days": round(total, 2),
            "reused_days": round(total - left, 2),
        }
        if status != "complete":  # a failed or truncated month: the topic can't be complete
            failed_topics.append(topic)
            if status == "failed:plan_rows_missing":
                del plan[topic]  # planned again by the next invocation
                why = "topic_plan_rows_missing" if why == "topic_scan_truncated" else why
            elif status != "truncated":
                why = "topic_scan_failed"
            continue
        used[topic] = [*reused, *scanned]
        for pid, key in window_posts(conn, topic, window, used[topic]):
            for ref in index.get(key, []):
                lst = hits.setdefault(ref, [])
                if pid not in lst:
                    lst.append(pid)
    if not frozen and not failed_topics and pending and all(t in plan for t in topics):
        # every topic complete: freeze the hits of every shortlisted repo, with the listing's
        # provenance, and keep each topic's measured density (it outlives the purged rows)
        checkpoint["ph_topic_hits"] = {k: hits[k] for k in sorted(hits)}
        checkpoint["ph_topic_provenance"] = {
            "topic_cache": res.topic_cache,
            "topic_status": res.topic_status,
            "topic_pages": res.topic_pages,
            "topic_cache_days": res.topic_cache_days,
            "repos_indexed": len(todo),
        }
        checkpoint[PH_HITS_FROZEN] = True
        for topic, rows in used.items():
            record_density(conn, topic, rows, now=clock())
        res.topic_hits_frozen = True
        save(checkpoint)
    # a topic that could not be read completely (a month failed, or hit the page cap) leaves
    # Product Hunt incomplete for every repo not done yet (ADR-085 item 8's rule, applied to
    # Product Hunt; verifier round 7): stored `incomplete`, no view-B anchor, not checkpointed,
    # retried by the next run
    if failed_topics and pending:
        for c in pending:
            store.replace_signals(
                c.ref, PH_SOURCE,
                [{"source": PH_SOURCE, "rule": ANCHOR_RULE_VERSION, "status": "incomplete",
                  "reason": why, "posts": []}],
            )  # fmt: skip
        res.incomplete = len(pending)
        res.incomplete_reasons = {why: len(pending)}
        save(checkpoint)
        if len(pending) > PH_INCOMPLETE_MAX_SHARE * len(todo):
            raise LaunchSourceIncomplete(
                f"Product Hunt: the topic scan of {', '.join(failed_topics)} could not be read "
                f"completely, so {len(pending)} of {len(todo)} shortlisted repos have incomplete "
                f"Product Hunt data (more than {PH_INCOMPLETE_MAX_SHARE:.0%}): the selection is "
                "refused so view B's anchors don't change silently. Run again later: the scan "
                "resumes from its stored cursor, and a topic whose planned scan rows are gone "
                "is planned again (ADR-085 addenda 4 and 5)"
            )
        pending = []

    def finish(c: Candidate, recs: dict[str, dict[str, Any]]) -> None:
        store.replace_signals(
            c.ref,
            PH_SOURCE,
            [{"source": PH_SOURCE, "rule": ANCHOR_RULE_VERSION, "status": "complete",
              "posts": [recs[k] for k in sorted(recs)]}],
        )  # fmt: skip
        for rec in recs.values():
            res.by_route[rec["route"]] = res.by_route.get(rec["route"], 0) + 1
            key = "confirmed" if rec["confirmed"] else "unconfirmed"
            how = str(rec["confirmation"]).removeprefix("unconfirmed:")
            counts[key][how] = counts[key].get(how, 0) + 1
        res.posts += len(recs)
        res.looked_up += 1
        done.add(c.ref)
        checkpoint["ph_done"] = sorted(done)
        save(checkpoint)

    # (a) the slug lookup, then the product slot and the confirmation, per repo
    for g in range(0, len(pending), group_size):
        group = pending[g : g + group_size]
        deferred: list[tuple[Candidate, dict[str, dict[str, Any]]]] = []
        checks: list[Check] = []
        for c in group:
            full = str(c.repo_full_name)
            found: dict[str, tuple[Any, set[str]]] = {}
            for i, slug in enumerate(ph_slug_candidates(full)):
                f = ph.post_by_slug(
                    slug, evidence_url=ph_evidence_url(f"repo:{full}", route="slug", candidate=i)
                )
                p = fetch_drop(f, parse_post)
                if p is not None:
                    found.setdefault(p.id, (p, set()))[1].add("slug")
            for pid in hits.get(c.ref, []):
                if pid in found:
                    found[pid][1].add("topic")
                    continue
                # a topic hit (the cache holds no tagline, description or counts): read fresh
                f = ph.post_by_id(pid, evidence_url=ph_evidence_url(f"repo:{full}", route="topic"))
                p = fetch_drop(f, parse_post)
                if p is not None:
                    found[p.id] = (p, {"topic"})
            recs: dict[str, dict[str, Any]] = {}
            mine: list[Check] = []
            urls_only = ph_urls_only(full)
            meta = c.metadata
            for pid in sorted(found):
                p, routes = found[pid]
                at = p.event_at
                if at is None:
                    res.no_time += 1
                    continue
                # kept whatever its date (anchor-v8): before the window it is a launch before
                # the window, after it it can still give an anchor's launch-window votes
                if at < start:
                    res.before_window += 1
                elif at > end:
                    res.after_window += 1
                if ph_name_key(p.name) != ph_repo_key(full):
                    res.not_product_slot += 1
                    counts["not_slot"] = int(counts.get("not_slot") or 0) + 1
                    continue
                rec: dict[str, Any] = {
                    "ph_post_id": p.id,
                    "created_at": p.created_at.isoformat() if p.created_at else None,
                    "featured_at": p.featured_at.isoformat() if p.featured_at else None,
                    "votes": p.votes,
                    "comments": p.comments,
                    "route": "+".join(sorted(routes)),
                    "rule": ANCHOR_RULE_VERSION,
                }
                how = ph_confirm_by_rules(
                    full_name=full,
                    description=meta.get("description"),
                    homepage_domain=meta.get("homepage_domain") or None,
                    tagline=p.tagline,
                    post_description=p.description,
                    urls_only=urls_only,
                )
                if how is not None:
                    rec["confirmed"], rec["confirmation"] = True, how
                elif urls_only:
                    rec["confirmed"], rec["confirmation"] = (
                        False,
                        "unconfirmed:short_or_common_name",
                    )
                else:
                    text = ph_haiku_input(
                        full, meta.get("description"), meta.get("homepage_domain"), p.name,
                        p.tagline, p.description,
                    )  # fmt: skip
                    mine.append(Check((c.ref, pid), text))
                recs[pid] = rec
            if mine:
                checks += mine
                deferred.append((c, recs))
            else:
                finish(c, recs)
        if not checks:
            continue
        conf = confirmer or Confirmer(None, prompt=PH_PROMPT, job=PH_JOB, namespace=PH_NAMESPACE)
        outcomes = conf.run(checks)
        counts["haiku_checks"] = int(counts.get("haiku_checks") or 0) + len(checks)
        for c, recs in deferred:
            for pid, rec in recs.items():
                o = outcomes.get((c.ref, pid))
                if o is None:
                    continue
                rec["confirmed"], rec["confirmation"] = o.confirmed, o.confirmation
                if o.provenance is not None:
                    rec["confirmation_provenance"] = o.provenance
            finish(c, recs)
    res.confirmed = dict(sorted(counts["confirmed"].items()))
    res.unconfirmed = dict(sorted(counts["unconfirmed"].items()))
    res.haiku_checks = int(counts.get("haiku_checks") or 0)
    save(checkpoint)
    return res


def ph_confirmer(llm: Any, **kw: Any) -> Confirmer:
    """The Haiku check of name-only Product Hunt matches (the Product Hunt prompt and job)."""
    return Confirmer(llm, prompt=PH_PROMPT, job=PH_JOB, namespace=PH_NAMESPACE, **kw)


# --- Bluesky: declared accounts and the search ----------------------------------------------------
@dataclass
class BskyResult:
    repos: int = 0
    searched: int = 0  # repos processed in this invocation
    already_done: int = 0
    with_declared_account: int = 0
    accounts_over_cap: int = 0
    accounts_unresolvable: int = 0  # declared links that point to no account (dead links)
    declared_in: dict[str, int] = field(default_factory=dict)  # source kind -> repos (counts)
    readme_ambiguous: int = 0  # READMEs naming 2+ accounts: they declare nothing (addendum 3)
    launch_posts: int = 0  # launch-worded posts stored (any date up to the window's end)
    launch_posts_before_window: int = 0
    posts_without_launch_wording: int = 0  # in the window: never events (addendum 3)
    bluesky_requests: int = 0
    github_requests: int = 0
    posts: int = 0
    status: dict[str, int] = field(default_factory=dict)
    incomplete: int = 0
    incomplete_reasons: dict[str, int] = field(default_factory=dict)
    rule: str = ANCHOR_RULE_VERSION
    evidence_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d.pop("evidence_ids")
        return d


def bsky_post_is_launch(text: str | None) -> bool:
    """ADR-085 addendum 3: a declared maintainer's Bluesky post is a launch event only when its
    text is worded as a launch, by the release rule's own pattern (`outcomes.RELEASE_LAUNCH_RE`,
    from `selection.RELEASE_LAUNCH_PATTERN`: whole words, case-insensitive). The text is read in
    memory only and never stored."""
    from pigtail.briefs.outcomes import RELEASE_LAUNCH_RE

    return RELEASE_LAUNCH_RE.search(text or "") is not None


def declared_account_sources(
    sources: Sequence[tuple[str, list[str]]],
    resolve: Callable[[str], str | None] | None = None,
) -> tuple[list[str], list[str], bool]:
    """(accounts in order of first appearance, the source kinds that declared at least one new
    account, README ambiguous) from the texts of each source (ADR-085 addendum 3). The GitHub
    profile's social accounts, the org page and the homepage field always count; the README
    counts only when it names exactly `README_MAX_ACCOUNTS` (1) account, distinct after
    normalization (`account_id`: a handle lowercased; a handle and a DID are two identifiers),
    else it declares nothing and is reported ambiguous. When the README names several
    identifiers and `resolve` is given (owner decision 2026-09-28), its handles are resolved to
    DIDs in memory and identifiers of the same account count once: a handle and its own DID are
    one account. A handle that doesn't resolve stays a distinct identifier. `resolve` failures
    (`FetchError`) propagate: the caller treats them as an incomplete source. In memory only."""
    from pigtail.connectors.bluesky import declared_accounts

    accounts: list[str] = []
    kinds: list[str] = []
    ambiguous = False
    for kind, texts in sources:
        named = declared_accounts(texts)
        if kind == "readme" and len(named) > README_MAX_ACCOUNTS and resolve is not None:
            same: dict[str, str] = {}  # identifier -> the account it names (a DID when known)
            for a in named:
                same[a] = a if a.startswith("did:") else (resolve(a) or a)
            distinct = list(dict.fromkeys(same.values()))
            if len(distinct) <= README_MAX_ACCOUNTS:
                named = distinct
        if kind == "readme" and len(named) > README_MAX_ACCOUNTS:
            ambiguous = True
            continue
        got = [a for a in named if a not in accounts]
        if got:
            kinds.append(kind)
        accounts += got
    return accounts, kinds, ambiguous


def _norm_url(u: str) -> tuple[str, str] | None:
    s = u.strip()
    if not s:
        return None
    if "://" not in s:
        s = "https://" + s
    try:
        parts = urlsplit(s)
    except ValueError:
        return None
    host = (parts.hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return (host, parts.path.rstrip("/").lower()) if host else None


def links_to(links: Sequence[str], match: str, full_name: str, url: str) -> bool:
    """Whether a post's links include the URL searched: the repo's github.com/owner/name (the
    A2 normalizer), or the homepage (same host without `www.`, and the same path or a path
    below it)."""
    from pigtail.connectors.hn import normalize_github_repo

    if match == "repo_url":
        return any(normalize_github_repo(u) == full_name.lower() for u in links)
    want = _norm_url(url)
    if want is None:
        return False
    for u in links:
        got = _norm_url(u)
        if got is None or got[0] != want[0]:
            continue
        if got[1] == want[1] or got[1].startswith(want[1] + "/"):
            return True
    return False


def homepage_search_url(homepage: str | None, full_name: str) -> str | None:
    """The homepage URL searched besides the repo URL: an http(s) URL that is not the repo's
    own GitHub page and not a Bluesky profile; else None."""
    from pigtail.connectors.hn import normalize_github_repo

    if not homepage or not homepage.strip():
        return None
    h = homepage.strip()
    n = _norm_url(h)
    if n is None or n[0] in ("bsky.app", "github.com"):
        return None
    if normalize_github_repo(h) == full_name.lower():
        return None
    return h if "://" in h else "https://" + h


def default_readme(github: Any) -> Callable[[Candidate], tuple[bytes | None, str | None]]:
    """The README of a repo: from the snapshot store when discovery's copy is still there
    (`metadata.readme.content_hash`), else fetched through the connector (a project page,
    kept like discovery's; ADR-072.7)."""

    def load(c: Candidate) -> tuple[bytes | None, str | None]:
        rec = c.metadata.get("readme") or {}
        if rec.get("none") or github is None or c.repo_full_name is None:
            return None, None
        h = rec.get("content_hash")
        if h and github.store.exists(h):
            return github.store.get(h), rec.get("evidence_id")
        f = github.readme(c.repo_full_name, repo_id=c.repo_id)
        return (None, None) if f is None else (f.data, f.evidence.id)

    return load


def run_bluesky(
    conn: psycopg.Connection[Any],
    brief: Brief,
    bsky: Any,
    github: Any,
    cands: Sequence[Candidate],
    *,
    window: tuple[datetime, datetime],
    checkpoint: dict[str, Any],
    save: Callable[[dict[str, Any]], None],
    recorder: Any = None,
    readme: Callable[[Candidate], tuple[bytes | None, str | None]] | None = None,
) -> BskyResult:
    """The Bluesky step (module docstring). `LaunchSourceUnavailable` without the connector;
    `LaunchSourceIncomplete` once more than 10 % of the repos are incomplete (the repos done so
    far stay checkpointed; incomplete ones are retried by the next run); `BudgetExhausted` from
    the GitHub budget propagates (the stage pauses)."""
    from pigtail.capture.db import CaptureDB
    from pigtail.connectors.base import FetchError
    from pigtail.connectors.bluesky import bsky_evidence_url, parse_search_page
    from pigtail.connectors.github import parse_org_profile, parse_social_accounts
    from pigtail.connectors.producthunt import EVIDENCE_REPO_MARK
    from pigtail.privacy.deletion import PARSE_ERRORS, DeletionLog
    from pigtail.privacy.deletion import drop_after_parse as drop

    assert brief.version is not None
    if (why := bsky_blocked(bsky)) is not None:
        raise LaunchSourceUnavailable(why)
    if checkpoint.get("bsky_rule") != ANCHOR_RULE_VERSION:
        checkpoint.pop("bsky_done", None)
        checkpoint.pop("bsky_incomplete", None)
        checkpoint["bsky_rule"] = ANCHOR_RULE_VERSION
    start, end = window
    todo = sorted((c for c in cands if c.repo_full_name), key=lambda c: c.ref)
    res = BskyResult(repos=len(todo))
    db = CaptureDB(conn)
    store = CandidateStore(conn, brief.brief_id, brief.version)
    dlog = DeletionLog(db, "retention", run_id=getattr(recorder, "id", None))
    done: set[str] = set(checkpoint.get("bsky_done") or [])
    pending = [c for c in todo if c.ref not in done]
    res.already_done = len(todo) - len(pending)
    load_readme = readme or default_readme(github)
    gh_on = github is not None and getattr(github, "enabled", True)
    links: dict[str, tuple[str | None, str | None]] = {}
    links_failed = False
    if gh_on and pending:
        try:
            links, fetched = github.repo_links([str(c.repo_full_name) for c in pending])
        except FetchError:
            links_failed = True
        else:
            for f in fetched:
                res.github_requests += 1
                res.evidence_ids.append(f.evidence.id)
                drop(db, github.store, f.evidence.id, f.content_hash, dlog)

    def gh_text(f: Any, parse: Callable[[bytes], list[str]]) -> list[str]:
        if f is None:
            return []
        res.github_requests += 1
        res.evidence_ids.append(f.evidence.id)
        try:
            return parse(f.data)
        except PARSE_ERRORS:
            return []
        finally:
            drop(db, github.store, f.evidence.id, f.content_hash, dlog)

    def incomplete(reason: str) -> dict[str, Any]:
        return {"status": "incomplete", "reason": reason, "posts": []}

    def one(c: Candidate) -> dict[str, Any]:
        """One repo's signal body. Handles live in this frame only."""
        full = str(c.repo_full_name)
        owner = full.split("/", 1)[0]
        if not gh_on:
            return incomplete("declared_sources_unavailable")
        if links_failed:
            return incomplete("declared_sources_failed")
        homepage, owner_type = links.get(full.lower(), (None, c.metadata.get("owner_type")))
        ev = f"?{EVIDENCE_REPO_MARK}{full.lower()}&purpose=declared_accounts"
        sources: list[tuple[str, list[str]]] = []
        try:
            if owner_type == "User":
                f = github.social_accounts(owner, evidence_url=f"{_gh_api()}/users/{owner}"
                                           f"/social_accounts{ev}")  # fmt: skip
                sources.append(("github_profile", gh_text(f, parse_social_accounts)))
            elif owner_type == "Organization":
                f = github.org_profile(owner, evidence_url=f"{_gh_api()}/orgs/{owner}{ev}")
                sources.append(("org_page", gh_text(f, parse_org_profile)))
            sources.append(("homepage", [homepage] if homepage else []))
            raw, _ev = load_readme(c)
        except FetchError:
            return incomplete("declared_sources_failed")
        if raw:
            sources.append(("readme", [raw[:README_CHARS].decode("utf-8", errors="replace")]))

        def resolve_once(handle: str) -> str | None:
            res.bluesky_requests += 1
            did: str | None = bsky.resolve_handle(handle)
            return did

        try:
            accounts, kinds, ambiguous = declared_account_sources(sources, resolve=resolve_once)
        except FetchError:
            return incomplete("resolve_failed")
        for kind in kinds:
            res.declared_in[kind] = res.declared_in.get(kind, 0) + 1
        if ambiguous:
            res.readme_ambiguous += 1
        extra = {"readme_ambiguous": ambiguous}
        if not accounts:
            return {"status": "no_declared_account", "posts": [], **extra}
        res.with_declared_account += 1
        if len(accounts) > BSKY_MAX_ACCOUNTS:
            res.accounts_over_cap += len(accounts) - BSKY_MAX_ACCOUNTS
            accounts = accounts[:BSKY_MAX_ACCOUNTS]
        urls = [("repo_url", f"https://github.com/{full}")]
        hp = homepage_search_url(homepage, full)
        if hp is not None:
            urls.append(("homepage_url", hp))
        found: dict[str, tuple[datetime, str]] = {}  # launch-worded posts up to the window's end
        seen: set[str] = set()
        unworded = 0
        for account in accounts:
            # a declared handle is resolved first: a handle that resolves to nothing (HTTP 400
            # "Unable to resolve handle") is a dead or mistyped link, skipped
            # and counted, never mistaken for an outage; the DID stays in memory
            author: str | None = account
            if not account.startswith("did:"):
                try:
                    author = bsky.resolve_handle(account)
                except FetchError:
                    return incomplete("resolve_failed")
                res.bluesky_requests += 1
            if author is None:
                res.accounts_unresolvable += 1
                continue
            for match, url in urls:
                cursor: str | None = None
                for page in range(BSKY_MAX_PAGES):
                    try:
                        f = bsky.search_posts(
                            author=author,
                            url=url,
                            since=start,
                            until=end,
                            cursor=cursor,
                            evidence_url=bsky_evidence_url(bsky.base, full, match, page),
                        )
                    except FetchError:
                        # any failed search (400 included) is incomplete, never "no posts": the
                        # AppView answers 200 for an unknown author (verifier M22 round 7)
                        return incomplete("search_failed")
                    res.bluesky_requests += 1
                    res.evidence_ids.append(f.evidence.id)
                    try:
                        hits, cursor = parse_search_page(f.data)
                    except PARSE_ERRORS:
                        return incomplete("search_failed")
                    finally:
                        drop(db, bsky.store, f.evidence.id, f.content_hash, dlog)
                    # no date filter in the request (the AppView refuses q=* with since/until):
                    # the window is applied here. Posts before it are read too (anchor-v8: a
                    # launch-worded one is a launch before the window); paging (newest first)
                    # goes on to the list's end (addendum 5): the earliest launch-worded post,
                    # not the first one met before the window, is what view B's per-kind count
                    # of launches before the window compares across sources. The search is
                    # filtered by author and URL, so the list is nearly always one page
                    for h in hits:
                        if h.at is None or h.at > end or not links_to(h.links, match, full, url):
                            continue
                        key = h.key or h.at.isoformat()
                        if key in seen:
                            continue
                        seen.add(key)
                        if bsky_post_is_launch(h.text):  # the text: in memory only
                            found[key] = (h.at, match)
                        elif h.at >= start:
                            unworded += 1
                    if not cursor:
                        break
                else:
                    return incomplete("search_capped")
        posts = sorted(
            ({"kind": BSKY_KIND, "time": at.isoformat(), "role": BSKY_ROLE, "match": m}
             for at, m in found.values()),
            key=lambda x: (x["time"], x["match"]),
        )  # fmt: skip
        res.launch_posts_before_window += sum(1 for at, _m in found.values() if at < start)
        res.posts_without_launch_wording += unworded
        return {"status": "complete", "posts": posts, "posts_without_launch_wording": unworded,
                **extra}  # fmt: skip

    incomplete_refs: list[str] = []
    for c in pending:
        body = one(c)
        store.replace_signals(
            c.ref, BSKY_SOURCE, [{"source": BSKY_SOURCE, "rule": ANCHOR_RULE_VERSION, **body}]
        )
        res.searched += 1
        res.posts += len(body["posts"])
        res.launch_posts += len(body["posts"])
        st = str(body["status"])
        res.status[st] = res.status.get(st, 0) + 1
        if st == "incomplete":
            incomplete_refs.append(c.ref)
            r = str(body.get("reason"))
            res.incomplete_reasons[r] = res.incomplete_reasons.get(r, 0) + 1
        else:
            done.add(c.ref)
        res.incomplete = len(incomplete_refs)
        checkpoint["bsky_done"] = sorted(done)
        save(checkpoint)
        if len(incomplete_refs) > BSKY_INCOMPLETE_MAX_SHARE * len(todo):
            raise LaunchSourceIncomplete(
                f"Bluesky: {len(incomplete_refs)} of {len(todo)} shortlisted repos have "
                f"incomplete data (more than {BSKY_INCOMPLETE_MAX_SHARE:.0%}; reasons: "
                + ", ".join(f"{k} {v}" for k, v in sorted(res.incomplete_reasons.items()))
                + "): the selection is refused so view B's anchors don't change silently. "
                "Run again later: repos done are kept, incomplete ones are retried (ADR-085)"
            )
    return res


def _gh_api() -> str:
    from pigtail.connectors.github import API

    return API


# --- the pre-registered parameters -----------------------------------------------------------
def launch_source_params(
    *, product_hunt: bool, bluesky: bool, ph_topics: Sequence[str]
) -> dict[str, Any]:
    """View B's launch sources as they go into `Context.params()` (ADR-085)."""
    from pigtail.briefs.ph_cache import cache_params
    from pigtail.connectors.bluesky import BSKY_QUERY, RESOLVE_PATH, SEARCH_PATH
    from pigtail.connectors.producthunt import POST_FIELDS, TOPIC_FIELDS

    return {
        "product_hunt": {
            "applies": product_hunt,
            "refusal": "the selection is refused (exit 8) when this applies and PH_API_TOKEN is "
            "missing (the connector is off without it)",
            "api": "Product Hunt API v2 GraphQL, official, the operator's developer token",
            "fields": list(POST_FIELDS),
            "never_requested": ["makers", "user", "comments", "votes"],
            "slug_rule": "at most 2 slug candidates: the GitHub name lowercased with _ and . "
            "turned into -, then the same without hyphens when that differs; post(slug: ...)",
            "topics": list(ph_topics),
            "topic_scan": "posts(topic, postedAfter, postedBefore, order NEWEST) over the gaps of "
            "the window the shared topic cache doesn't cover (topic_cache), every page (up to "
            f"{PH_TOPIC_MAX_PAGES} per calendar-month interval: a truncated month is never "
            f"reused and makes the topic incomplete), listing fields {', '.join(TOPIC_FIELDS)} "
            "only; the cached posts of the window matched by the SHA-256 of the normalized "
            "name (casefold, only a-z and 0-9 kept) equal to that of the repo name; a topic hit "
            "read again by post(id:)",
            "topic_cache": cache_params(),
            "incomplete": "a topic page that can't be parsed, a month cut at the page cap, or a "
            "planned scan row found missing while the index is read, leaves that topic "
            "incomplete: every repo not done yet is stored incomplete (topic_scan_failed | "
            "topic_scan_truncated | topic_plan_rows_missing), has no view-B anchor "
            "(launch_source_incomplete:product_hunt, counted) and is retried by the next run "
            "(the scan resumes from its stored cursor; a topic with missing rows is planned "
            "again)",
            "topic_hits": "computed once per run, by the first invocation that completes every "
            "topic's scan, for every shortlisted repo, and frozen in the run's checkpoint; a "
            "resume reuses them and never reads the cache's index again",
            "incomplete_max_share": PH_INCOMPLETE_MAX_SHARE,
            "incomplete_refusal": "more than 10 % of the shortlisted repos incomplete: the "
            "selection is refused (exit 9, resumable)",
            "product_slot": "the post's normalized name equals the repo's normalized GitHub name",
            "event_time": "featuredAt when set, else createdAt; a launch event inside the "
            "brief's window; a confirmed post before it is a launch before the window (posts "
            "of any date found by slug are stored; the topic scan is bounded to the window, so "
            "a launch before the window found only by the topic scan is missed)",
            "confirmation": ph_confirmation_params(),
            "stored": "post id, createdAt, featuredAt, votesCount, commentsCount, route, "
            "confirmed, confirmation (method or reason), Haiku provenance",
            "secondary_measures": "att.ph_votes and att.ph_comments: votes and comments (as of "
            "fetch) of the confirmed post whose launch time falls in the launch-size window, "
            "endpoint days 0..2 from the view-B anchor's first day (the launch-size day "
            "mapping: US Pacific endpoint days), the one with the most votes (earliest on a "
            "tie); none there: unknown (no_ph_post_in_launch_window); reported, never ranked "
            "on",
            "ambiguity": "a post confirmed for more than one shortlisted repo is dropped for all",
        },
        "bluesky": {
            "applies": bluesky,
            "refusal": "the selection is refused (exit 8) when this applies and the "
            "bluesky_search connector is off",
            "declared_accounts": "bsky.app/profile/<handle or DID> links and @<name>.bsky.social "
            "in, in order: the owner's GitHub profile social accounts (provider bluesky or a "
            "bsky.app URL; user owners), the org page's blog and description (org owners), the "
            "repo's homepage field, the README; at most "
            f"{BSKY_MAX_ACCOUNTS} accounts per repo; matched in memory, never stored or logged; "
            "never a search for people by name (ADR-075.3); declared handles resolved in memory "
            f"({RESOLVE_PATH}), the DID never stored",
            "readme_rule": "the profile, org page and homepage field always count; the README "
            f"counts only when it names exactly {README_MAX_ACCOUNTS} account (distinct after "
            "normalization: handles lowercased, and handles resolved in memory so a handle and "
            "its own DID are one account); with 2 or more it declares nothing and is counted "
            "readme_ambiguous",
            "search": f"GET {SEARCH_PATH} q={BSKY_QUERY!r} author=<declared account> url=<repo "
            "GitHub URL>, then url=<homepage URL> when there is one, sort=latest, limit=100, "
            "no since/until (the AppView refuses them with q=*; the window is applied in "
            "memory), every page until the list ends (to find the earliest launch-worded post, "
            f"before the window included), up to {BSKY_MAX_PAGES} pages each (more: "
            "incomplete); a post counts "
            "only when one of its links is that URL; no feed, profile or follower reads",
            "launch_wording": "a post is a launch event only when its text (record.text, "
            "whole) matches the release rule's pattern (view_b_anchor.release_launch_pattern, "
            "whole words, case-insensitive), read in memory, never stored; posts linking the "
            "repo without it are neither launch events nor relaunches, and are counted (a "
            "number per repo, in the window)",
            "event_time": "the API's sortAt: the earlier of the record's createdAt and indexedAt",
            "stored": "kind bluesky_maintainer_post, time, role maintainer, match (repo_url | "
            "homepage_url) per launch-worded post up to the window's end (before it included), "
            "the repo's status, the number of in-window posts without launch wording and "
            "whether the README was ambiguous; never a handle, DID, URI, text, or a post's "
            "like/repost/reply counts",
            "incomplete": "a failed or capped search, or unreadable declared-account sources: "
            "the repo is stored incomplete, has no view-B anchor (launch_source_incomplete:"
            "bluesky, counted) and is retried by the next run",
            "incomplete_max_share": BSKY_INCOMPLETE_MAX_SHARE,
            "incomplete_refusal": "more than 10 % of the shortlisted repos incomplete: the "
            "selection is refused (exit 9, resumable)",
        },
    }
