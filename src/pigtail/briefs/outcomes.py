"""Outcome inputs of a final shortlist for the selection stage (PRD R3.1–R3.4, R4.3, R4.8,
R18.8; outcome-model v2.1 §1–§4, §5.6, §7; ADR-032.3, ADR-070, ADR-077, ADR-081).

Two steps, both project-level (repo names and ids, daily star counts, HN item ids, times and
points; no identities):

1. **`fetch_outcome_data`** (network: HN Algolia, then GitHub). First the **launch lookup**
   (`lookup_launches`, ADR-081): for every shortlisted repo, three HN Algolia searches inside the
   brief's window (Show HN by the repo URL and by the repo name, Launch HN by name) find its
   declared launches whether discovery found them or not; a hit is kept when its URL is the
   repo's `github.com/owner/name` (case-insensitive) or, linking no other GitHub repo, its title
   names the repo as a whole word (`match_launch_post`); only item id, time, points, kind and
   match are stored (as candidate signals), the raw page is dropped at parse (CB-24), and the
   step is checkpointed per repo. Then, for every repo on the final shortlist, fill
   missing project metadata (GitHub id, creation date, language; one GraphQL query per 50 repos,
   for repos added by URL in the review) and fetch its **star history**
   (`pigtail.capture.star_history`, ETag-conditional, 30 weeks per page, back to 60 days before
   the brief's window or the repo's creation week). Progress is checkpointed per repo; the GitHub
   request budget pauses the stage (resumable), and a repo the API can't serve (deleted,
   renamed) is recorded and left `unknown`. The refusal list (CB-13) is checked again before
   any fetch, and once more with the GitHub id the metadata query returns: a repo refused by id
   only that was added by URL is removed from the brief version (`Shortlist.forget`), its
   metadata is not stored and its star history is never fetched (M22 verifier round 2).

2. **`load_inputs`** (database only): one `selection.CaseInput` per shortlisted repo.
   - **Anchor T** (§2.2, `choose_anchor`, rule `anchor-v2`): the declared launches (Show HN and
     Launch HN posts from discovery and the lookup, one per item id; hour precision) and the
     first `velocity-v0` burst on the star-history days inside the brief's window: a launch in
     `[T_burst − 30 d, T_burst]` wins; a launch with no burst in the 90 days after it (and before
     the first burst) wins; else the burst; else the launch; else no anchor. Against a
     day-precision onset the comparison is on endpoint days, so a launch on the onset day
     precedes the burst (ADR-081). Title matches that two shortlisted repos share, or that
     another repo's post links by URL, are dropped. Without any star history the burst rule
     can't be checked and a launch is used with that note.
   - **Values** (§1, §7): `att.stars@30/@90` = raw net stars over the `k` endpoint days from the
     first day (§1.2 day mapping), `pending` until `T + k + settle_lag (3 d)` has passed,
     `unknown` when a day is missing, labelled "unfiltered, anomaly-checked";
     `att.hn_points` = the highest points of a declared launch post from `T − 7 d` on (as of
     fetch). **Every other metric is `unknown` with reason `no_connector`**: registry downloads,
     dependents, PR-based community metrics and the business signals have no connector yet
     (outcome-model §7), and nothing is imputed (R18.8).
   - **Covariates** (§5.6): LSM from the first 2 endpoint days, launch quarter and half-year of
     T (UTC), repo age at T from the creation date, primary language (current, not at T), launch
     type (`show_hn` or `burst`), and the founder audience band, which is `unknown` for every
     candidate until the audience proxy is built and cleared (O14; `unknown` is matched as its
     own level).
   - **Star anomaly flag** (§4.3): `pigtail.analysis.anomaly.check_population` over the
     anchored field and reference candidates, on the endpoint days `[T − 60 d, T + k_max)` with
     the `forks` channel where per-repo event counts exist (tracked projects); issues, downloads
     and mentions have no daily source for retrospective candidates, so many spikes are
     `unchecked` and the flag `unknown`, which is reported, never read as clean.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import psycopg

from pigtail.analysis.anomaly import check_population
from pigtail.analysis.bursts import Onset, segment
from pigtail.analysis.params import ANOMALY, STAR_HISTORY_DAY_TZ
from pigtail.briefs.candidates import Candidate, CandidateStore
from pigtail.briefs.model import METRICS, Brief
from pigtail.briefs.selection import (
    BUSINESS_COUNT,
    BUSINESS_SIGNALS,
    Anchor,
    AnomalyFlag,
    CaseInput,
    Covariates,
    Definition,
    SelectionError,
    Value,
)
from pigtail.briefs.shortlist import Shortlist

SETTLE_LAG_DAYS = 3  # outcome-model §1.1: a fixed measurement default for every source
BASELINE_BEFORE_DAYS = 60  # anomaly input window starts at T - 60 d (§4.2)
STAR_WEEKS_PER_PAGE = 30
DAY = timedelta(days=1)
STAR_LABEL = ANOMALY.label
NO_CONNECTOR = Value("unknown", reason="no_connector")


def shortlisted(conn: psycopg.Connection[Any], brief: Brief) -> list[Candidate]:
    """The repos on the brief version's **final** shortlist (R4.7), refused repos left out."""
    sl = Shortlist(conn, brief)
    st = sl.status()
    if st is None or st["status"] != "final":
        raise SelectionError(
            f"the shortlist of {brief.brief_id} v{brief.version} is not final: finalize it first "
            f"(pigtail brief shortlist finalize {brief.brief_id})"
        )
    dec = sl.latest()
    return [
        c
        for c in sl.candidates.all()
        if c.repo_full_name is not None
        and not sl.refused(c.repo_full_name, c.repo_host_id)
        and sl.included(c, dec.get(c.ref)) is True
    ]


