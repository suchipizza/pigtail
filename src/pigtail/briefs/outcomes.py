"""Outcome inputs of a final shortlist for the selection stage (PRD R3.1–R3.4, R4.3, R4.8,
R18.8; outcome-model v2.1 §1–§4, §5.6, §7; ADR-032.3, ADR-070, ADR-077, ADR-081, ADR-082).

Two steps, both project-level (repo names and ids, daily star counts, HN item ids, times and
points; no identities):

1. **`fetch_outcome_data`** (network: GitHub, then HN Algolia, then GitHub). It refuses to
   start without the Show HN connector (`LaunchLookupUnavailable`, ADR-082): the pre-registered
   selection rule includes the launch lookup. First, for repos added by URL in the review, fill
   missing project metadata (GitHub id, creation date, language; one GraphQL query per 50
   repos). Then the **launch lookup** (`lookup_launches`, ADR-081 as amended by ADR-082): for
   every shortlisted repo, three HN Algolia searches inside the brief's window (Show HN by the
   repo URL and by the repo name, `tags=show_hn`; Launch HN by the name, `tags=launch_hn`) find
   its declared launches whether discovery found them or not. A hit is kept when its URL is the
   repo's `github.com/owner/name` (case-insensitive), or by title only when the title is
   `Show HN: <name>` / `Launch HN: <name>` followed by its end or a separator, the post links
   no GitHub repo, was posted no earlier than a day before the repo's creation, and the name
   has 5+ characters and is not a common or generic word (`classify_launch_post`). Only item
   id, time, points, kind, match and rule are stored (as candidate signals, replacing a repo's
   earlier lookup records); rejected title-only candidates are counted by reason, never
   stored; the raw page is dropped at parse (CB-24); the step is checkpointed per repo. Then,
   for every repo on the final shortlist, fetch its **star history**
   (`pigtail.capture.star_history`, ETag-conditional, 30 weeks per page, back to 60 days before
   the brief's window or the repo's creation week). Progress is checkpointed per repo; the GitHub
   request budget pauses the stage (resumable), and a repo the API can't serve (deleted,
   renamed) is recorded and left `unknown`. The refusal list (CB-13) is checked again before
   any fetch, and once more with the GitHub id the metadata query returns: a repo refused by id
   only that was added by URL is removed from the brief version (`Shortlist.forget`), its
   metadata is not stored and its star history is never fetched (M22 verifier round 2).

2. **`load_inputs`** (database only): one `selection.CaseInput` per shortlisted repo.
   - **Anchor T** (§2.2, `choose_anchor`, rule `anchor-v3`): the declared launches (Show HN and
     Launch HN posts from discovery and the lookup under the current rule, one per item id;
     hour precision) and the first `velocity-v0` burst on the star-history days inside the
     brief's window: a launch in
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
     T (UTC), repo age at T from the creation date, primary language (current, not at T),
     launch type (`show_hn`, `launch_hn` or `burst`), and the founder audience band, which is
     `unknown` for every candidate until the audience proxy is built and cleared (O14;
     `unknown` is matched as its own level).
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
    ANCHOR_RULE_VERSION,
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
    With `window`, the launch lookup (step 1b) runs after the metadata fill and before the star
    history, checkpointed per repo; it needs `hn` (`LaunchLookupUnavailable` otherwise, raised
    before anything is written; ADR-082)."""
    from pigtail.briefs.discovery import _meta_from_graphql
    from pigtail.capture.db import CaptureDB
    from pigtail.capture.star_history import fetch_star_history
    from pigtail.connectors.base import FetchError

    assert brief.version is not None
    if window is not None and (why := launch_lookup_blocked(hn)) is not None:
        raise LaunchLookupUnavailable(why)  # ADR-082: never a selection without the lookup
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
    gh_on = github is not None and getattr(github, "enabled", True)
    store = CandidateStore(conn, brief.brief_id, brief.version)
    done: set[str] = set(checkpoint.get("star_history_done") or [])
    failed: dict[str, str] = dict(checkpoint.get("star_history_failed") or {})
    # metadata first: the launch lookup's title rule needs each repo's creation date (ADR-082)
    need = sorted(
        c.repo_full_name
        for c in cands
        if c.repo_full_name
        and c.ref not in done
        and (c.repo_host_id is None or not c.metadata.get("created_at"))
    )
    if gh_on and need and not checkpoint.get("metadata_done"):
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
    if window is not None:
        latest = {c.ref: c for c in store.all()}
        lk = lookup_launches(
            conn,
            brief,
            hn,
            [latest.get(c.ref, c) for c in cands if c.ref not in refused],
            window=window,
            checkpoint=checkpoint,
            save=save,
            recorder=recorder,
        )
        res.launch_lookup = lk.to_dict()
        res.evidence_ids.extend(lk.evidence_ids)
    if not gh_on:
        res.failed["no_github_connector"] = len(cands)
        return res
    db = CaptureDB(conn)
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


