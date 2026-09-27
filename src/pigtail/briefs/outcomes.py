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
   stored; the raw page is dropped at parse (CB-24); the step is checkpointed per repo. Since
   anchor-v4 (ADR-083 E) a title-only match counts only when the repo has no URL-matched launch
   in the window and it is confirmed (`pigtail.briefs.confirm`: homepage domain, owner login,
   two description keywords, else a paid Haiku check that fails closed); unconfirmed matches
   are stored with the reason and never anchor. Then,
   for every repo on the final shortlist, fetch its **star history**
   (`pigtail.capture.star_history`, ETag-conditional, 30 weeks per page, back to 60 days before
   the brief's window or the repo's creation week; for a repo with a declared launch in the
   window, back to its creation week, for "stars before launch", ADR-083; the same 100-page
   cap). Progress is checkpointed per repo; the GitHub
   request budget pauses the stage (resumable), and a repo the API can't serve (deleted,
   renamed) is recorded and left `unknown`. The refusal list (CB-13) is checked again before
   any fetch, and once more with the GitHub id the metadata query returns: a repo refused by id
   only that was added by URL is removed from the brief version (`Shortlist.forget`), its
   metadata is not stored and its star history is never fetched (M22 verifier round 2).

   Since anchor-v5 (ADR-084), between the lookup and the star history: **view B's launch
   events**. Every shortlisted repo's GitHub releases (`fetch_releases`: newest first, 100 per
   page, up to 10 pages; stored: tag, published_at, prerelease and whether its name or the
   first 300 characters of its body are worded as a launch, `release_is_launch`; never the
   name or body; raw pages dropped), then, for repos with no launch event in the window, the
   **first external mention** (`first_mention`: HN Algolia `search_by_date` over every item
   type for the repo's `github.com/owner/name`, the text read in memory only to check the link;
   stored: item id, time, kind). Both checkpointed per repo. The star history of every repo
   with a view-B anchor is fetched back to its creation week (stars before launch).

   Since anchor-v7 (ADR-085), right after the HN lookup: **Product Hunt launches** (slug lookup
   and topic scan, confirmed name matches; `launch_sources.run_product_hunt`) and **Bluesky posts
   by declared maintainer accounts** that link the repo (`launch_sources.run_bluesky`), when the
   pre-registered parameters say they apply. Both are launch events of view B.

   Since anchor-v8 (ADR-085 addendum 3): the launch lookup also runs its three searches from
   HN's epoch to the window's start, Product Hunt posts of any date found by slug are stored,
   and Bluesky is read past the window's start, so a **declared launch before the window** can
   be seen: such a repo has no view-B anchor (`launched_before_window`). A Bluesky post is a
   launch event only when its text is worded as a launch (the release pattern, in memory).

   Since anchor-v9 (ADR-085 addendum 4): the Product Hunt topic scan reads a **shared,
   instance-level listing cache** (`pigtail.briefs.ph_cache`): only the parts of the window no
   complete scan of the last 14 days covers are scanned, and a failed topic scan leaves the
   repos not done yet `incomplete` (no view-B anchor, counted; more than 10 % refuses).