# --- 1. fetch ----------------------------------------------------------------------------------
@dataclass
class FetchResult:
    repos: int = 0
    fetched: int = 0
    already_done: int = 0
    metadata_filled: int = 0
    failed: dict[str, int] = field(default_factory=dict)  # reason -> count (no names)
    pages: int = 0
    launch_lookup: dict[str, Any] | None = None
    evidence_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d.pop("evidence_ids")
        return d


def star_pages(window_start: date, as_of: date) -> int:
    """Pages of 30 weeks that reach 60 days before the window's start (the anomaly baseline)."""
    weeks = (as_of - (window_start - timedelta(days=BASELINE_BEFORE_DAYS + 1))).days // 7 + 2
    return max(1, min(100, math.ceil(weeks / STAR_WEEKS_PER_PAGE)))


def fetch_outcome_data(
    conn: psycopg.Connection[Any],
    brief: Brief,
    github: Any,
    cands: Sequence[Candidate],
    *,
    window_start: date,
    as_of: date,
    checkpoint: dict[str, Any],
    save: Callable[[dict[str, Any]], None],
    brief_run_id: str | None,
    now: datetime,
    recorder: Any = None,
    hn: Any = None,
    window: tuple[datetime, datetime] | None = None,
) -> FetchResult:
    """Step 1 (module docstring). `BudgetExhausted` propagates: the stage pauses, resumable.
    With `hn` and `window`, the launch lookup (step 1b) runs first, checkpointed per repo."""
    from pigtail.briefs.discovery import _meta_from_graphql
    from pigtail.capture.db import CaptureDB
    from pigtail.capture.star_history import fetch_star_history
    from pigtail.connectors.base import FetchError

    assert brief.version is not None
    sl = Shortlist(conn, brief)
    refused: set[str] = set()

    def refuse(ref: str) -> None:  # CB-13: out of the brief version, nothing fetched
        refused.add(ref)
        sl.forget(ref)

    for cand in cands:  # the refusal list may have grown since finalize
        if cand.repo_full_name is not None and sl.refused(cand.repo_full_name, cand.repo_host_id):
            refuse(cand.ref)
    cands = [c for c in cands if c.ref not in refused]
    res = FetchResult(repos=len(cands))
    if refused:
        res.failed["refused"] = len(refused)
    if window is not None:
        lk = lookup_launches(
            conn,
            brief,
            hn,
            cands,
            window=window,
            checkpoint=checkpoint,
            save=save,
            recorder=recorder,
        )
        res.launch_lookup = lk.to_dict()
        res.evidence_ids.extend(lk.evidence_ids)
    if github is None or not getattr(github, "enabled", True):
        res.failed["no_github_connector"] = len(cands)
        return res
    db = CaptureDB(conn)
    store = CandidateStore(conn, brief.brief_id, brief.version)
    done: set[str] = set(checkpoint.get("star_history_done") or [])
    failed: dict[str, str] = dict(checkpoint.get("star_history_failed") or {})
    need = sorted(
        c.repo_full_name
        for c in cands
        if c.repo_full_name
        and c.ref not in done
        and (c.repo_host_id is None or not c.metadata.get("created_at"))
    )
    if need and not checkpoint.get("metadata_done"):
        meta = github.repos_metadata(need)
        for name in need:
            m = meta.get(name)
            if m is None:
                continue
            if sl.refused(name, m.host_id):  # refused by id: learnt only now
                refuse(f"gh:{name}")
                res.failed["refused"] = res.failed.get("refused", 0) + 1
                continue
            store.upsert(
                Candidate(
                    ref=f"gh:{name}",
                    repo_full_name=name,
                    repo_host_id=m.host_id,
                    metadata=_meta_from_graphql(m),
                ),
                brief_run_id=brief_run_id,
                now=now,
            )
            res.metadata_filled += 1
        checkpoint["metadata_done"] = True
        save(checkpoint)
    fresh = {c.ref: c for c in store.all()}
    pages = star_pages(window_start, as_of)
    for ref in sorted(c.ref for c in cands):
        if ref in refused:
            continue
        if ref in done:
            res.already_done += 1
            continue
        c = fresh.get(ref)
        if c is not None and c.repo_full_name and sl.refused(c.repo_full_name, c.repo_host_id):
            refuse(ref)
            res.failed["refused"] = res.failed.get("refused", 0) + 1
            continue
        if c is None or c.repo_host_id is None or c.repo_full_name is None:
            failed[ref] = "no_repo_id"
        else:
            try:
                r = fetch_star_history(
                    github,
                    db,
                    c.repo_host_id,
                    c.repo_full_name,
                    per_page=STAR_WEEKS_PER_PAGE,
                    max_pages=pages,
                    run=recorder,
                )
            except FetchError as e:
                failed[ref] = type(e).__name__
            else:
                res.fetched += 1
                res.pages += r.pages
                res.evidence_ids.extend(r.evidence_ids)
                failed.pop(ref, None)
        done.add(ref)
        checkpoint["star_history_done"] = sorted(done)
        checkpoint["star_history_failed"] = dict(sorted(failed.items()))
        save(checkpoint)
    for reason in failed.values():
        res.failed[reason] = res.failed.get(reason, 0) + 1
    return res