# --- 1b. launch lookup (outcome-model §2.1, ADR-081, ADR-082) --------------------------------
LAUNCH_LOOKUP_SOURCE = "hn_launch_lookup"
LAUNCH_LOOKUP_REQUESTS = 3  # HN Algolia requests per shortlisted repo (estimate, ADR-082)
LAUNCH_LOOKUP_HITS = 50
TITLE_MIN_CHARS = 5  # shorter repo names are matched by URL only (ADR-082 rule d)
CREATION_TOLERANCE = timedelta(days=1)  # a post may precede the repo's creation by <= 1 day
# ADR-082 rule (d): repo names that are common English words or generic tech words never match
# by title, whatever the title says ("Show HN: Studio – …" is rarely `someone/studio`). A name
# is refused when every part of it (split on `-`, `_`, spaces) is on this list. The list is
# small on purpose and necessarily incomplete: a dictionary word not on it can still match by
# title when rules (a)-(c) hold, which is why title-matched anchors are labelled
# `lookup:title` for review. The verifier's round-4 false matches are on it (studio, poly, toml,
# pick, microwave, augur, confetti, json). Changing the list changes the anchor rule: bump
# `ANCHOR_RULE_VERSION` (the guard test `test_anchor_rule_source_is_pinned` fails until then).
_STOPLIST_WORDS = """
    about access action active agent alpha amber anchor apex api app apps arena argus arrow
    atlas atom augur aura auth autumn awesome babel badge base basic batch beacon beam bench
    beta binary bits blank blaze block blocks blog bloom blue board boost bot bots box bridge
    bright buffer build builder bundle button cache calendar canvas capsule cargo carbon cards
    cast castle catalog chain chart charts chat check circle city clean cli client clip clock
    cloud cluster code coder codex comet common compass compose config confetti connect console
    copy core craft cron crystal cube cursor dash dashboard data delta demo deploy desk devtools
    diff digest docs domain dot draft drift drop dune dust echo edge editor ember engine entry
    event events explorer express fabric falcon feed fetch fiber field file files filter flag
    flash fleet flow flux focus forge form forms frame fresh fuse gamma gate gateway gem ghost
    glass globe graph grid guard guide harbor hash haven helix helm hive hook hooks horizon host
    hub icon index ink insight iris island jet json kernel keys kit lab lambda lane launch layer
    leaf ledger lens level library light lime line link lint list lite live loader local lock
    log logs loop lumen lunar magic mail map maps markdown matrix meta metric metrics micro
    microwave mind mint mirror mobile mode model monitor moon motion nano native nebula nest net
    network nexus node notes nova oasis ocean omega open orbit panel parser path pay peak pick
    pilot pipe pixel plan planet platform play plugin pod point poly portal post prism probe
    project proto proxy pulse query queue quick radar rail rapid raven react ready relay remote
    render repo rest ring rocket root route router rover rust sage scale scout screen script
    search sense server shadow shell shield shift signal simple sketch sky slate smart snap solar
    source space spark sphere spot stack stage star static station storm stream studio summit
    sync system table tail task tasks term terminal test text theme tide tile timer token tool
    toolkit tools toml track trace tree trend tune type ui unit uno vault vector verse view
    vision vista void volt wave web wiki wind wing wire works world yaml zen zero zone
"""
TITLE_STOPLIST: frozenset[str] = frozenset(_STOPLIST_WORDS.split())
# rule (a): the name fills the title's product slot, then the title ends or a separator follows
_SLOT_PREFIX = r"^\s*(?:show|launch)\s+hn\s*:\s*"
_SLOT_END = r"(?=\s*$|\s*[–—:,(|]|\s+-)"
_NAME_SEP = r"[-_ ]+"  # hyphens, underscores and spaces are equivalent inside the name