2. **`load_inputs`** (database only): one `selection.CaseInput` per shortlisted repo, carrying
   the same repo as view B reads it (`launch_case`: anchored by `view_b_anchor` on launch
   events only, never on star data; its values, covariates and anomaly flag relative to that
   anchor; its relaunch events) and the distribution surface coded before any outcome.
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
     fetch). The views' windows (ADR-083): `att.stars_launch@0-2` (endpoint days 0, 1 and 2,
     the LSM days) and `att.stars_follow@3-30` (days 3..29), with the same pending/unknown rules;
     `att.reddit_reach` is `unknown` (`no_connector`: TM-05 is a GAP, pigtail has no Reddit
     connector). **Every other metric is `unknown` with reason `no_connector`**: registry downloads,
     dependents, PR-based community metrics and the business signals have no connector yet
     (outcome-model §7), and nothing is imputed (R18.8).
   - **Covariates** (§5.6): LSM from endpoint days 0..2 (ADR-083), launch quarter and half-year of
     T (UTC), repo age at T from the creation date, primary language (current, not at T),
     launch type (`show_hn`, `launch_hn` or `burst`), stars before launch (net stars from the
     repo's creation day to the day before the anchor window's first day; `unknown` when the
     stored series doesn't reach creation, ADR-083), and the founder audience band, which is
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
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import psycopg

from pigtail.analysis.anomaly import check_population
from pigtail.analysis.bursts import Onset, segment
from pigtail.analysis.params import ANOMALY, STAR_HISTORY_DAY_TZ
from pigtail.briefs.candidates import Candidate, CandidateStore
from pigtail.briefs.confirm import Check, Confirmer, confirm_by_rules, haiku_input
from pigtail.briefs.launch_sources import (
    BSKY_KIND,
    BSKY_SOURCE,
    PH_SOURCE,
    run_bluesky,
    run_product_hunt,
)
from pigtail.briefs.model import METRICS, Brief
from pigtail.briefs.selection import (
    ANCHOR_RULE_VERSION,
    BUSINESS_COUNT,
    BUSINESS_SIGNALS,
    DECLARED_RULES,
    FOLLOW_DAYS,
    FOLLOW_STARS,
    INCOMPLETE_PREFIX,
    LAUNCH_DAYS,
    LAUNCH_SIZE,
    PH_COMMENTS,
    PH_TOPICS,
    PH_VOTES,
    PRE_WINDOW_REASON,
    REDDIT_REACH,
    RELEASE_LAUNCH_PATTERN,
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
    to_creation: int = 0  # repos with a view-B anchor: history fetched back to creation
    launch_lookup: dict[str, Any] | None = None
    # view B's launch events (ADR-084): GitHub releases and the first external mention
    releases: dict[str, Any] = field(default_factory=dict)
    mentions: dict[str, Any] = field(default_factory=dict)
    # ADR-085: Product Hunt launches and declared maintainers' Bluesky posts (counts only)
    product_hunt: dict[str, Any] | None = None
    bluesky: dict[str, Any] | None = None
    evidence_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d.pop("evidence_ids")
        return d


def star_pages(window_start: date, as_of: date) -> int:
    """Pages of 30 weeks that reach 60 days before the window's start (the anomaly baseline)."""
    weeks = (as_of - (window_start - timedelta(days=BASELINE_BEFORE_DAYS + 1))).days // 7 + 2
    return max(1, min(STAR_MAX_PAGES, math.ceil(weeks / STAR_WEEKS_PER_PAGE)))


STAR_MAX_PAGES = 100  # the endpoint's page cap (GitHub docs); 100 x 30 weeks = 57 years
# ADR-083, extended by ADR-084 (checkpoint key): back to creation for every repo with a
# declared launch or a view-B anchor
STAR_PAGES_RULE = "window-60d+creation-for-launched-or-view-b-v2"


def creation_pages(created: date, as_of: date) -> int:
    """Pages of 30 weeks that reach the repo's creation week (stars before launch, view B,
    ADR-083), under the same page cap."""
    weeks = (as_of - created).days // 7 + 2
    return max(1, min(STAR_MAX_PAGES, math.ceil(weeks / STAR_WEEKS_PER_PAGE)))


def has_declared_launch(c: Candidate, start: datetime, end: datetime) -> bool:
    """Whether the candidate's star history is fetched back to creation (stars before launch):
    it has a declared launch in the window that can anchor it (a discovery Show HN post, a
    URL-matched lookup post, or a confirmed title match, current rule; ADR-083), or a view-B
    anchor (a launch event, or an undeclared launch inside the window; ADR-084)."""
    return bool(_launches(c, start, end)) or view_b_anchor(c, start, end)[0] is not None


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
    confirmer: Confirmer | None = None,
    launch_sources: Sequence[str] = (),
    ph: Any = None,
    bsky: Any = None,
    ph_confirmer: Confirmer | None = None,
    ph_topics: Sequence[str] = PH_TOPICS,
    readme: Callable[[Candidate], tuple[bytes | None, str | None]] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> FetchResult:
    """Step 1 (module docstring). `BudgetExhausted` propagates: the stage pauses, resumable.
    `launch_sources` names the view-B sources the pre-registered parameters apply
    (`product_hunt`, `bluesky`; ADR-085): their steps run after the HN lookup with `ph` /
    `bsky` (refused without them, `LaunchSourceUnavailable`), `ph_confirmer` running the
    Product Hunt Haiku check and `readme` loading READMEs for the declared accounts.
    With `window`, the launch lookup (step 1b) runs after the metadata fill and before the star
    history, checkpointed per repo group; it needs `hn` (`LaunchLookupUnavailable` otherwise,
    raised before anything is written; ADR-082), and `confirmer` runs the Haiku check of title
    matches (ADR-083 E; without it they fail closed). A repo with a declared launch in the
    window gets its star history back to its creation week (stars before launch, ADR-083)."""
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
    if checkpoint.get("star_pages_rule") != STAR_PAGES_RULE:  # fetched under an older rule
        checkpoint.pop("star_history_done", None)
        checkpoint.pop("star_history_failed", None)
        checkpoint["star_pages_rule"] = STAR_PAGES_RULE
    done: set[str] = set(checkpoint.get("star_history_done") or [])
    failed: dict[str, str] = dict(checkpoint.get("star_history_failed") or {})
    # metadata first: the launch lookup's title rule needs each repo's creation date (ADR-082)
    # and its confirmation rule 1 the homepage domain (ADR-083 E; "" when there is none)
    need = sorted(
        c.repo_full_name
        for c in cands
        if c.repo_full_name
        and c.ref not in done
        and (
            c.repo_host_id is None
            or not c.metadata.get("created_at")
            or "homepage_domain" not in c.metadata
        )
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
            confirmer=confirmer,
        )
        res.launch_lookup = lk.to_dict()
        res.evidence_ids.extend(lk.evidence_ids)
        live = [latest.get(c.ref, c) for c in cands if c.ref not in refused]
        # view B's Product Hunt launches and declared maintainers' Bluesky posts (ADR-085)
        if "product_hunt" in launch_sources:
            pr = run_product_hunt(
                conn, brief, ph, live, window=window, checkpoint=checkpoint, save=save,
                topics=ph_topics, recorder=recorder, confirmer=ph_confirmer, clock=clock,
            )  # fmt: skip
            res.product_hunt = pr.to_dict()
            res.evidence_ids.extend(pr.evidence_ids)
        if "bluesky" in launch_sources:
            br = run_bluesky(
                conn, brief, bsky, github if gh_on else None, live, window=window,
                checkpoint=checkpoint, save=save, recorder=recorder, readme=readme,
            )  # fmt: skip
            res.bluesky = br.to_dict()
            res.evidence_ids.extend(br.evidence_ids)
        # view B's launch events (ADR-084): releases (GitHub), then the first external mention
        # (HN) of repos with no launch event; project-level, checkpointed per repo
        keep = [c.ref for c in cands if c.ref not in refused]
        if gh_on:
            res.releases, ev = _fetch_all_releases(
                conn, brief, github, keep, checkpoint=checkpoint, save=save, recorder=recorder
            )
            res.evidence_ids.extend(ev)
        res.mentions, ev = _search_all_mentions(
            conn, brief, hn, keep, window=window, checkpoint=checkpoint, save=save,
            recorder=recorder,
        )  # fmt: skip
        res.evidence_ids.extend(ev)
    if not gh_on:
        res.failed["no_github_connector"] = len(cands)
        return res
    db = CaptureDB(conn)
    fresh = {c.ref: c for c in store.all()}
    base_pages = star_pages(window_start, as_of)
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
            pages = base_pages
            created = _created(c)
            if window is not None and created is not None and has_declared_launch(c, *window):
                pages = max(pages, creation_pages(created, as_of))  # stars before launch
                res.to_creation += 1
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
# HN Algolia requests per shortlisted repo (estimate, ADR-082): the three searches inside the
# window, and again from HN's epoch to the window's start (anchor-v8, ADR-085 addendum 3)
LAUNCH_LOOKUP_REQUESTS = 6
LAUNCH_LOOKUP_HITS = 50
LOOKUP_GROUP = 25  # repos per checkpoint (and per Haiku batch of title checks, ADR-083 E)
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
    # title matches that passed ADR-082 and then the E rule (ADR-083): confirmed, by method;
    # unconfirmed (stored with the reason, excluded from anchors and hn_points), by reason
    title_confirmed: dict[str, int] = field(default_factory=dict)
    title_unconfirmed: dict[str, int] = field(default_factory=dict)
    haiku_checks: int = 0
    failed: dict[str, int] = field(default_factory=dict)
    rule: str = ANCHOR_RULE_VERSION
    evidence_ids: list[str] = field(default_factory=list)

    @property
    def title_rejected_total(self) -> int:
        return sum(self.title_rejected.values())

    @property
    def title_unconfirmed_total(self) -> int:
        return sum(self.title_unconfirmed.values())

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d.pop("evidence_ids")
        d["title_rejected_total"] = self.title_rejected_total
        d["title_unconfirmed_total"] = self.title_unconfirmed_total
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
    confirmer: Confirmer | None = None,
    group_size: int = LOOKUP_GROUP,
) -> LookupResult:
    """Step 1b (ADR-081, ADR-082, ADR-083): find each shortlisted repo's Show HN / Launch HN
    posts in the window, whether discovery found them or not. Project-level fields only (item
    id, time, points, kind, match, rule, and for title matches the confirmation):
    `parse_show_hn_page` never reads the author, and the raw page is dropped right after parsing
    (CB-24). The evidence record names the repo, not the query. The connector's rate limiter
    paces the requests. A repo's lookup records replace the ones an earlier rule stored (for
    example rows carried forward from another brief version). Title-only candidates the ADR-082
    rule rejects are counted by reason, never stored.

    Title matches that pass it go through the E rule (`pigtail.briefs.confirm`, ADR-083):
    excluded when the repo has a URL-matched launch in the window (discovery or this lookup),
    else confirmed by rules 1-3 or, failing those, by the Haiku check (`confirmer`, batched per
    group of `group_size` repos; without a confirmer they fail closed). Unconfirmed matches
    are stored with `confirmed: false` and the reason, and counted. The titles are held in
    memory only while their group is processed.

    Checkpointed per repo (`launch_lookup_done`); a repo with a title match waiting for the
    Haiku check is checkpointed after its group's batch. A failed request, or a Haiku batch
    still running (`BatchPending`), propagates, and the stage resumes with the repos not done
    (their free HN requests are made again; answered Haiku checks come from the LLM cache and a
    running batch is collected, not resubmitted). Raises `LaunchLookupUnavailable` when the
    connector is off (ADR-082)."""
    from pigtail.capture.db import CaptureDB
    from pigtail.privacy.deletion import DeletionLog

    assert brief.version is not None
    if (why := launch_lookup_blocked(hn)) is not None:
        raise LaunchLookupUnavailable(why)
    if checkpoint.get("launch_lookup_rule") != ANCHOR_RULE_VERSION:  # an older rule's progress
        for k in (
            "launch_lookup_done",
            "launch_lookup_title_rejected",
            "launch_lookup_title_confirmed",
            "launch_lookup_title_unconfirmed",
            "launch_lookup_haiku_checks",
        ):
            checkpoint.pop(k, None)
        checkpoint["launch_lookup_rule"] = ANCHOR_RULE_VERSION
    todo = sorted((c for c in cands if c.repo_full_name), key=lambda c: c.ref)
    res = LookupResult(repos=len(todo))
    db = CaptureDB(conn)
    store = CandidateStore(conn, brief.brief_id, brief.version)
    dlog = DeletionLog(db, "retention", run_id=getattr(recorder, "id", None))
    done: set[str] = set(checkpoint.get("launch_lookup_done") or [])
    rejected_all: dict[str, int] = dict(checkpoint.get("launch_lookup_title_rejected") or {})
    confirmed_all: dict[str, int] = dict(checkpoint.get("launch_lookup_title_confirmed") or {})
    unconf_all: dict[str, int] = dict(checkpoint.get("launch_lookup_title_unconfirmed") or {})
    haiku_n = int(checkpoint.get("launch_lookup_haiku_checks") or 0)
    start, end = window
    pending = [c for c in todo if c.ref not in done]
    res.already_done = len(todo) - len(pending)

    def finish(c: Candidate, found: dict[int, dict[str, Any]], rejected: dict[int, str]) -> None:
        """Store one repo's records, count them and checkpoint it."""
        store.replace_signals(c.ref, LAUNCH_LOOKUP_SOURCE, [found[k] for k in sorted(found)])
        for rec in found.values():
            res.by_match[rec["match"]] = res.by_match.get(rec["match"], 0) + 1
            if rec["match"] != "title":
                continue
            if rec["confirmed"]:
                k = str(rec["confirmation"])
                confirmed_all[k] = confirmed_all.get(k, 0) + 1
            else:
                k = str(rec["confirmation"]).removeprefix("unconfirmed:")
                unconf_all[k] = unconf_all.get(k, 0) + 1
        res.posts += len(found)
        for item, why in rejected.items():
            if item not in found:
                rejected_all[why] = rejected_all.get(why, 0) + 1
        res.looked_up += 1
        done.add(c.ref)
        checkpoint["launch_lookup_done"] = sorted(done)
        checkpoint["launch_lookup_title_rejected"] = dict(sorted(rejected_all.items()))
        checkpoint["launch_lookup_title_confirmed"] = dict(sorted(confirmed_all.items()))
        checkpoint["launch_lookup_title_unconfirmed"] = dict(sorted(unconf_all.items()))
        checkpoint["launch_lookup_haiku_checks"] = haiku_n
        save(checkpoint)

    for g in range(0, len(pending), group_size):
        group = pending[g : g + group_size]
        deferred: list[tuple[Candidate, dict[int, dict[str, Any]], dict[int, str]]] = []
        checks: list[Check] = []
        for c in group:
            full = str(c.repo_full_name)
            found, rejected, stories_of = _lookup_repo(
                hn, full, _created_at(c), start, end, res, db, dlog
            )
            # the E rule's "URL-matched launch in the window", judged per period: a post before
            # the window (view B's pre-window rule, anchor-v8) against the ones before it only,
            # so view A's in-window matches are exactly as before
            url_in = any(
                r["match"] == "url" and not _before(r, start) for r in found.values()
            ) or bool(_discovery_launches(c, start, end))
            url_pre = any(r["match"] == "url" and _before(r, start) for r in found.values())
            mine: list[Check] = []
            for item, rec in found.items():
                if rec["match"] != "title":
                    continue
                st = stories_of[item]
                if url_pre if _before(rec, start) else url_in:
                    rec["confirmed"], rec["confirmation"] = False, "unconfirmed:has_url_launch"
                    continue
                meta = c.metadata
                how = confirm_by_rules(
                    full_name=full,
                    description=meta.get("description"),
                    homepage_domain=meta.get("homepage_domain") or None,
                    title=st.title,
                    url=st.url,
                )
                if how is not None:
                    rec["confirmed"], rec["confirmation"] = True, how
                    continue
                text = haiku_input(
                    full, meta.get("description"), meta.get("homepage_domain"), st.title, st.url
                )
                mine.append(Check((c.ref, item), text))
            if mine:  # waits for the group's Haiku batch
                checks += mine
                deferred.append((c, found, rejected))
            else:
                finish(c, found, rejected)
        if not checks:
            continue
        # rule 4, one batch for the group's title matches (BatchPending propagates: the
        # deferred repos aren't done, so a resume looks them up again and collects the batch)
        outcomes = (confirmer or Confirmer(None)).run(checks)
        haiku_n += len(checks)
        for c, found, rejected in deferred:
            for item, rec in found.items():
                o = outcomes.get((c.ref, item))
                if o is None:
                    continue
                rec["confirmed"], rec["confirmation"] = o.confirmed, o.confirmation
                if o.provenance is not None:
                    rec["confirmation_provenance"] = o.provenance
            finish(c, found, rejected)
    res.title_rejected = dict(sorted(rejected_all.items()))
    res.title_confirmed = dict(sorted(confirmed_all.items()))
    res.title_unconfirmed = dict(sorted(unconf_all.items()))
    res.haiku_checks = haiku_n
    return res


def _before(rec: Mapping[str, Any], start: datetime) -> bool:
    """Whether a stored launch record is dated before the window's start."""
    return _t(rec["time"]) < start


def _lookup_repo(
    hn: Any,
    full: str,
    created: datetime | None,
    start: datetime,
    end: datetime,
    res: LookupResult,
    db: Any,
    dlog: Any,
) -> tuple[dict[int, dict[str, Any]], dict[int, str], dict[int, Any]]:
    """The three searches of one repo: (records by item id, title-only rejections by item id,
    the parsed stories by item id, held in memory for the confirmation only)."""
    from pigtail.connectors.hn import launch_lookup_evidence_url as evidence_url
    from pigtail.connectors.hn import parse_show_hn_page as parse
    from pigtail.privacy.deletion import PARSE_ERRORS as parse_errors
    from pigtail.privacy.deletion import drop_after_parse as drop

    found: dict[int, dict[str, Any]] = {}
    rejected: dict[int, str] = {}
    stories_of: dict[int, Any] = {}
    # inside the window, then from HN's epoch to the window's start (anchor-v8: view B's
    # pre-window rule): two ranges, so posts before the window never crowd the window's own
    # relevance-ranked hits out
    searches = [
        (label, query, tags, lo, hi, suffix)
        for lo, hi, suffix in ((start, end, ""), (None, start - timedelta(seconds=1), ":before"))
        for label, query, tags in lookup_queries(full)
    ]
    for label, query, tags, lo, hi, suffix in searches:
        f = hn.search_show_hn(
            query,
            since=lo,
            until=hi,
            hits=LAUNCH_LOOKUP_HITS,
            tags=tags,
            evidence_url=evidence_url(full, f"{label}{suffix}", tags),
        )
        res.requests += 1
        res.evidence_ids.append(f.evidence.id)
        try:
            stories, _ = parse(f.data)
        except parse_errors:
            stories = []
            res.failed["parse_failed"] = res.failed.get("parse_failed", 0) + 1
        drop(db, hn.store, f.evidence.id, f.content_hash, dlog)
        kind = "launch_hn" if tags == "launch_hn" else "show_hn"
        for st in stories:
            if st.created_at is None or st.created_at > end:
                continue
            how, why = classify_launch_post(st, full, created=created)
            if how is None:
                if why is not None:
                    rejected.setdefault(st.item_id, why)
                continue
            prev = found.get(st.item_id)
            if prev is not None and (prev["match"] == "url" or how == "title"):
                continue
            stories_of[st.item_id] = st
            found[st.item_id] = {
                "source": LAUNCH_LOOKUP_SOURCE,
                "hn_item_id": st.item_id,
                "time": st.created_at.isoformat(),
                "points": st.points,
                "kind": kind,
                "match": how,
                "rule": ANCHOR_RULE_VERSION,
            }
    return found, rejected, stories_of


# --- 1c. view B's launch events: GitHub releases and the first external mention (ADR-084) -----
RELEASE_SOURCE = "gh_releases"
RELEASE_MAX_PAGES = 10  # 100 releases per page, newest first: up to 1,000 releases per repo
RELEASE_LAUNCH_RE = re.compile(RELEASE_LAUNCH_PATTERN, re.IGNORECASE)
RELEASE_TEXT_CHARS = 300
MENTION_SOURCE = "hn_first_mention"
MENTION_MAX_REQUESTS = 5  # HN Algolia requests per repo (page 0, then from the oldest page back)
MENTION_HITS_CAP = 1000  # Algolia serves at most ~1,000 hits per query (source-matrix §2.3)


def release_is_launch(name: str | None, body: str | None) -> bool:
    """ADR-084 (ii): a release is announced as a launch when its name, or the first 300
    characters of its body, contain `launch`, `launching`, `introducing`, `announcing` or
    `first public release` as whole words, case-insensitive (each text is tested on its own)."""
    return any(
        RELEASE_LAUNCH_RE.search(t) is not None
        for t in (name or "", (body or "")[:RELEASE_TEXT_CHARS])
    )


def fetch_releases(
    github: Any,
    db: Any,
    dlog: Any,
    full: str,
    repo_id: str | None,
    evidence: list[str] | None = None,
) -> dict[str, Any]:
    """One repo's releases (newest first, 100 per page, up to `RELEASE_MAX_PAGES`), as the
    stored signal: tag, published_at, prerelease and the launch-wording test per published
    release (drafts have no publication date and are left out); `complete` is false when the
    page cap cut the list or a request failed. The raw pages are dropped after parsing (their
    items embed the author's account; `parse_releases` never reads it), and no release name or
    body is stored."""
    from pigtail.connectors.base import FetchError
    from pigtail.connectors.github import RELEASES_PER_PAGE, parse_releases
    from pigtail.privacy.deletion import PARSE_ERRORS as parse_errors
    from pigtail.privacy.deletion import drop_after_parse as drop

    rels: list[Any] = []
    pages = 0
    status = "truncated"
    for page in range(1, RELEASE_MAX_PAGES + 1):
        try:
            f = github.releases_page(full, page=page, repo_id=repo_id)
        except FetchError as e:
            status = f"failed:{type(e).__name__}"
            break
        pages += 1
        if evidence is not None:
            evidence.append(f.evidence.id)
        try:
            items = parse_releases(f.data)
        except parse_errors:
            drop(db, github.store, f.evidence.id, f.content_hash, dlog)
            status = "failed:parse"
            break
        drop(db, github.store, f.evidence.id, f.content_hash, dlog)
        rels += items
        if len(items) < RELEASES_PER_PAGE:
            status = "complete"
            break
    records = sorted(
        (
            {
                "tag": r.tag,
                "published_at": r.published_at.astimezone(UTC).isoformat(),
                "prerelease": r.prerelease,
                "launch": release_is_launch(r.name, r.body_head),
            }
            for r in rels
            if r.published_at is not None
        ),
        key=lambda x: (x["published_at"], x["tag"]),
    )
    return {
        "source": RELEASE_SOURCE,
        "rule": ANCHOR_RULE_VERSION,
        "status": status,
        "complete": status == "complete",
        "pages": pages,
        "releases": records,
    }


def first_mention(
    hn: Any, db: Any, dlog: Any, full: str, until: datetime, evidence: list[str] | None = None
) -> dict[str, Any]:
    """ADR-084: the earliest HN item (any type) up to `until` whose URL or text links the
    repo's `github.com/owner/name`, as the stored signal (item id, time and kind only). HN
    Algolia `search_by_date` (newest first) for `github.com/<owner>/<name>` over all item
    types: page 0, then from the oldest page back, until a page holds a real link (Algolia's
    match is fuzzy; `parse_mention_page` checks each hit), at most `MENTION_MAX_REQUESTS`
    requests. Status `found`, `none` (every page read, no link), `capped` (more hits than
    Algolia serves: the oldest can't be reached), `truncated` (request cap) or `failed:…`."""
    from pigtail.connectors.base import FetchError
    from pigtail.connectors.hn import launch_lookup_evidence_url as evidence_url
    from pigtail.connectors.hn import parse_mention_page
    from pigtail.privacy.deletion import PARSE_ERRORS as parse_errors
    from pigtail.privacy.deletion import drop_after_parse as drop

    query = f"github.com/{full}"
    base: dict[str, Any] = {"source": MENTION_SOURCE, "rule": ANCHOR_RULE_VERSION, "requests": 0}

    def page(n: int) -> tuple[list[Any], int, int] | str:
        try:
            f = hn.search_mentions(
                query,
                until=until,
                page=n,
                evidence_url=f"{evidence_url(full, 'first_mention', 'story,comment')}&page={n}",
            )
        except FetchError as e:
            return f"failed:{type(e).__name__}"
        base["requests"] += 1
        if evidence is not None:
            evidence.append(f.evidence.id)
        try:
            got = parse_mention_page(f.data, full)
        except parse_errors:
            drop(db, hn.store, f.evidence.id, f.content_hash, dlog)
            return "failed:parse"
        drop(db, hn.store, f.evidence.id, f.content_hash, dlog)
        return got

    first = page(0)
    if isinstance(first, str):
        return {**base, "status": first}
    hits0, nb, npages = first
    if nb > MENTION_HITS_CAP:
        return {**base, "status": "capped"}
    order = [*range(max(npages, 1) - 1, 0, -1), 0]  # oldest page first, page 0 last
    for n in order:
        if n == 0:
            hits = hits0
        elif base["requests"] >= MENTION_MAX_REQUESTS:
            return {**base, "status": "truncated"}
        else:
            got = page(n)
            if isinstance(got, str):
                return {**base, "status": got}
            hits = got[0]
        linked = [h for h in hits if h.links_repo and h.created_at is not None]
        if linked:
            h = min(linked, key=lambda x: (x.created_at, x.item_id))
            return {
                **base,
                "status": "found",
                "hn_item_id": h.item_id,
                "time": h.created_at.isoformat(),
                "kind": h.kind,
            }
    return {**base, "status": "none"}


def _fetch_all_releases(
    conn: psycopg.Connection[Any],
    brief: Brief,
    github: Any,
    refs: Sequence[str],
    *,
    checkpoint: dict[str, Any],
    save: Callable[[dict[str, Any]], None],
    recorder: Any = None,
) -> tuple[dict[str, Any], list[str]]:
    """`fetch_releases` for every shortlisted repo, checkpointed per repo (`releases_done`);
    `BudgetExhausted` propagates (the stage pauses, resumable). Counts only."""
    from pigtail.capture.db import CaptureDB
    from pigtail.privacy.deletion import DeletionLog

    assert brief.version is not None
    if checkpoint.get("releases_rule") != ANCHOR_RULE_VERSION:
        checkpoint.pop("releases_done", None)
        checkpoint["releases_rule"] = ANCHOR_RULE_VERSION
    done: set[str] = set(checkpoint.get("releases_done") or [])
    store = CandidateStore(conn, brief.brief_id, brief.version)
    db = CaptureDB(conn)
    dlog = DeletionLog(db, "retention", run_id=getattr(recorder, "id", None))
    out: dict[str, Any] = {"repos": len(refs), "already_done": 0, "pages": 0, "status": {}}
    evidence: list[str] = []
    latest = {c.ref: c for c in store.all()}
    for ref in sorted(refs):
        if ref in done:
            out["already_done"] += 1
            continue
        c = latest.get(ref)
        if c is None or not c.repo_full_name:
            continue
        sig = fetch_releases(github, db, dlog, c.repo_full_name, c.repo_id, evidence)
        store.replace_signals(ref, RELEASE_SOURCE, [sig])
        out["pages"] += sig["pages"]
        st = str(sig["status"])
        out["status"][st] = out["status"].get(st, 0) + 1
        done.add(ref)
        checkpoint["releases_done"] = sorted(done)
        save(checkpoint)
    return out, evidence


def _search_all_mentions(
    conn: psycopg.Connection[Any],
    brief: Brief,
    hn: Any,
    refs: Sequence[str],
    *,
    window: tuple[datetime, datetime],
    checkpoint: dict[str, Any],
    save: Callable[[dict[str, Any]], None],
    recorder: Any = None,
) -> tuple[dict[str, Any], list[str]]:
    """`first_mention` for every shortlisted repo without a launch event in the window (after
    the lookup and the releases), checkpointed per repo (`mentions_done`). Counts only."""
    from pigtail.capture.db import CaptureDB
    from pigtail.privacy.deletion import DeletionLog

    assert brief.version is not None
    if checkpoint.get("mentions_rule") != ANCHOR_RULE_VERSION:
        checkpoint.pop("mentions_done", None)
        checkpoint["mentions_rule"] = ANCHOR_RULE_VERSION
    done: set[str] = set(checkpoint.get("mentions_done") or [])
    store = CandidateStore(conn, brief.brief_id, brief.version)
    db = CaptureDB(conn)
    dlog = DeletionLog(db, "retention", run_id=getattr(recorder, "id", None))
    start, end = window
    out: dict[str, Any] = {"searched": 0, "not_needed": 0, "requests": 0, "status": {}}
    evidence: list[str] = []
    latest = {c.ref: c for c in store.all()}
    for ref in sorted(refs):
        c = latest.get(ref)
        if c is None or not c.repo_full_name:
            continue
        if launch_events(c, start, end) or launch_events_before(c, start):
            out["not_needed"] += 1
            continue
        if ref in done:
            continue
        sig = first_mention(hn, db, dlog, c.repo_full_name, end, evidence)
        store.replace_signals(ref, MENTION_SOURCE, [sig])
        out["searched"] += 1
        out["requests"] += int(sig.get("requests") or 0)
        st = str(sig["status"])
        out["status"][st] = out["status"].get(st, 0) + 1
        done.add(ref)
        checkpoint["mentions_done"] = sorted(done)
        save(checkpoint)
    return out, evidence


@dataclass(frozen=True)
class LaunchEvent:
    """One maintainer-initiated launch event of a repo (view B, ADR-084)."""

    at: datetime
    kind: str  # show_hn | launch_hn | product_hunt | release_launch | bluesky_maintainer_post
    ref: str  # the HN item id, the Product Hunt post id, the release tag, or `<match>#<n>`
    # discovery | lookup:url | lookup:title | product_hunt:<route> | github_release |
    # bluesky:<match>
    via: str

    def to_dict(self) -> dict[str, Any]:
        return {"at": self.at.isoformat(), "kind": self.kind, "ref": self.ref, "via": self.via}


def _signal(c: Candidate, source: str) -> dict[str, Any] | None:
    """The candidate's current-rule signal of `source` (one per repo), or None."""
    for s in c.sources:
        if s.get("source") == source and s.get("rule") == ANCHOR_RULE_VERSION:
            return s
    return None


def ph_launches(
    c: Candidate,
    start: datetime,
    end: datetime,
    drop: set[tuple[str, Any]] | frozenset[tuple[str, Any]] = frozenset(),
) -> list[tuple[datetime, str, str, int | None, int | None]]:
    """The repo's confirmed Product Hunt launches inside the window (ADR-085): (time, post id,
    route, votes, comments), the time being featuredAt when set, else createdAt. A post
    confirmed for more than one shortlisted repo (`drop`) never counts."""
    out = []
    for r in (_signal(c, PH_SOURCE) or {}).get("posts") or []:
        if r.get("confirmed") is not True or (c.ref, f"ph:{r.get('ph_post_id')}") in drop:
            continue
        raw = r.get("featured_at") or r.get("created_at")
        if not raw:
            continue
        t = _t(raw)
        if start <= t <= end:
            v, n = r.get("votes"), r.get("comments")
            votes = v if isinstance(v, int) else None
            comments = n if isinstance(n, int) else None
            out.append((t, str(r["ph_post_id"]), str(r.get("route") or ""), votes, comments))
    return sorted(out, key=lambda x: (x[0], x[1]))


def launch_events(
    c: Candidate,
    start: datetime,
    end: datetime,
    drop: set[tuple[str, Any]] | frozenset[tuple[str, Any]] = frozenset(),
) -> list[LaunchEvent]:
    """The repo's launch events inside the window, in order: its declared Show HN / Launch HN
    launches (`_launches`: discovery and the lookup, confirmed title matches only), its
    confirmed Product Hunt launches (`ph_launches`, ADR-085), its GitHub releases announced as
    a launch (`release_is_launch`, stored as `launch`) and the Bluesky posts of its declared
    maintainer accounts that link it (ADR-085). Sorted by time, then kind (`DECLARED_RULES`:
    show_hn, launch_hn, product_hunt, release_launch, bluesky_maintainer_post), then ref. No
    star data."""
    order = {k: i for i, k in enumerate(DECLARED_RULES)}
    out = [
        LaunchEvent(x.at, x.source, str(x.item_id), x.via) for x in _launches(c, start, end, drop)
    ]
    for t, pid, route, _v, _n in ph_launches(c, start, end, drop):
        out.append(LaunchEvent(t, "product_hunt", pid, f"product_hunt:{route}"))
    rel = _signal(c, RELEASE_SOURCE)
    for r in (rel or {}).get("releases") or []:
        if not r.get("launch"):
            continue
        t = _t(r["published_at"])
        if start <= t <= end:
            out.append(LaunchEvent(t, "release_launch", str(r["tag"]), "github_release"))
    bs = _signal(c, BSKY_SOURCE)
    if bs is not None and bs.get("status") == "complete":
        for i, r in enumerate(bs.get("posts") or []):
            if r.get("kind") != BSKY_KIND or r.get("role") != "maintainer" or not r.get("time"):
                continue
            t = _t(r["time"])
            if start <= t <= end:
                m = str(r.get("match"))
                out.append(LaunchEvent(t, BSKY_KIND, f"{m}#{i}", f"bluesky:{m}"))
    return sorted(out, key=lambda e: (e.at, order[e.kind], e.ref))


EARLIEST = datetime(1970, 1, 1, tzinfo=UTC)  # the lower bound of "before the window"
LATEST = datetime(9999, 1, 1, tzinfo=UTC)


def launch_events_before(
    c: Candidate,
    start: datetime,
    drop: set[tuple[str, Any]] | frozenset[tuple[str, Any]] = frozenset(),
) -> list[LaunchEvent]:
    """The repo's declared launch events before the window's start (ADR-085 addendum 3), by
    the same rules as `launch_events` (discovery's and the lookup's Show HN / Launch HN posts,
    confirmed Product Hunt posts, launch-worded releases and launch-worded posts of declared
    Bluesky accounts), in the same order. Any of them means the repo launched before the
    window: it has no view-B anchor."""
    return [
        e
        for e in launch_events(c, EARLIEST, start - timedelta(microseconds=1), drop)
        if e.at < start
    ]


def incomplete_source(c: Candidate, sources: Sequence[str]) -> str | None:
    """The first launch source among `sources` (the ones the pre-registered parameters apply)
    whose data for this repo is missing or incomplete (ADR-085), else None. An unread source
    could hold the earliest launch event, so the view-B anchor would silently change."""
    for src in sources:
        if src == "product_hunt":
            # anchor-v9 (ADR-085 addendum 4): a failed topic scan stores `incomplete`
            p = _signal(c, PH_SOURCE)
            if p is None or p.get("status") != "complete":
                return src
        if src == "bluesky":
            s = _signal(c, BSKY_SOURCE)
            if s is None or s.get("status") not in ("complete", "no_declared_account"):
                return src
    return None


def ambiguous_ph_posts(cands: Sequence[Candidate]) -> set[tuple[str, str]]:
    """(candidate ref, `ph:<post id>`) of Product Hunt posts confirmed for more than one
    shortlisted repo (ADR-085): dropped for all of them, as ADR-082 rule (e) drops a title
    match two repos claim."""
    by_post: dict[str, set[str]] = {}
    for c in cands:
        for r in (_signal(c, PH_SOURCE) or {}).get("posts") or []:
            if r.get("confirmed") is True and r.get("ph_post_id") is not None:
                by_post.setdefault(str(r["ph_post_id"]), set()).add(c.ref)
    return {(ref, f"ph:{pid}") for pid, refs in by_post.items() if len(refs) > 1 for ref in refs}


def first_release_at(c: Candidate) -> tuple[datetime | None, str | None, bool]:
    """(time of the earliest published release or None, its tag, known): `known` is false when
    the release list is missing or incomplete (page cap, failed request), so the first release
    can't be told."""
    rel = _signal(c, RELEASE_SOURCE)
    if rel is None or not rel.get("complete"):
        return None, None, False
    rs = rel.get("releases") or []
    if not rs:
        return None, None, True
    r0 = min(rs, key=lambda r: (r["published_at"], str(r["tag"])))
    return _t(r0["published_at"]), str(r0["tag"]), True


def choose_launch_anchor(
    events: Sequence[LaunchEvent],
    mention: Mapping[str, Any] | None,
    first_release: tuple[datetime | None, str | None, bool],
    start: datetime,
    end: datetime,
) -> tuple[Anchor | None, str | None, list[dict[str, Any]]]:
    """View B's anchor (ADR-084, `selection.VIEW_B_ANCHOR_RULE`): (anchor or None, reason,
    relaunch events). Launch events only, never star data: the first launch event in the window
    anchors (rule = its kind) and every later one is a relaunch event. Without one, the earlier
    of the first external mention and the first public release (`undeclared:first_mention`,
    `undeclared:first_release`; a tie goes to the mention), inside the window; no anchor when
    neither exists, when the earlier is outside the window, or when either source is unknown."""
    if events:
        e0 = events[0]
        a = Anchor("launch", e0.at, "hour", e0.kind, e0.via, rule=e0.kind, ref=e0.ref)
        return a, None, [e.to_dict() for e in events[1:]]
    rel_at, rel_tag, rel_known = first_release
    status = (mention or {}).get("status")
    if mention is None or status not in ("found", "none"):
        return None, f"undeclared:mention_{status or 'not_searched'}", []
    if not rel_known:
        return None, "undeclared:releases_incomplete", []
    options: list[tuple[datetime, int, Anchor]] = []
    if status == "found":
        t = _t(mention["time"])
        via = "hn_comment" if mention.get("kind") == "comment" else "hn_story"
        rule = "undeclared:first_mention"
        ref = str(mention.get("hn_item_id"))
        options.append((t, 0, Anchor("launch", t, "hour", "first_mention", via, rule, ref)))
    if rel_at is not None:
        a = Anchor(
            "launch", rel_at, "hour", "first_release", "github_release",
            "undeclared:first_release", rel_tag,
        )  # fmt: skip
        options.append((rel_at, 1, a))
    if not options:
        return None, "no_launch_event", []
    t, _k, a = min(options, key=lambda o: (o[0], o[1]))
    if t < start:
        return None, "undeclared:before_window", []
    if t > end:
        return None, "undeclared:after_window", []
    return a, None, []


def view_b_anchor(
    c: Candidate,
    start: datetime,
    end: datetime,
    drop: set[tuple[str, Any]] | frozenset[tuple[str, Any]] = frozenset(),
    *,
    required: Sequence[str] = (),
) -> tuple[Anchor | None, str | None, list[dict[str, Any]]]:
    """View B's anchor of a candidate from its stored launch signals (`choose_launch_anchor`).
    `required`: the launch sources the pre-registered parameters apply (ADR-085); when one's
    data for this repo is missing or incomplete, there is no anchor (reason
    `launch_source_incomplete:<source>`). A declared launch event before the window's start
    comes first (ADR-085 addendum 3): no anchor, reason `launched_before_window`, and no
    relaunches (an unread source could only hold an even earlier event, so it can't change
    this)."""
    if launch_events_before(c, start, drop):
        return None, PRE_WINDOW_REASON, []
    if (src := incomplete_source(c, required)) is not None:
        return None, f"{INCOMPLETE_PREFIX}{src}", []
    return choose_launch_anchor(
        launch_events(c, start, end, drop),
        _signal(c, MENTION_SOURCE),
        first_release_at(c),
        start,
        end,
    )


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
                if s.get("match") == "title" and s.get("confirmed") is not True:
                    continue  # never a launch (ADR-083 E), so it claims nothing
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
    per HN item id (the lookup's record wins: fresher points, and it says how it matched). A
    title match counts only when it is confirmed and the repo has no URL-matched launch in the
    window (ADR-083 E; checked here again, so records of any origin obey it). In time order,
    then item id (§2.2 rule 5)."""
    by_item: dict[int, Launch] = {}
    for s in c.sources:
        src = s.get("source")
        if src not in ("show_hn", LAUNCH_LOOKUP_SOURCE) or not s.get("time"):
            continue
        item = int(s.get("hn_item_id") or 0)
        if src == LAUNCH_LOOKUP_SOURCE and (not _current_lookup(s) or (c.ref, item) in drop):
            continue  # a record of an earlier anchor rule, or an ambiguous title match
        title = src == LAUNCH_LOOKUP_SOURCE and s.get("match") == "title"
        if title and s.get("confirmed") is not True:
            continue  # an unconfirmed title-only match (ADR-083 E)
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
    if any(x.via != "lookup:title" for x in by_item.values()):  # a URL-matched launch exists
        by_item = {k: x for k, x in by_item.items() if x.via != "lookup:title"}
    return sorted(by_item.values(), key=lambda x: (x.at, x.item_id))


def _discovery_launches(c: Candidate, start: datetime, end: datetime) -> list[int]:
    """Item ids of the Show HN posts discovery linked to the repo by URL, inside the window."""
    out = []
    for s in c.sources:
        if s.get("source") == "show_hn" and s.get("time") and start <= _t(s["time"]) <= end:
            out.append(int(s.get("hn_item_id") or 0))
    return out


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


# The code whose behaviour is the anchor rule (ADR-082 guard, extended by ADR-083): the lookup
# loop, the matching of lookup hits, the GitHub-URL normalizer, the title confirmation (E rule
# and the Haiku check), the merge of launches, the ambiguity drop and `choose_anchor` with its
# comparisons, and the star windows the views read (launch size, follow-through, stars before
# launch, view A's fit), plus the constants they read. Names without a module are in this
# module; `module:name` elsewhere. Their hash is pinned next to `ANCHOR_RULE_VERSION`
# (`selection.ANCHOR_RULE_SOURCE_SHA256`) and, since selection-v5, is part of the
# pre-registered parameters (`Context.params()["anchor_rule_source_sha256"]`).
ANCHOR_RULE_FUNCTIONS = (
    "lookup_launches",
    "_lookup_repo",
    "_discovery_launches",
    "has_declared_launch",
    "creation_pages",
    "pigtail.connectors.hn:normalize_github_repo",
    "pigtail.briefs.confirm:url_domain",
    "pigtail.briefs.confirm:_owner",
    "pigtail.briefs.confirm:_norm",
    "pigtail.briefs.confirm:keywords",
    "pigtail.briefs.confirm:owner_named",
    "pigtail.briefs.confirm:owner_overlaps_name",
    "pigtail.briefs.confirm:_repo_name_parts",
    "pigtail.briefs.confirm:confirm_by_rules",
    "pigtail.briefs.confirm:haiku_input",
    "pigtail.briefs.confirm:Confirmer",
    "pigtail.briefs.confirm:_outcome",
    "pigtail.briefs.confirm:_fail",
    "pigtail.briefs.selection:follow_through_fit",
    "pigtail.briefs.selection:_ln1p0",
    "pigtail.briefs.selection:_pending_or_unknown",
    "star_window",
    "stars_before_launch",
    "first_day",
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
    # view B's launch-event anchor (ADR-084)
    "release_is_launch",
    "fetch_releases",
    "first_mention",
    "_fetch_all_releases",
    "_search_all_mentions",
    "_signal",
    "launch_events",
    "first_release_at",
    "choose_launch_anchor",
    "view_b_anchor",
    "pigtail.connectors.github:parse_releases",
    "pigtail.connectors.hn:parse_mention_page",
    "pigtail.connectors.hn:HNShowDiscoveryConnector",  # the lookup and mention searches
    "pigtail.connectors.hn:github_repos_in_text",
    "pigtail.briefs.selection:CaseInput",
    "pigtail.briefs.selection:anchor_rule",
    "pigtail.briefs.selection:in_population",
    # Product Hunt and declared maintainers' Bluesky posts (ADR-085)
    "ph_launches",
    "launch_events_before",
    "_before",
    "incomplete_source",
    "ambiguous_ph_posts",
    "ph_values",
    "pigtail.briefs.launch_sources:ph_blocked",
    "pigtail.briefs.launch_sources:bsky_blocked",
    "pigtail.briefs.launch_sources:ph_name_key",
    "pigtail.briefs.launch_sources:ph_repo_key",
    "pigtail.briefs.launch_sources:ph_slug_candidates",
    "pigtail.briefs.launch_sources:ph_urls_only",
    "pigtail.briefs.launch_sources:run_product_hunt",
    # the shared Product Hunt topic cache (ADR-085 addendum 4)
    "pigtail.briefs.ph_cache:ScanRow",
    "pigtail.briefs.ph_cache:gaps",
    "pigtail.briefs.ph_cache:month_intervals",
    "pigtail.briefs.ph_cache:ph_key_hash",
    "pigtail.briefs.ph_cache:usable_scans",
    "pigtail.briefs.ph_cache:start_scan",
    "pigtail.briefs.ph_cache:save_page",
    "pigtail.briefs.ph_cache:end_scan",
    "pigtail.briefs.ph_cache:window_posts",
    "pigtail.briefs.launch_sources:ph_confirmer",
    "pigtail.briefs.launch_sources:_norm_url",
    "pigtail.briefs.launch_sources:links_to",
    "pigtail.briefs.launch_sources:homepage_search_url",
    "pigtail.briefs.launch_sources:default_readme",
    "pigtail.briefs.launch_sources:run_bluesky",
    "pigtail.briefs.launch_sources:bsky_post_is_launch",
    "pigtail.briefs.launch_sources:declared_account_sources",
    "pigtail.briefs.confirm:domain_in_text",
    "pigtail.briefs.confirm:ph_confirm_by_rules",
    "pigtail.briefs.confirm:ph_haiku_input",
    "pigtail.briefs.selection:launch_source_settings",
    "pigtail.briefs.selection:_flag",
    "pigtail.connectors.producthunt:PHPost",
    "pigtail.connectors.producthunt:parse_post_node",
    "pigtail.connectors.producthunt:parse_post",
    "pigtail.connectors.producthunt:parse_posts_page",
    "pigtail.connectors.producthunt:ProductHuntConnector",
    "pigtail.connectors.bluesky:account_id",
    "pigtail.connectors.bluesky:declared_accounts",
    "pigtail.connectors.bluesky:_links",
    "pigtail.connectors.bluesky:parse_search_page",
    "pigtail.connectors.bluesky:BlueskySearchConnector",
    "pigtail.connectors.github:parse_repo_links",
    "pigtail.connectors.github:parse_social_accounts",
    "pigtail.connectors.github:parse_org_profile",
)
ANCHOR_RULE_CONSTANTS = (
    "TITLE_MIN_CHARS",
    "CREATION_TOLERANCE",
    "TITLE_STOPLIST",
    "_SLOT_PREFIX",
    "_SLOT_END",
    "_NAME_SEP",
    "LAUNCH_LOOKUP_SOURCE",
    "SETTLE_LAG_DAYS",
    "LOOKUP_GROUP",
    "STAR_MAX_PAGES",
    "STAR_WEEKS_PER_PAGE",
    "pigtail.connectors.hn:_GH_NOT_OWNER",
    "pigtail.connectors.hn:_GH_OWNER",
    "pigtail.connectors.hn:_GH_REPO",
    "pigtail.briefs.confirm:CONFIRMATION_VERSION",
    "pigtail.briefs.confirm:OWNER_MIN_CHARS",
    "pigtail.briefs.confirm:KEYWORD_MIN_CHARS",
    "pigtail.briefs.confirm:KEYWORD_MIN_SHARED",
    "pigtail.briefs.confirm:SHARED_HOSTS",
    "pigtail.briefs.confirm:KEYWORD_STOPWORDS",
    "pigtail.briefs.confirm:_YC_BATCH",
    "pigtail.briefs.confirm:_TOKEN",
    "pigtail.briefs.confirm:_URL_TOKEN",
    "pigtail.briefs.confirm:JOB",
    "pigtail.briefs.confirm:NAMESPACE",
    "pigtail.briefs.confirm:SYSTEM",
    "pigtail.briefs.confirm:TEMPLATE",
    "pigtail.briefs.selection:LAUNCH_DAYS",
    "pigtail.briefs.selection:FOLLOW_DAYS",
    "pigtail.briefs.selection:FIT_EPS",
    "pigtail.briefs.selection:FT_ROUND",
    "pigtail.briefs.selection:MIN_POPULATION",
    "RELEASE_SOURCE",
    "RELEASE_MAX_PAGES",
    "RELEASE_TEXT_CHARS",
    "MENTION_SOURCE",
    "MENTION_MAX_REQUESTS",
    "MENTION_HITS_CAP",
    "pigtail.briefs.selection:RELEASE_LAUNCH_PATTERN",
    "pigtail.briefs.selection:DECLARED_RULES",
    "pigtail.connectors.github:RELEASES_PER_PAGE",
    "pigtail.connectors.github:RELEASE_BODY_CHARS",
    "pigtail.connectors.hn:MENTION_FIELDS",
    "pigtail.connectors.hn:_GH_URL_IN_TEXT",
    # ADR-085
    "pigtail.briefs.launch_sources:PH_SOURCE",
    "pigtail.briefs.launch_sources:PH_SLUG_CANDIDATES",
    "pigtail.briefs.launch_sources:PH_TOPIC_MAX_PAGES",
    "pigtail.briefs.launch_sources:PH_GROUP",
    "pigtail.briefs.launch_sources:PH_INCOMPLETE_MAX_SHARE",
    "pigtail.briefs.ph_cache:PH_TOPIC_CACHE_RULE",
    "pigtail.briefs.ph_cache:PH_TOPIC_CACHE_MAX_AGE_DAYS",
    "pigtail.briefs.ph_cache:GAP_RULE",
    "pigtail.briefs.ph_cache:INDEX_RULE",
    "pigtail.connectors.producthunt:TOPIC_FIELDS",
    "pigtail.briefs.launch_sources:BSKY_SOURCE",
    "pigtail.briefs.launch_sources:BSKY_KIND",
    "pigtail.briefs.launch_sources:BSKY_ROLE",
    "pigtail.briefs.launch_sources:BSKY_MAX_ACCOUNTS",
    "pigtail.briefs.launch_sources:BSKY_MAX_PAGES",
    "pigtail.briefs.launch_sources:BSKY_INCOMPLETE_MAX_SHARE",
    "pigtail.briefs.launch_sources:README_CHARS",
    "pigtail.briefs.launch_sources:README_MAX_ACCOUNTS",
    "pigtail.briefs.selection:PRE_WINDOW_REASON",
    "EARLIEST",
    "LATEST",
    "LAUNCH_LOOKUP_HITS",
    "pigtail.briefs.confirm:PH_CONFIRMATION_VERSION",
    "pigtail.briefs.confirm:PH_JOB",
    "pigtail.briefs.confirm:PH_NAMESPACE",
    "pigtail.briefs.confirm:PH_TEXT_CHARS",
    "pigtail.briefs.confirm:PH_SYSTEM",
    "pigtail.briefs.confirm:PH_TEMPLATE",
    "pigtail.briefs.selection:PH_TOPICS",
    "pigtail.briefs.selection:INCOMPLETE_PREFIX",
    "pigtail.connectors.producthunt:POST_FIELDS",
    "pigtail.connectors.producthunt:SLUG_QUERY",
    "pigtail.connectors.producthunt:ID_QUERY",
    "pigtail.connectors.producthunt:TOPIC_QUERY",
    "pigtail.connectors.producthunt:PH_PAGE",
    "pigtail.connectors.producthunt:RESERVE_FRACTION",
    "pigtail.connectors.producthunt:DESCRIPTION_CHARS",
    "pigtail.connectors.bluesky:SEARCH_PATH",
    "pigtail.connectors.bluesky:RESOLVE_PATH",
    "pigtail.connectors.bluesky:DEFAULT_BSKY_BASE",
    "pigtail.connectors.bluesky:BSKY_QUERY",
    "pigtail.connectors.bluesky:BSKY_PAGE",
    "pigtail.connectors.bluesky:SEARCH_PARAMS",
    "pigtail.connectors.bluesky:_HANDLE",
    "pigtail.connectors.bluesky:_DID",
    "pigtail.connectors.bluesky:_PROFILE",
    "pigtail.connectors.bluesky:_AT_HANDLE",
    "pigtail.connectors.bluesky:_RESERVED",
    "pigtail.connectors.bluesky:_URL_IN_TEXT",
    "pigtail.connectors.github:LINKS_FIELDS",
)


def _guarded(name: str) -> Any:
    """The object a guard entry names (`name` in this module, or `module:name`)."""
    import importlib

    if ":" in name:
        mod, attr = name.split(":", 1)
        return getattr(importlib.import_module(mod), attr)
    return globals()[name]


def anchor_rule_source_sha256() -> str:
    """SHA-256 of the anchor rule's code (`ANCHOR_RULE_FUNCTIONS`, parsed, docstrings dropped,
    so comments, docstrings and formatting don't count) and constants (`ANCHOR_RULE_CONSTANTS`,
    sorted where they are sets). Pinned as `selection.ANCHOR_RULE_SOURCE_SHA256` (ADR-082) and
    part of the pre-registered selection parameters (ADR-083)."""
    import ast
    import hashlib
    import inspect
    import textwrap

    parts: list[str] = []
    for name in ANCHOR_RULE_FUNCTIONS:
        tree = ast.parse(textwrap.dedent(inspect.getsource(_guarded(name))))
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
        v = _guarded(name)
        parts.append(f"{name}={sorted(v) if isinstance(v, frozenset | set) else v!r}")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def star_value(series: Mapping[date, int], first: date, k: int, as_of: date) -> Value:
    """`att.stars@k` (outcome-model §1.2): raw net stars over k endpoint days."""
    return star_window(series, first, 0, k, as_of)


def star_window(series: Mapping[date, int], first: date, lo: int, hi: int, as_of: date) -> Value:
    """Raw net stars on the endpoint days with index `lo <= i < hi` from the anchor window's
    first day (§1.2 day mapping; ADR-083: launch size `[0, 3)`, follow-through `[3, 30)`).
    `pending` until `first + hi + settle_lag` (the horizon rule of `att.stars@k`, with k = hi),
    `unknown` without a series or with a missing day; labelled "unfiltered, anomaly-checked"."""
    if as_of < first + timedelta(days=hi + SETTLE_LAG_DAYS):
        return Value("pending", reason="horizon_not_reached", tag="unknown")
    days = [first + timedelta(days=i) for i in range(lo, hi)]
    if not series:
        return Value("unknown", reason="no_star_history")
    if any(d not in series for d in days):
        return Value("unknown", reason="incomplete_series")
    return Value("observed", float(sum(series[d] for d in days)), "verified", STAR_LABEL)


def stars_before_launch(
    series: Mapping[date, int], created: datetime | None, first: date
) -> tuple[int | None, str | None]:
    """(net stars, None) on the endpoint days from the repo's creation day `D(created)` (§1.2
    day mapping) to the day before the anchor window's first day, or (None, reason): no
    creation date, no series, a series that doesn't reach the creation day (older pages not
    fetched), or a missing day in between (ADR-083, view B). A launch on or before the
    creation day has 0 stars before it."""
    if created is None:
        return None, "no_creation_date"
    if not series:
        return None, "no_star_history"
    day0 = endpoint_day(created)
    if day0 >= first:
        return 0, None
    if min(series) > day0:
        return None, "series_does_not_reach_creation"
    n = (first - day0).days
    days = [day0 + timedelta(days=i) for i in range(n)]
    if any(d not in series for d in days):
        return None, "incomplete_series"
    return sum(series[d] for d in days), None


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
    launch_sources: Sequence[str] = (),
) -> list[CaseInput]:
    """Step 2 (module docstring). `launch_sources`: the view-B sources the pre-registered
    parameters apply (ADR-085; a repo whose data from one is incomplete has no view-B anchor).
    Each case also carries the same repo as view B reads it
    (`CaseInput.launch_case`, ADR-084): anchored on its launch-event anchor
    (`view_b_anchor`, from launch events only, never star data), with its values, covariates
    and anomaly flags relative to that anchor, and its relaunch events. Both carry the
    distribution surface coded before any outcome (`surface.surface_of`)."""
    from pigtail.briefs.surface import surface_of

    definition = Definition.from_brief(brief)
    k_max = _star_horizon_max(definition)
    start, end = window
    prepared: list[dict[str, Any]] = []
    drop: set[tuple[str, Any]] = {*ambiguous_title_matches(cands), *ambiguous_ph_posts(cands)}
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
        # view B (ADR-084): launch events only; the star series is not an input
        b_anchor, b_reason, relaunches = view_b_anchor(c, start, end, drop, required=launch_sources)
        before = launch_events_before(c, start, drop) if b_reason == PRE_WINDOW_REASON else []
        prepared.append(
            {
                "c": c,
                "series": series,
                "created": created,
                "launches": launches,
                "anchor": anchor,
                "reason": reason,
                "b_anchor": b_anchor,
                "b_reason": b_reason,
                "relaunches": relaunches,
                "pre_window": before[0].to_dict() if before else None,
                # every confirmed post, any date: the votes come from the one in the anchor's
                # days 0..2 (`ph_values`), which may lie past the window's end
                "ph": ph_launches(c, EARLIEST, LATEST, drop)
                if "product_hunt" in launch_sources
                else None,
            }
        )

    a_reports, a_windows = _anomaly(conn, prepared, "anchor", k_max)
    b_reports, b_windows = _anomaly(conn, prepared, "b_anchor", k_max)
    out: list[CaseInput] = []
    for p in prepared:
        c = p["c"]
        surface, paths = surface_of(c)
        b_case = _case_input(
            p, p["b_anchor"], p["b_reason"], b_reports, b_windows, as_of, surface, paths
        )
        b_case = replace(
            b_case, relaunch_events=tuple(p["relaunches"]), pre_window_launch=p["pre_window"]
        )
        a_case = _case_input(
            p, p["anchor"], p["reason"], a_reports, a_windows, as_of, surface, paths
        )
        out.append(replace(a_case, launch_case=b_case))
    return out


def _anomaly(
    conn: psycopg.Connection[Any], prepared: Sequence[Mapping[str, Any]], key: str, k_max: int
) -> tuple[dict[str, Any], dict[str, tuple[date, date, list[str]]]]:
    """Anomaly checks (§4.2) over the field and reference candidates anchored by `key` (the
    §2.2 anchor, or view B's), on the endpoint days `[T - 60 d, T + k_max)`."""
    anomaly_in: dict[str, tuple[dict[date, int], dict[str, dict[date, int]]]] = {}
    windows: dict[str, tuple[date, date, list[str]]] = {}
    for p in prepared:
        c, a = p["c"], p[key]
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
        windows[c.ref] = (w0, w1, sorted(anomaly_in[c.ref][1]))
    return check_population(anomaly_in), windows


def _case_input(
    p: Mapping[str, Any],
    a: Anchor | None,
    reason: str | None,
    reports: Mapping[str, Any],
    windows: Mapping[str, tuple[date, date, list[str]]],
    as_of: date,
    surface: str,
    install_paths: tuple[str, ...],
) -> CaseInput:
    """One case's values, covariates and anomaly flag relative to the anchor `a`."""
    c, series = p["c"], p["series"]
    values: dict[str, Value] = {}
    business = dict.fromkeys(BUSINESS_SIGNALS, NO_CONNECTOR)
    cov = Covariates(
        language=c.metadata.get("language"), surface=surface, install_paths=install_paths
    )
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
        # the views' star windows (ADR-083): launch size (days 0..2, the LSM days) and
        # follow-through (days 3..29); Reddit reach has no connector (TM-05 is a GAP)
        values[LAUNCH_SIZE] = star_window(series, f, *LAUNCH_DAYS, as_of)
        values[FOLLOW_STARS] = star_window(series, f, *FOLLOW_DAYS, as_of)
        values[REDDIT_REACH] = NO_CONNECTOR
        pre, pre_why = stars_before_launch(series, _created_at(c), f)
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
        values[PH_VOTES], values[PH_COMMENTS] = ph_values(p.get("ph"), f)
        lsm = None
        launch_days = [f + DAY * i for i in range(*LAUNCH_DAYS)]
        if all(d in series for d in launch_days):
            lsm = math.log10(1 + max(0, sum(series[d] for d in launch_days)))
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
            prelaunch_stars=pre,
            prelaunch_log=None if pre is None else math.log10(1 + max(0, pre)),
            prelaunch_reason=pre_why,
            surface=surface,
            install_paths=install_paths,
        )
        rep = reports.get(c.ref)
        if rep is not None:
            w0, w1, channels = windows[c.ref]
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
                    {"kind": fl.kind, "start": _iso(fl.start), "end": _iso(fl.end)} for fl in flags
                ],
                "spikes_in_window": len(spikes),
                "ratio": None if rep.ratio is None else round(rep.ratio, 4),
                "stars_total": rep.stars_total,
                "window": [w0.isoformat(), w1.isoformat()],
                "channels": channels,
            }
        elif c.panel != "exemplar":
            anomaly = {"label": STAR_LABEL, "status": "no_star_history_for_window"}
    return CaseInput(
        ref=c.ref,
        panel=c.panel,
        distance=c.distance if c.distance is not None else 0,
        named_index=c.named_index,
        anchor=a,
        anchor_reason=reason,
        values=values,
        business=business,
        covariates=cov,
        star_anomaly_flag=flag,
        anomaly=anomaly,
    )