# --- 1b. launch lookup (outcome-model §2.1, ADR-081) ------------------------------------------
LAUNCH_LOOKUP_SOURCE = "hn_launch_lookup"
LAUNCH_LOOKUP_REQUESTS = 3  # HN Algolia requests per shortlisted repo (estimate, ADR-081)
LAUNCH_LOOKUP_HITS = 50
TITLE_MIN_CHARS = 4  # shorter repo names are matched by URL only
_LAUNCH_HN_TITLE = re.compile(r"^\s*launch hn\b", re.IGNORECASE)


def lookup_queries(full_name: str) -> list[tuple[str, str, str]]:
    """(label, query, tags) of the launch lookup for one repo: the repo URL and the repo name
    among Show HN stories, and the name among all stories for Launch HN (Algolia has no
    Launch HN tag). The queries hold nothing but the repo's own name."""
    owner, name = full_name.split("/", 1)
    return [
        ("url", f"github.com/{owner}/{name}", "show_hn"),
        ("name", name, "show_hn"),
        ("launch_hn", f"Launch HN {name}", "story"),
    ]


def title_names_repo(title: str | None, full_name: str) -> bool:
    """The title names the repo's name as a whole word, case-insensitively (ADR-081): the name
    is neither preceded nor followed by a letter, digit, `_` or `-`, nor followed by `.` and a
    letter or digit (so `KubeForge.` at a sentence end matches, `kubeforge.io` and
    `kubeforge-ui` don't). Names shorter than `TITLE_MIN_CHARS` never match by title."""
    name = full_name.split("/", 1)[-1]
    if not title or len(name) < TITLE_MIN_CHARS:
        return False
    pat = r"(?<![A-Za-z0-9_.-])" + re.escape(name) + r"(?![A-Za-z0-9_-]|\.[A-Za-z0-9])"
    return re.search(pat, title, re.IGNORECASE) is not None