class LaunchLookupUnavailable(SelectionError):
    """ADR-082: the selection's launch lookup can't run (Show HN connector off or missing), so
    the selection is refused before anything is fetched, computed or stored."""


LAUNCH_LOOKUP_NEEDS_HN = (
    "the selection's launch lookup needs the Show HN connector (hn_showhn), which is off or not "
    "configured: set PIGTAIL_CONNECTOR_HN_SHOWHN_ENABLED=true (it is on by default when unset) "
    "and run again. The pre-registered selection rule includes the lookup, so the selection is "
    "refused; nothing was fetched, computed or stored and the run can be resumed (ADR-082)"
)


def launch_lookup_blocked(hn: Any) -> str | None:
    """Why the launch lookup can't run (ADR-082), or None when the connector is there."""
    if hn is None or not getattr(hn, "enabled", True):
        return LAUNCH_LOOKUP_NEEDS_HN
    return None


def lookup_queries(full_name: str) -> list[tuple[str, str, str]]:
    """(label, query, tags) of the launch lookup for one repo: the repo URL and the repo name
    among Show HN stories (`tags=show_hn`), and the name among Launch HN stories
    (`tags=launch_hn`, ADR-082). The queries hold nothing but the repo's own name."""
    owner, name = full_name.split("/", 1)
    return [
        ("url", f"github.com/{owner}/{name}", "show_hn"),
        ("name", name, "show_hn"),
        ("launch_hn", name, "launch_hn"),
    ]


def _name_parts(full_name: str) -> list[str]:
    return [p for p in re.split(r"[-_\s]+", full_name.split("/", 1)[-1]) if p]


def _mentions_name(title: str, parts: Sequence[str]) -> bool:
    """The title names the repo anywhere as a whole word (the anchor-v2 test; ADR-082 uses it
    only to count rejected title-only candidates)."""
    pat = (
        r"(?<![A-Za-z0-9_.-])"
        + _NAME_SEP.join(re.escape(p) for p in parts)
        + r"(?![A-Za-z0-9_-]|\.[A-Za-z0-9])"
    )
    return re.search(pat, title, re.IGNORECASE) is not None


def title_names_repo(title: str | None, full_name: str) -> bool:
    """ADR-082 rules (a) and (d): the title is `Show HN: <name>` or `Launch HN: <name>` followed
    by the end of the title or a separator (`–`, `—`, ` -`, `:`, `,`, `(`, `|`), case-insensitive,
    with hyphens, underscores and spaces in the name equivalent; the GitHub name has at least
    `TITLE_MIN_CHARS` characters and is not made only of `TITLE_STOPLIST` words."""
    return title_rejection(title, full_name) is None


def title_rejection(title: str | None, full_name: str) -> str | None:
    """Why the title does not name the repo in its product slot (ADR-082 rules a, d), or None."""
    parts = _name_parts(full_name)
    if not title or not parts:
        return "not_product_slot"
    slot = _SLOT_PREFIX + _NAME_SEP.join(re.escape(p) for p in parts) + _SLOT_END
    if re.match(slot, title, re.IGNORECASE) is None:
        return "not_product_slot"
    if len(full_name.split("/", 1)[-1]) < TITLE_MIN_CHARS:
        return "short_name"
    if all(p.lower() in TITLE_STOPLIST for p in parts):
        return "common_word"
    return None