def _iso(d: date | None) -> str | None:
    return None if d is None else d.isoformat()


def ph_values(
    posts: Sequence[tuple[datetime, str, str, int | None, int | None]] | None, first: date
) -> tuple[Value, Value]:
    """View B's secondary launch-size measures from Product Hunt (ADR-085 as amended by its
    addendum 3; reported, never ranked on): the votes and comments, as of fetch, of the
    confirmed post whose launch time falls in the launch-size window, the endpoint days
    `LAUNCH_DAYS` (0..2) from the anchor's first day `first` (the same day mapping as launch
    size: `endpoint_day`, US Pacific), with the most votes (earliest on a tie). None there:
    `unknown` (`no_ph_post_in_launch_window`). `posts` None: the source is off."""
    if posts is None:
        no = Value("unknown", reason="product_hunt_not_collected")
        return no, no
    lo, hi = first + DAY * LAUNCH_DAYS[0], first + DAY * (LAUNCH_DAYS[1] - 1)
    cand = [x for x in posts if lo <= endpoint_day(x[0]) <= hi and x[3] is not None]
    if not cand:
        no = Value("unknown", reason="no_ph_post_in_launch_window")
        return no, no
    best = max(cand, key=lambda x: (x[3] or 0, -x[0].timestamp()))
    votes = Value("observed", float(best[3] or 0), "verified", "as of fetch")
    comments = (
        Value("observed", float(best[4]), "verified", "as of fetch")
        if best[4] is not None
        else Value("unknown", reason="no_comment_count")
    )
    return votes, comments