def match_launch_post(story: Any, full_name: str, kind: str) -> str | None:
    """`url`, `title` or None for one lookup hit (ADR-081). URL: the story links the repo's
    `github.com/owner/name` (normalised, so case, `www.`, `.git` and deeper paths don't matter;
    the A2 rule). Title: the story links no other GitHub repo and `title_names_repo`. A Launch HN
    hit must have a title starting "Launch HN"."""
    if kind == "launch_hn" and not _LAUNCH_HN_TITLE.match(story.title or ""):
        return None
    linked = story.repo_full_name
    if linked is not None and linked == full_name.lower():
        return "url"
    if linked is not None:
        return None
    return "title" if title_names_repo(story.title, full_name) else None


@dataclass
class LookupResult:
    repos: int = 0
    looked_up: int = 0
    already_done: int = 0
    requests: int = 0
    posts: int = 0
    by_match: dict[str, int] = field(default_factory=dict)
    failed: dict[str, int] = field(default_factory=dict)
    skipped: str | None = None
    evidence_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d.pop("evidence_ids")
        return d


def lookup_launches(
    conn: psycopg.Connection[Any],
    brief: Brief,
    hn: Any,
    cands: Sequence[Candidate],
    *,
    window: tuple[datetime, datetime],
    checkpoint: dict[str, Any],
    save: Callable[[dict[str, Any]], None],
    recorder: Any = None,
) -> LookupResult:
    """Step 1b (ADR-081): find each shortlisted repo's Show HN / Launch HN posts in the window,
    whether discovery found them or not. Project-level fields only (item id, time, points, kind,
    match): `parse_show_hn_page` never reads the author, and the raw page is dropped right after
    parsing (CB-24). The evidence record names the repo, not the query. The connector's rate
    limiter paces the requests. Checkpointed per repo (`launch_lookup_done`); a failed request
    propagates and the stage resumes from the next repo not done."""
    from pigtail.capture.db import CaptureDB
    from pigtail.connectors.hn import launch_lookup_evidence_url, parse_show_hn_page
    from pigtail.privacy.deletion import PARSE_ERRORS, DeletionLog, drop_after_parse

    assert brief.version is not None
    todo = sorted((c for c in cands if c.repo_full_name), key=lambda c: c.ref)
    res = LookupResult(repos=len(todo))
    if hn is None or not getattr(hn, "enabled", True):
        res.skipped = "hn connector disabled or not configured"
        return res
    db = CaptureDB(conn)
    store = CandidateStore(conn, brief.brief_id, brief.version)
    dlog = DeletionLog(db, "retention", run_id=getattr(recorder, "id", None))
    done: set[str] = set(checkpoint.get("launch_lookup_done") or [])
    start, end = window
    for c in todo:
        if c.ref in done:
            res.already_done += 1
            continue
        full = str(c.repo_full_name)
        found: dict[int, dict[str, Any]] = {}
        for label, query, tags in lookup_queries(full):
            f = hn.search_show_hn(
                query,
                since=start,
                until=end,
                hits=LAUNCH_LOOKUP_HITS,
                tags=tags,
                evidence_url=launch_lookup_evidence_url(full, label, tags),
            )
            res.requests += 1
            res.evidence_ids.append(f.evidence.id)
            try:
                stories, _ = parse_show_hn_page(f.data)
            except PARSE_ERRORS:
                stories = []
                res.failed["parse_failed"] = res.failed.get("parse_failed", 0) + 1
            drop_after_parse(db, hn.store, f.evidence.id, f.content_hash, dlog)
            kind = "launch_hn" if label == "launch_hn" else "show_hn"
            for st in stories:
                if st.created_at is None or not start <= st.created_at <= end:
                    continue
                how = match_launch_post(st, full, kind)
                if how is None:
                    continue
                prev = found.get(st.item_id)
                if prev is not None and (prev["match"] == "url" or how == "title"):
                    continue
                found[st.item_id] = {
                    "source": LAUNCH_LOOKUP_SOURCE,
                    "hn_item_id": st.item_id,
                    "time": st.created_at.isoformat(),
                    "points": st.points,
                    "kind": kind,
                    "match": how,
                }
        if found:
            store.add_sources(c.ref, [found[k] for k in sorted(found)])
            for rec in found.values():
                res.by_match[rec["match"]] = res.by_match.get(rec["match"], 0) + 1
            res.posts += len(found)
        res.looked_up += 1
        done.add(c.ref)
        checkpoint["launch_lookup_done"] = sorted(done)
        save(checkpoint)
    return res