def classify_launch_post(
    story: Any, full_name: str, *, created: datetime | None
) -> tuple[str | None, str | None]:
    """(match, rejection) for one lookup hit (ADR-082). `match` is `url` when the story links
    the repo's `github.com/owner/name` (normalised: case, `www.`, `.git` and deeper paths don't
    matter; the A2 rule), `title` when all of these hold: (a)+(d) `title_names_repo`; (b) the
    story links no GitHub repo; (c) it was posted no earlier than the repo's creation minus
    `CREATION_TOLERANCE` (an unknown creation date fails). Otherwise `match` is None, and
    `rejection` names the first rule a **title-only candidate** failed (a hit whose title names
    the repo as a whole word anywhere); hits that don't name it at all are not counted."""
    linked = story.repo_full_name
    if linked is not None and linked == full_name.lower():
        return "url", None
    title = story.title or ""
    parts = _name_parts(full_name)
    if not parts or not _mentions_name(title, parts):
        return None, None
    why = title_rejection(title, full_name)
    if why is not None:
        return None, why
    if linked is not None:
        return None, "links_other_repo"
    if created is None:
        return None, "no_creation_date"
    if story.created_at is None or story.created_at < created - CREATION_TOLERANCE:
        return None, "before_creation"
    return "title", None


def match_launch_post(story: Any, full_name: str, *, created: datetime | None) -> str | None:
    """`url`, `title` or None for one lookup hit (`classify_launch_post`, ADR-082)."""
    return classify_launch_post(story, full_name, created=created)[0]


@dataclass
class LookupResult:
    repos: int = 0
    looked_up: int = 0
    already_done: int = 0
    requests: int = 0
    posts: int = 0
    by_match: dict[str, int] = field(default_factory=dict)
    # title-only candidates rejected by the ADR-082 rule, by first failed rule; counts only
    # (no titles), cumulative over the run (kept in the checkpoint across resumes)
    title_rejected: dict[str, int] = field(default_factory=dict)
    failed: dict[str, int] = field(default_factory=dict)
    rule: str = ANCHOR_RULE_VERSION
    evidence_ids: list[str] = field(default_factory=list)

    @property
    def title_rejected_total(self) -> int:
        return sum(self.title_rejected.values())

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d.pop("evidence_ids")
        d["title_rejected_total"] = self.title_rejected_total
        return d


def _created_at(c: Candidate) -> datetime | None:
    raw = c.metadata.get("created_at")
    if not raw:
        return None
    try:
        return _t(raw)
    except ValueError:
        return None


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
    """Step 1b (ADR-081, ADR-082): find each shortlisted repo's Show HN / Launch HN posts in the
    window, whether discovery found them or not. Project-level fields only (item id, time,
    points, kind, match, rule): `parse_show_hn_page` never reads the author, and the raw page is
    dropped right after parsing (CB-24). The evidence record names the repo, not the query. The
    connector's rate limiter paces the requests. A repo's lookup records replace the ones an
    earlier rule stored (for example rows carried forward from another brief version). Title-only
    candidates the rule rejects are counted by reason, never stored. Checkpointed per repo
    (`launch_lookup_done`); a failed request propagates and the stage resumes from the next repo
    not done. Raises `LaunchLookupUnavailable` when the connector is off (ADR-082)."""
    from pigtail.capture.db import CaptureDB
    from pigtail.connectors.hn import launch_lookup_evidence_url, parse_show_hn_page
    from pigtail.privacy.deletion import PARSE_ERRORS, DeletionLog, drop_after_parse

    assert brief.version is not None
    if (why := launch_lookup_blocked(hn)) is not None:
        raise LaunchLookupUnavailable(why)
    todo = sorted((c for c in cands if c.repo_full_name), key=lambda c: c.ref)
    res = LookupResult(repos=len(todo))
    db = CaptureDB(conn)
    store = CandidateStore(conn, brief.brief_id, brief.version)
    dlog = DeletionLog(db, "retention", run_id=getattr(recorder, "id", None))
    done: set[str] = set(checkpoint.get("launch_lookup_done") or [])
    rejected_all: dict[str, int] = dict(checkpoint.get("launch_lookup_title_rejected") or {})
    start, end = window
    for c in todo:
        if c.ref in done:
            res.already_done += 1
            continue
        full = str(c.repo_full_name)
        created = _created_at(c)
        found: dict[int, dict[str, Any]] = {}
        rejected: dict[int, str] = {}
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
            kind = "launch_hn" if tags == "launch_hn" else "show_hn"
            for st in stories:
                if st.created_at is None or not start <= st.created_at <= end:
                    continue
                how, why = classify_launch_post(st, full, created=created)
                if how is None:
                    if why is not None:
                        rejected.setdefault(st.item_id, why)
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
                    "rule": ANCHOR_RULE_VERSION,
                }
        store.replace_signals(c.ref, LAUNCH_LOOKUP_SOURCE, [found[k] for k in sorted(found)])
        for rec in found.values():
            res.by_match[rec["match"]] = res.by_match.get(rec["match"], 0) + 1
        res.posts += len(found)
        for item, why in rejected.items():
            if item not in found:
                rejected_all[why] = rejected_all.get(why, 0) + 1
        res.looked_up += 1
        done.add(c.ref)
        checkpoint["launch_lookup_done"] = sorted(done)
        checkpoint["launch_lookup_title_rejected"] = dict(sorted(rejected_all.items()))
        save(checkpoint)
    res.title_rejected = dict(sorted(rejected_all.items()))
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