# --- 2. load -----------------------------------------------------------------------------------
def endpoint_day(t: datetime, tz: str = STAR_HISTORY_DAY_TZ) -> date:
    """D(t): the star-history endpoint day containing the instant t (outcome-model §1.2)."""
    return t.astimezone(ZoneInfo(tz)).date()


def first_day(anchor: Anchor) -> date:
    """First endpoint day of `[T, T+k)`: D(T) for hour precision; T's UTC date for day
    precision (a dated launch without a time); the onset day for a burst."""
    if anchor.precision == "day" and anchor.type == "launch":
        return anchor.at.astimezone(UTC).date()
    return endpoint_day(anchor.at)


def star_series(conn: psycopg.Connection[Any], host_id: int, as_of: date) -> dict[date, int]:
    """Ended endpoint days of `repo_star_daily` up to `as_of` (partial days left out)."""
    rows = conn.execute(
        "SELECT day, stars_net FROM repo_star_daily WHERE repo_host_id = %s AND day <= %s"
        " AND NOT is_partial ORDER BY day",
        (host_id, as_of),
    ).fetchall()
    return {r[0]: int(r[1]) for r in rows}


def fork_series(conn: psycopg.Connection[Any], host_id: int) -> dict[date, int]:
    rows = conn.execute(
        "SELECT day, forks_seen FROM repo_event_daily_agg WHERE repo_host_id = %s ORDER BY day",
        (host_id,),
    ).fetchall()
    return {r[0]: int(r[1]) for r in rows}


@dataclass(frozen=True)
class Launch:
    """One declared launch (outcome-model §2.1) of a candidate: a Show HN or Launch HN post."""

    at: datetime
    item_id: int
    points: int | None
    source: str  # show_hn | launch_hn
    via: str  # discovery | lookup:url | lookup:title


def _t(raw: Any) -> datetime:
    t = datetime.fromisoformat(str(raw))
    return t if t.tzinfo is not None else t.replace(tzinfo=UTC)


def ambiguous_title_matches(cands: Sequence[Candidate]) -> set[tuple[str, int]]:
    """(candidate ref, item id) of lookup title matches to drop (ADR-081): an item matched by
    title to more than one shortlisted repo, or linked by URL to another one."""
    title: dict[int, set[str]] = {}
    url: dict[int, set[str]] = {}
    for c in cands:
        for s in c.sources:
            if s.get("source") == "show_hn" and s.get("hn_item_id"):
                url.setdefault(int(s["hn_item_id"]), set()).add(c.ref)
            elif s.get("source") == LAUNCH_LOOKUP_SOURCE and s.get("hn_item_id"):
                d = title if s.get("match") == "title" else url
                d.setdefault(int(s["hn_item_id"]), set()).add(c.ref)
    out: set[tuple[str, int]] = set()
    for item, refs in title.items():
        if len(refs) > 1 or url.get(item, set()) - refs:
            out |= {(r, item) for r in refs}
    return out


def _launches(
    c: Candidate,
    start: datetime,
    end: datetime,
    drop: set[tuple[str, int]] | frozenset[tuple[str, int]] = frozenset(),
) -> list[Launch]:
    """The candidate's declared launches inside the window (outcome-model §2.1, ADR-081): the
    Show HN posts discovery recorded (linked by URL) merged with the launch lookup's posts, one
    per HN item id (the lookup's record wins: fresher points, and it says how it matched). In
    time order, then item id (§2.2 rule 5)."""
    by_item: dict[int, Launch] = {}
    for s in c.sources:
        src = s.get("source")
        if src not in ("show_hn", LAUNCH_LOOKUP_SOURCE) or not s.get("time"):
            continue
        item = int(s.get("hn_item_id") or 0)
        if src == LAUNCH_LOOKUP_SOURCE and (c.ref, item) in drop:
            continue
        t = _t(s["time"])
        if not start <= t <= end:
            continue
        pts = s.get("points")
        rec = Launch(
            at=t,
            item_id=item,
            points=int(pts) if isinstance(pts, int) else None,
            source="launch_hn" if s.get("kind") == "launch_hn" else "show_hn",
            via=f"lookup:{s.get('match')}" if src == LAUNCH_LOOKUP_SOURCE else "discovery",
        )
        prev = by_item.get(item)
        if prev is None or (prev.via == "discovery" and rec.via != "discovery"):
            by_item[item] = rec
    return sorted(by_item.values(), key=lambda x: (x.at, x.item_id))


def _precedes(t: datetime, b: Onset) -> bool:
    """The launch instant `t` is at or before the burst onset (§2.2 rules 1 and 5). A
    day-precision onset is compared at day precision: a launch on the onset's endpoint day
    (§1.2 day mapping) counts as preceding it (ADR-081)."""
    if b.precision == "day":
        return endpoint_day(t) <= b.day
    return t <= b.at


def _within_30d_before(t: datetime, b: Onset) -> bool:
    if b.precision == "day":
        return endpoint_day(t) >= b.day - timedelta(days=30)
    return t >= b.at - timedelta(days=30)


def _burst_within_90d_after(t: datetime, b: Onset) -> bool:
    if b.precision == "day":
        return endpoint_day(t) <= b.day < endpoint_day(t) + timedelta(days=90)
    return t <= b.at < t + timedelta(days=90)


def choose_anchor(
    launches: Sequence[Launch], bursts: Sequence[Onset], has_series: bool
) -> tuple[Anchor | None, str | None]:
    """outcome-model §2.2 as read by ADR-077.3, amended by ADR-081 (`ANCHOR_RULE_VERSION`):
    1. a launch in `[T_burst - 30 d, T_burst]` (the earliest) -> launch; 2./3. else the first
    launch when it precedes the first burst and no burst follows within 90 days -> launch; else
    the burst; else the launch; else no anchor. Against a day-precision onset every comparison is
    on endpoint days, so a same-day launch precedes the burst (rule 5). `launches` in time order,
    `bursts` in onset order."""

    def anchor(x: Launch) -> Anchor:
        return Anchor("launch", x.at, "hour", x.source, x.via)

    b0 = bursts[0] if bursts else None
    if b0 is not None:
        near = [x for x in launches if _within_30d_before(x.at, b0) and _precedes(x.at, b0)]
        if near:
            return anchor(near[0]), None
    if launches:
        first = launches[0]
        later = any(_burst_within_90d_after(first.at, b) for b in bursts)
        if b0 is None or (_precedes(first.at, b0) and not later):
            note = None if has_series else "no_star_history: burst rule not checked"
            return anchor(first), note
    if b0 is not None:
        return Anchor("burst", b0.at, b0.precision, "velocity-v0"), None
    return None, "no_anchor" if has_series else "no_anchor: no launch post and no star history"