def _current_lookup(s: Mapping[str, Any]) -> bool:
    """A launch-lookup record stored under the current anchor rule (ADR-082): records of an
    earlier rule (no `rule` field: anchor-v2) are ignored, since their title matches were made
    under the old, looser rule."""
    return s.get("source") == LAUNCH_LOOKUP_SOURCE and s.get("rule") == ANCHOR_RULE_VERSION


def ambiguous_title_matches(cands: Sequence[Candidate]) -> set[tuple[str, int]]:
    """(candidate ref, item id) of lookup title matches to drop (ADR-081): an item matched by
    title to more than one shortlisted repo, or linked by URL to another one."""
    title: dict[int, set[str]] = {}
    url: dict[int, set[str]] = {}
    for c in cands:
        for s in c.sources:
            if s.get("source") == "show_hn" and s.get("hn_item_id"):
                url.setdefault(int(s["hn_item_id"]), set()).add(c.ref)
            elif _current_lookup(s) and s.get("hn_item_id"):
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
        if src == LAUNCH_LOOKUP_SOURCE and (not _current_lookup(s) or (c.ref, item) in drop):
            continue  # a record of an earlier anchor rule, or an ambiguous title match
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


# The code whose behaviour is the anchor rule (ADR-082 guard): the matching of lookup hits, the
# merge of launches, the ambiguity drop and `choose_anchor` with its comparisons, plus the
# constants they read. Their hash is pinned next to `ANCHOR_RULE_VERSION`
# (`selection.ANCHOR_RULE_SOURCE_SHA256`).
ANCHOR_RULE_FUNCTIONS = (
    "lookup_queries",
    "_name_parts",
    "_mentions_name",
    "title_names_repo",
    "title_rejection",
    "classify_launch_post",
    "match_launch_post",
    "_current_lookup",
    "ambiguous_title_matches",
    "_launches",
    "endpoint_day",
    "_precedes",
    "_within_30d_before",
    "_burst_within_90d_after",
    "choose_anchor",
)
ANCHOR_RULE_CONSTANTS = (
    "TITLE_MIN_CHARS",
    "CREATION_TOLERANCE",
    "TITLE_STOPLIST",
    "_SLOT_PREFIX",
    "_SLOT_END",
    "_NAME_SEP",
    "LAUNCH_LOOKUP_SOURCE",
)


def anchor_rule_source_sha256() -> str:
    """SHA-256 of the anchor rule's code (`ANCHOR_RULE_FUNCTIONS`, parsed, docstrings dropped,
    so comments, docstrings and formatting don't count) and constants (`ANCHOR_RULE_CONSTANTS`,
    sorted where they are sets). Pinned as `selection.ANCHOR_RULE_SOURCE_SHA256` (ADR-082)."""
    import ast
    import hashlib
    import inspect
    import textwrap

    g = globals()
    parts: list[str] = []
    for name in ANCHOR_RULE_FUNCTIONS:
        tree = ast.parse(textwrap.dedent(inspect.getsource(g[name])))
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if (
                isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
                and isinstance(body, list)
                and body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:] or [ast.Pass()]
        parts.append(f"{name}={ast.dump(tree, annotate_fields=False)}")
    for name in ANCHOR_RULE_CONSTANTS:
        v = g[name]
        parts.append(f"{name}={sorted(v) if isinstance(v, frozenset | set) else v!r}")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


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