def star_value(series: Mapping[date, int], first: date, k: int, as_of: date) -> Value:
    """`att.stars@k` (outcome-model §1.2): raw net stars over k endpoint days."""
    if as_of < first + timedelta(days=k + SETTLE_LAG_DAYS):
        return Value("pending", reason="horizon_not_reached", tag="unknown")
    days = [first + timedelta(days=i) for i in range(k)]
    if not series:
        return Value("unknown", reason="no_star_history")
    if any(d not in series for d in days):
        return Value("unknown", reason="incomplete_series")
    return Value("observed", float(sum(series[d] for d in days)), "verified", STAR_LABEL)


def _star_horizon_max(d: Definition) -> int:
    dims = {d.primary, *d.floors, *(d.weights or {})}
    ks = [int(d.metrics[x].split("@")[1]) for x in dims if d.metrics[x].startswith("att.stars@")]
    return max(ks, default=30)


def _half_year(t: datetime) -> str:
    u = t.astimezone(UTC)
    return f"{u.year}H{1 if u.month <= 6 else 2}"


def _quarter(t: datetime) -> int:
    u = t.astimezone(UTC)
    return u.year * 4 + (u.month - 1) // 3


def _created(c: Candidate) -> date | None:
    raw = c.metadata.get("created_at")
    if not raw:
        return None
    t = datetime.fromisoformat(str(raw))
    return (t if t.tzinfo else t.replace(tzinfo=UTC)).astimezone(UTC).date()


def load_inputs(
    conn: psycopg.Connection[Any],
    brief: Brief,
    cands: Sequence[Candidate],
    *,
    window: tuple[datetime, datetime],
    as_of: date,
) -> list[CaseInput]:
    """Step 2 (module docstring)."""
    definition = Definition.from_brief(brief)
    k_max = _star_horizon_max(definition)
    start, end = window
    prepared: list[dict[str, Any]] = []
    drop = ambiguous_title_matches(cands)
    for c in sorted(cands, key=lambda x: x.ref):
        series = star_series(conn, c.repo_host_id, as_of) if c.repo_host_id is not None else {}
        created = _created(c)
        launches = _launches(c, start, end, drop)
        bursts: list[Onset] = []
        if series:
            lo = max(start.date(), min(series))
            hi = min(end.date(), as_of)
            if lo <= hi:
                seg = segment(series, lo, hi, created=created)
                bursts = [b.onset for b in seg.bursts]
        anchor, reason = choose_anchor(launches, bursts, bool(series))
        prepared.append(
            {
                "c": c,
                "series": series,
                "created": created,
                "launches": launches,
                "anchor": anchor,
                "reason": reason,
            }
        )

    # anomaly checks over the anchored field and reference candidates (§4.2)
    anomaly_in: dict[str, tuple[dict[date, int], dict[str, dict[date, int]]]] = {}
    windows: dict[str, tuple[date, date]] = {}
    for p in prepared:
        c, a = p["c"], p["anchor"]
        if a is None or c.panel == "exemplar" or not p["series"]:
            continue
        f = first_day(a)
        w0, w1 = f - timedelta(days=BASELINE_BEFORE_DAYS), f + timedelta(days=k_max - 1)
        days = [w0 + timedelta(days=i) for i in range((w1 - w0).days + 1)]
        if any(d not in p["series"] for d in days):
            continue
        stars = {d: p["series"][d] for d in days}
        forks = fork_series(conn, c.repo_host_id) if c.repo_host_id is not None else {}
        act = {"forks": {d: v for d, v in forks.items() if w0 <= d <= w1}}
        anomaly_in[c.ref] = (stars, {k: v for k, v in act.items() if v})
        windows[c.ref] = (w0, w1)
    reports = check_population(anomaly_in)

    out: list[CaseInput] = []
    for p in prepared:
        c, a, series = p["c"], p["anchor"], p["series"]
        values: dict[str, Value] = {}
        business = dict.fromkeys(BUSINESS_SIGNALS, NO_CONNECTOR)
        cov = Covariates(language=c.metadata.get("language"))
        flag: AnomalyFlag = "unknown"
        anomaly: dict[str, Any] = {"label": STAR_LABEL, "status": "not_checked"}
        if a is None:
            no = Value("unknown", reason="no_anchor")
            values = {m: no for dim in METRICS for m in METRICS[dim]}
        else:
            f = first_day(a)
            for m in (*METRICS["attention"], *METRICS["adoption"], *METRICS["community"]):
                values[m] = NO_CONNECTOR
            values[BUSINESS_COUNT] = NO_CONNECTOR
            for k in (30, 90):
                values[f"att.stars@{k}"] = star_value(series, f, k, as_of)
            pts = [
                x.points
                for x in p["launches"]
                if x.points is not None and x.at >= a.at - timedelta(days=7)
            ]
            values["att.hn_points"] = (
                Value("observed", float(max(pts)), "verified", "as of fetch")
                if pts
                else Value("unknown", reason="no_matched_story_captured")
            )
            lsm = None
            if f in series and f + DAY in series:
                lsm = math.log10(1 + max(0, series[f] + series[f + DAY]))
            age = None
            if p["created"] is not None:
                age = math.log10(max(1, (a.at.astimezone(UTC).date() - p["created"]).days))
            cov = Covariates(
                lsm=lsm,
                launch_quarter=_quarter(a.at),
                launch_half_year=_half_year(a.at),
                age_log10=age,
                audience_band="unknown",
                language=c.metadata.get("language"),
                launch_type=a.source if a.type == "launch" else "burst",
            )
            rep = reports.get(c.ref)
            if rep is not None:
                w0, w1 = windows[c.ref]
                case_from = f - timedelta(days=30)
                spikes = [s for s in rep.spikes if s.end >= case_from and s.start <= w1]
                flags = [
                    fl
                    for fl in rep.flags
                    if fl.kind == "ratio_outlier"
                    or (fl.end is not None and fl.start is not None and fl.end >= case_from)
                ]
                checked = all(s.checked for s in spikes)
                ratio_ok = (
                    rep.ratio is not None and rep.stars_total >= ANOMALY.ratio_min_stars
                ) or rep.stars_total < ANOMALY.ratio_min_stars
                flag = "true" if flags else ("false" if checked and ratio_ok else "unknown")
                anomaly = {
                    "label": STAR_LABEL,
                    "rule_version": rep.rule_version,
                    "params_version": rep.params_version,
                    "status": rep.status,
                    "flags": [
                        {"kind": fl.kind, "start": _iso(fl.start), "end": _iso(fl.end)}
                        for fl in flags
                    ],
                    "spikes_in_window": len(spikes),
                    "ratio": None if rep.ratio is None else round(rep.ratio, 4),
                    "stars_total": rep.stars_total,
                    "window": [w0.isoformat(), w1.isoformat()],
                    "channels": sorted(anomaly_in[c.ref][1]),
                }
            elif c.panel != "exemplar":
                anomaly = {"label": STAR_LABEL, "status": "no_star_history_for_window"}
        out.append(
            CaseInput(
                ref=c.ref,
                panel=c.panel,
                distance=c.distance if c.distance is not None else 0,
                named_index=c.named_index,
                anchor=a,
                anchor_reason=p["reason"],
                values=values,
                business=business,
                covariates=cov,
                star_anomaly_flag=flag,
                anomaly=anomaly,
            )
        )
    return out


def _iso(d: date | None) -> str | None:
    return None if d is None else d.isoformat()
