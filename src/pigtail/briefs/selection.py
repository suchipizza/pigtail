"""Outcome sort, winners and matched losers, balance diagnostics and the sensitivity check of a
brief version's final shortlist (PRD R4.3, R4.8, R4.9, R4.10, R4.11, R18.8, §8.2, §9.2;
outcome-model v2.1 §3, §5, §6, §8; ADR-053.2, ADR-054, ADR-055.5, ADR-057, ADR-077).

Pure functions over `CaseInput`s (one per shortlisted repo): no database, no network, no clock.
`pigtail.briefs.outcomes` builds the inputs from stored data and `pigtail.briefs.selection_store`
persists the result. Everything here is deterministic for a given brief version and input set
(R4.8): cases are processed in `candidate_ref` order whatever order they arrive in, and every tie
is broken by `sha256(brief_id:brief_version:candidate_ref)` (outcome-model §5.4; the "seed" is
the brief id and version). `Selection.result_hash` is the SHA-256 of the canonical result.

**Outcome sort** (§3, §5.3–5.4, R18.8). The reference population is the final shortlist's field
and reference-case repos with an anchor (exemplars are outside the field, ADR-057.1). Each
metric's mid-rank percentile is computed over the population's `observed` values (per `group`,
e.g. the downloads ecosystem), and is `unknown` below `MIN_POPULATION` (20). A candidate
qualifies when it has an anchor and meets every threshold: a percentile floor is met only by an
observed value > 0 (zero floor) at or above the floor; `unknown` / `pending` never meet one and
make the candidate *undetermined*, not a loser; `not_applicable` fails unless the brief skips it
(`if_not_applicable`, `fallbacks.no_measurable_adoption`). Qualifiers are ranked by the primary
dimension's percentile (or the advanced weighted composite, §6); a qualifier without a rankable
value is *unrankable*.

**Selection** (§5.5–5.6, R4.3, R4.10, ADR-053.2, ADR-054, ADR-077). The top `panel.winners`
ranked qualifiers of the eligible panel are the winners. The loser pool is the eligible
candidates that fail a threshold on an observed value. Winners in rank order take an unused
loser (without replacement; more rounds while fewer than `panel.losers` are matched) among those
with the same `panel.exact_match` keys (founder audience bucket, launch half-year; `unknown` is
its own level) inside the calipers (`|ΔLSM| <= 0.5 SD`, `|Δquarter| <= 1`): one whose pair
passes the headline rule first, then the nearest (ADR-078; `match_losers`). The
panel starts at the core field (distance 0). If it has fewer than 15 winners or matched losers,
it widens one declared widening step at a time (R4.10); then, if fewer than
`fallbacks.too_few_winners.min_winners` rankable qualifiers remain, the brief's fallback steps
are applied in order (ADR-053.2, ADR-055.5). Every step is logged with its counts. Named
reference cases are always studied (`is_reference`, R4.11) whatever their role. Distribution
exemplars get `losers_per_exemplar` losers each, exactly matched on `match_on` and never on
field (ADR-057.1).

**Balance** (§5.6, §9.2, ADR-054.1): SMD per covariate (pooled-SD form; per level for language)
and the variance ratio, before and after matching, the exact-match check, and the pairs whose
standardized difference on any covariate exceeds `headline_exclusion_smd` (excluded from
headline patterns). After matching, each matched winner is counted once, even when it has two
losers (unweighted; ADR-078). No p-values; no re-matching.

**Sensitivity** (§8, R4.9): the winner set is recomputed under each listed alternative (primary
swap, band shift, weights, and D, exclude anomaly-flagged candidates, which the brief schema
still calls `fake_star_filter`), with the same N and eligible panel and without re-matching;
the Jaccard overlap and each baseline winner's and matched loser's status are reported, with the
flags `definition_sensitive`, `sensitive_to_star_anomaly` and `excluded_anomaly_flagged`.

**Views (selection-v5, ADR-083; owner decision 2026-09-27).** Under attention primary, launch
size (LSM, the matching caliper) and `att.stars@30` move together, so the 0.5 SD LSM caliper
left almost no matchable losers. `select_views` therefore runs the selection twice, and adds a
descriptive context view; the brief's success definition is unchanged, and each view reads
"attention" in its own way (selection-version semantics, not a brief change):

- **A, follow-through** (`VIEW_FOLLOW_THROUGH`): attention is follow-through relative to launch
  size, the residual of `ln(1 + stars on endpoint days 3..29)` from the closed-form OLS line on
  `ln(1 + stars on days 0..2)` fitted on the view's population (`follow_through_fit`; no fit,
  so no percentiles, below `MIN_POPULATION` or with zero variance). Population: every anchored
  field and reference case. Matching as before (LSM caliper 0.5 SD, exact keys, quarter).
  Sensitivity adds the log-ratio and plain `att.stars@30`.
- **B, launch** (`VIEW_LAUNCH`): attention is launch size (stars on days 0..2). Population:
  launch-anchored cases only (a burst anchor is defined by star velocity, i.e. by launch size:
  including it would select on the outcome); since selection-v6 view B reads its own
  launch-event anchor and declared launches only (below). Matching only on characteristics
  that existed before launch: the exact keys, quarter, repo age and stars before launch
  (calipers 0.5 SD), language and category (core vs adjacent field) as distance terms; launch
  size is neither a matching key nor a headline covariate.
- **C, context** (`context_view`): per view, the winners against the whole loser pool without
  matching, labelled "context, not a headline".

`select` with the default `VIEW_PLAIN` keeps the selection-v4 behaviour (the brief's attention
metric, every anchored case, LSM matching), for comparison and for the older tests only.

**selection-v6 (ADR-084; owner decisions 2026-09-27 after verifier round 6).**

- **Headline exclusion on numeric differences only** (owner option 1): a pair is left out of
  the headline patterns only when its standardized difference exceeds `headline_exclusion_smd`
  on a numeric covariate of its view (LSM and repo age in view A; repo age and stars before
  launch in view B; audience band and quarter when not exact-matched). Language, language
  group, category, distribution surface and install paths stay in the balance table (SMD per
  level, `balance_limited` when they miss the target) and never exclude a pair.
- **Language groups** (`language_group`, fixed): `js_ts` (JavaScript, TypeScript), `python`,
  `go`, `rust`, `other` (anything else, a missing language included), from GitHub's primary
  language names exactly. Each pair stores `same_language_group`; each headline view reports
  the sensitivity alternative "same-language-group pairs only" (its headline pairs, counts and
  balance on that subset). The later pattern step compares every headline pattern on all
  headline pairs against this subset (`PATTERN_LANGUAGE_RULE`).
- **Distribution surface** (`pigtail.briefs.surface`, coded before any outcome): `surface` and
  `install_path` are balance rows next to language; never a matching key or an exclusion.
- **View B's anchor** is its own (`CaseInput.launch_case`, built by
  `outcomes.choose_launch_anchor` from launch events only, never star data): the earliest
  maintainer-initiated launch event in the window (Show HN / Launch HN, or a GitHub release
  worded as a launch); later ones are relaunch events. A repo without one is anchored on the
  earlier of its first external mention (HN) and its first public release (GitHub), flagged
  `undeclared_launch` and reported in its own sub-population (`VIEW_LAUNCH_UNDECLARED`), not in
  the declared-launch headline. Burst anchors are never used in view B.
- **Calipers on log10 scale with the SD from the full shortlisted pool**: the SD of each
  covariate is computed over every shortlisted field and reference repo with an observed
  value (not just the view's winners and pool), at sort time, and stored with the selection.
- **View C** compares the winners with every shortlisted non-winner of the view (no-anchor and
  undetermined repos included), with counts of those with and without a value.
"""

from __future__ import annotations

import bisect
import hashlib
import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Literal

from pigtail.analysis.params import PARAMS_VERSION
from pigtail.briefs.model import DIMENSIONS, RANKABLE, THRESHOLD_PERCENTILE, Brief, sha256_json

# v2: headline-first matching, missing language (ADR-078); v3: anchor rule anchor-v2 (ADR-081);
# v4: anchor rule anchor-v3, the conservative title rule and the launch_hn tag (ADR-082);
# v5: views A (follow-through), B (launch) and C (context); anchor-v4 (ADR-083)
# v6: numeric-only headline exclusion, language groups, distribution surface, view B's own
#     launch-event anchor with the undeclared sub-population, full-pool SDs, view C over all
#     non-winners; anchor-v5, confirm-v2 (ADR-084)
# v7: view B's launch events add Product Hunt launches and declared maintainers' Bluesky posts,
#     with the incomplete-source rule and the launch-source flags; anchor-v7 (ADR-085)
# v9: Bluesky posts need launch wording, the README declares only one account, a declared launch
#     before the window leaves no view-B anchor, PH votes/comments from days 0-2 only; anchor-v8
#     (ADR-085 addendum 3)
SELECTION_VERSION = "selection-v9"
# view A's metric: follow-through relative to launch size (ADR-083)
FOLLOW_THROUGH_METRIC_VERSION = "follow-through-v1"
OUTCOME_MODEL_VERSION = "2.1"
MIN_POPULATION = 20  # outcome-model §3 (v0 design choice; the brief schema has no field yet, O17)
WINNERS_MIN = 15  # R4.8 range floor: below it the report says "fewer winners than the minimum"
LOSERS_MIN = 15
MAX_WIDENING = 2  # distances 0-2 (the relevance filter labels at most two widening steps)
LADDER: tuple[float | None, ...] = (None, 25.0, 50.0, 75.0, 90.0, 95.0)  # §8.1 band ladder
LSM_CALIPER_SD = 0.5
QUARTER_CALIPER = 1
# view B's calipers on pre-launch characteristics (ADR-083): the same width as the LSM caliper
AGE_CALIPER_SD = 0.5
PRELAUNCH_CALIPER_SD = 0.5
BUSINESS_SIGNALS = ("pricing_page", "hiring_hn_posts", "careers_roles")
BUSINESS_COUNT = "biz.verified_signal_count@365"
BAND_ORDINAL: dict[str, int] = {"none": 0, "r1": 1, "r2": 2, "r3": 3, "r4": 4}
TIE_BREAK = "sha256(brief_id:brief_version:candidate_ref) ascending"
MATCHING_RULE = (
    "round 1: each winner in rank order takes an eligible loser (exact match, calipers), "
    "headline-passing first, then nearest; later rounds: headline-passing losers first, then any "
    "eligible loser (ADR-078)"
)
# The anchor rule (outcome-model §2.2; `pigtail.briefs.outcomes.choose_anchor` and the launch
# lookup) is part of the pre-registered selection parameters (ADR-081): bump the version whenever
# either changes, so an existing pre-registration no longer passes the gate. The guard test
# (`tests/unit/test_m22_round4.py::test_anchor_rule_source_is_pinned`) hashes the code of the
# anchor and matching functions (`outcomes.anchor_rule_source_sha256`) and fails until
# `ANCHOR_RULE_SOURCE_SHA256` is updated together with a new `ANCHOR_RULE_VERSION` (ADR-082).
# Since selection-v5 the live hash is also part of `Context.params()` (ADR-083), so any change to
# the guarded code changes `selection_params_sha256` and an existing pre-registration stops
# passing the gate by itself; the pinned constant makes the developer bump the versions too.
# v5: view B's launch-event anchor (ADR-084); v6: Product Hunt and Bluesky launch events (ADR-085)
# v8: the owner decisions after verifier round 7 (ADR-085 addendum 3)
ANCHOR_RULE_VERSION = "anchor-v8"
ANCHOR_RULE_SOURCE_SHA256 = "982418244b2b5aedbf8f3ad58f61d2f7c1c3048a95689c0a69442c3efe940202"
ANCHOR_RULE = (
    "outcome-model §2.2 rules 1-6 as read by ADR-077.3; declared launches = Show HN or Launch HN "
    "posts from discovery and the per-repo launch lookup (current rule's records only), merged "
    "by item id; a title-only lookup match counts only when the repo has no URL-matched launch "
    "in the window (discovery or lookup) and it is confirmed (ADR-083 E); a launch is compared "
    "with a day-precision burst onset on the onset's endpoint day (US Pacific, §1.2), so a "
    "launch on the onset day precedes the burst (ADR-081, ADR-082, ADR-083)"
)
LAUNCH_LOOKUP = (
    "HN Algolia per shortlisted repo, inside the brief's window: tags=show_hn search for "
    "'github.com/<owner>/<name>' and for '<name>', tags=launch_hn search for '<name>', then the "
    "same three searches from HN's epoch to the window's start (view B's pre-window rule, "
    "ADR-085 addendum 3; before-window posts are stored, never anchor view A); the "
    "lookup must run (the selection is refused without the Show HN connector); a hit is "
    "accepted by URL when it links the repo's github.com/owner/name (case-insensitive), or by "
    "title only when all hold: (a) the title matches '^(Show|Launch) HN:\\s*<name>' followed by "
    "the end of the title or a separator (en dash, em dash, ' -', ':', ',', '(', '|'), "
    "case-insensitive, with '-', '_' and spaces in the GitHub name equivalent; (b) the post "
    "links no GitHub repo; (c) it was posted no earlier than 1 day before the repo's creation "
    "(unknown creation date: rejected); (d) the name has >= 5 characters and is not made only "
    "of stop-list words (common English or generic tech words, outcomes.TITLE_STOPLIST); (e) "
    "a title match claimed by two shortlisted repos, or linked by URL to another, is dropped; "
    "rejected title-only candidates are counted, not stored; stored: item id, time, points, "
    "kind, match, rule (ADR-082); a title match that passes (a)-(e) is then confirmed or "
    "excluded by the ADR-083 E rule (`confirm.CONFIRMATION_RULE`), and the confirmation method "
    "(or the reason it was not confirmed) is stored with it"
)
ROUND = 6

# --- view B's anchor (ADR-084; owner decision (2) of 2026-09-27) ---------------------------------
# rule labels, in the order a tie between launch events at the same instant is broken (ADR-085):
# the launch venues first (Show HN, Launch HN, Product Hunt), then a release worded as a launch,
# then a declared maintainer's Bluesky post (a post often announces one of the others)
DECLARED_RULES = (
    "show_hn",
    "launch_hn",
    "product_hunt",
    "release_launch",
    "bluesky_maintainer_post",
)
UNDECLARED_RULES = ("undeclared:first_mention", "undeclared:first_release")
ANCHOR_RULE_LABELS = (*DECLARED_RULES, *UNDECLARED_RULES, "none")
# a declared launch before the window (ADR-085 addendum 3): no view-B anchor (rule none, reason
# `launched_before_window`); the counts break these cases down by the kind of that first event
# (`launched_before_window:<kind>`, a part of `none`)
PRE_WINDOW_REASON = "launched_before_window"
PRE_WINDOW_LABELS = tuple(f"{PRE_WINDOW_REASON}:{k}" for k in DECLARED_RULES)
ANCHOR_COUNT_LABELS = (*ANCHOR_RULE_LABELS, *PRE_WINDOW_LABELS)
RELEASE_LAUNCH_PATTERN = r"\b(?:launch|launching|introducing|announcing|first\s+public\s+release)\b"
PRE_WINDOW_RULE = (
    "a repo whose first declared launch event (Show HN, Launch HN, Product Hunt, a launch-worded "
    "release, a launch-worded post of a declared maintainer's Bluesky account) precedes the "
    "brief's window has no view-B anchor (rule none, reason launched_before_window), like the "
    "undeclared rule's before-window case; later in-window events are not used as anchors or "
    "relaunches; counted per kind of that first event (launched_before_window:<kind>, part of "
    "none), and it takes precedence over an incomplete source (an unread source could only hold "
    "an even earlier event). Sources that can see before the window: the HN launch lookup "
    "(the same three searches from HN's epoch to the window's start) and discovery's recorded "
    "Show HN posts; releases (fetched back to the earliest, newest first, up to 10 pages: a list "
    "cut at the cap can miss older launch-worded releases); Product Hunt's slug route (no date "
    "bound; posts of any date stored); Bluesky (no date filter; the window applied in memory, "
    "every page read until the list ends or a launch-worded post before the window is found). "
    "Not seen: a Product Hunt launch before the window that only the topic scan (bounded to "
    "the window) would find"
)
VIEW_B_ANCHOR_RULE = (
    "view B (launch) has its own anchor, determined only from launch events, never from star "
    "data (bursts are never used): the earliest maintainer-initiated launch event inside the "
    "brief's window, i.e. (i) a Show HN or Launch HN post from discovery or the launch lookup "
    "(URL-matched, or a confirmed title match; Show HN is 'something you made' by HN's rules; "
    "no author is stored or compared), (ii) a Product Hunt launch (a post whose name is the "
    "repo name, found by slug or topic scan and confirmed, ADR-085; dated featuredAt, else "
    "createdAt), (iii) a GitHub release announced as a launch: its name, "
    "or the first 300 characters of its body, matches the whole-word, case-insensitive pattern "
    f"{RELEASE_LAUNCH_PATTERN!r} (releases fetched via the GitHub API, newest first, 100 per "
    "page, up to 10 pages; stored: tag, published_at, prerelease, launch match; never the name "
    "or body), or (iv) a Bluesky post by an account the maintainer declared (a bsky.app profile "
    "link or @name.bsky.social in the owner's GitHub profile social accounts, the org page or "
    "the repo's homepage field, or in a README that names exactly one account) that links the "
    "repo's GitHub URL or homepage and whose text matches the same launch-wording pattern "
    "(ADR-085 addendum 3; the text is read in memory only; posts without it are counted, never "
    "events or relaunches; dated by the API's sortAt; only kind, time, role and match stored). "
    "A declared launch event before the window leaves no anchor (PRE_WINDOW_RULE). "
    "Rules show_hn, launch_hn, "
    "product_hunt, release_launch, bluesky_maintainer_post; a tie at the same instant in that "
    "order, then item id, post id, tag or post ordinal. Every later launch event in the window "
    "is a relaunch event (time, kind, ref), reported descriptively. A repo whose Bluesky source "
    "(or Product Hunt source) could not be read completely has no view-B anchor (rule none, "
    "reason launch_source_incomplete:<source>), counted: an unread source could hold the "
    "earliest event, so the anchor would silently change. No launch event in the window: the "
    "earlier of (a) the first external mention, the earliest HN story or comment up to the "
    "window's end whose URL or text links the repo's github.com/owner/name (HN Algolia "
    "search_by_date over all item types, typo tolerance off, walked from the oldest page, "
    "up to 5 requests; stored: item id, time, kind) and (b) the first public release, the "
    "earliest GitHub release's published_at (prereleases included; tags are not used: their "
    "dates are not cheaply available); a tie goes to the mention. Rules undeclared:first_mention "
    "and undeclared:first_release, flagged undeclared_launch and reported as their own "
    "sub-population of view B (own winners, losers and balance), never mixed into the "
    "declared-launch headline. No anchor (rule none) when neither exists, when the earlier one "
    "is outside the window, or when either source is incomplete (a release list cut at the "
    "page cap, a mention search that hit its cap or failed): the earlier one can't be known"
)
VIEW_B_LIMITATIONS = (
    "X: paid API, and the owner's cap for paid services other than the API is USD 0",
    "Reddit: no API approval",
    "blogs: no source",
    "Bluesky posts by accounts the maintainer did not declare on the repo, README, org page or "
    "GitHub profile: never searched (finding them would be cross-platform name matching, "
    "which ADR-075.3 forbids)",
    "Product Hunt launches under another product name than the repo's, or that no rule "
    "confirms (ADR-085)",
)
# a source the pre-registered parameters turn off is a limitation too (ADR-085)
PH_OFF_LIMITATION = "Product Hunt: not collected (launch source product_hunt off)"
BSKY_OFF_LIMITATION = "Bluesky maintainer posts: not collected (launch source bluesky off)"
VIEW_B_BIAS = (
    "a case whose real first launch was on an unobserved channel (X, Reddit, a blog, an "
    "undeclared Bluesky account, a Product Hunt post under another name) is dated by its first "
    "observable event: its view-B anchor can be later than the real launch, so launch size may "
    "be measured on a relaunch or follow-up, and a case with no observable launch event is "
    "counted as an undeclared launch"
)

# --- language groups (ADR-084; owner decision (1): "groups fixed now: JS+TS, Python, Go, Rust,
# other"). Exact GitHub primary-language names (linguist); anything else, a missing language
# included, is `other`.
LANGUAGE_GROUPS: dict[str, str] = {
    "JavaScript": "js_ts",
    "TypeScript": "js_ts",
    "Python": "python",
    "Go": "go",
    "Rust": "rust",
}
LANGUAGE_GROUP_LABELS = ("js_ts", "python", "go", "rust", "other")
PATTERN_LANGUAGE_RULE = (
    "for every headline pattern of a view, the pattern step (a later milestone) computes the "
    "contrast d = share present among the view's headline winners - share among their matched "
    "losers, on all headline pairs (d_all) and on the same-language-group headline pairs only "
    "(d_same); holds: d_same has the sign of d_all and |d_same| >= 0.5 |d_all|; weakens: the "
    "same sign but |d_same| < 0.5 |d_all|, or d_same = 0; reverses: the opposite sign, and the "
    "pattern is labelled 'language-dependent'; not assessable when the subset falls below the "
    "minimum evidence (ADR-050.3); a pattern with d_all = 0 is not a pattern"
)
SD_RULE = (
    "calipers and standardized differences use, per covariate, the SD over the brief's full "
    "shortlisted pool at sort time: every shortlisted field and reference repo with an observed "
    "value of it (anchored or not in this view, whatever its role), on the log10 scale: "
    "LSM = log10(1 + stars on endpoint days 0..2), repo age = log10(max(1, days from creation "
    "to the anchor)), stars before launch = log10(1 + max(0, net stars from creation to the "
    "day before the anchor's first day)); each value is relative to the view's own anchor "
    "(view B: its launch-event anchor); the SDs are computed when the selection runs and "
    "stored with it (the rule is pre-registered, the values can't be)"
)

# --- metrics of the views (ADR-083; outcome-model §1.2 day mapping) ------------------------------
# Endpoint-day windows are half-open day-index ranges from the first day of the anchor window
# (§1.2): "days 0-2" = indices 0, 1 and 2 (the LSM days, owner decision 2026-09-27: days 0-2
# inclusive), "days 3-30" = indices 3..29 (27 days, to the end of the 30-day horizon). Contiguous.
LAUNCH_SIZE = "att.stars_launch@0-2"  # view B's attention metric; view A's regressor
FOLLOW_STARS = "att.stars_follow@3-30"  # view A's outcome count
FT_RESID = "att.stars_follow_resid@3-30"  # view A's primary metric (derived, per population)
FT_LOGRATIO = "att.stars_follow_logratio@3-30"  # view A's sensitivity alternative (derived)
LAUNCH_DAYS = (0, 3)
FOLLOW_DAYS = (3, 30)
HN_POINTS = "att.hn_points"
REDDIT_REACH = "att.reddit_reach"  # no Reddit connector (TM-05 is a GAP): always unknown
# view B's secondary launch-size measures from Product Hunt (ADR-085): reported, never ranked on
PH_VOTES = "att.ph_votes"
PH_COMMENTS = "att.ph_comments"
PLAIN_STARS = "att.stars@30"
FIT_EPS = 1e-12  # Sxx at or below this is zero variance
FT_ROUND = 9  # derived values are rounded so float noise never splits a percentile tie
FOLLOW_THROUGH_RULE = (
    f"{FOLLOW_THROUGH_METRIC_VERSION}: X = ln(1 + max(0, stars on endpoint days 0..2)), "
    "Y = ln(1 + max(0, stars on endpoint days 3..29)) (contiguous windows); both observed "
    "only after first day + window end + settle_lag (3 d), else pending; a missing day is "
    "unknown. Residual: closed-form OLS Y = a + bX on the view's population cases with both "
    "values observed (b = Sxy/Sxx, a = mean(Y) - b mean(X); sums in candidate_ref order), "
    "residual = Y - (a + bX), rounded to 9 decimals. Fewer than MIN_POPULATION (20) fit cases, "
    "or Sxx <= 1e-12: no fit, every residual unknown (no percentiles). Log-ratio (sensitivity): "
    "Y - X. Zero floor: a threshold is met only when stars on days 3..29 > 0. The fit is redone "
    "on each population it is used on (sensitivity D refits without the flagged cases)"
)
CONTEXT_LABEL = "context, not a headline"

# --- view B's launch sources (ADR-085) -----------------------------------------------------------
PH_TOPICS = ("open-source", "developer-tools")  # Product Hunt topic slugs scanned (a parameter)
PH_FLAG_ENV = "PIGTAIL_SELECTION_PRODUCT_HUNT"
BSKY_FLAG_ENV = "PIGTAIL_SELECTION_BLUESKY"
PH_TOPICS_ENV = "PIGTAIL_PH_TOPICS"
INCOMPLETE_PREFIX = "launch_source_incomplete:"  # a view-B anchor reason (ADR-085)


def _flag(v: str | None, default: bool) -> bool:
    if v is None or not v.strip():
        return default
    x = v.strip().lower()
    if x in ("1", "true", "yes", "on"):
        return True
    if x in ("0", "false", "no", "off"):
        return False
    raise SelectionError(f"bad boolean {v!r} for a launch-source flag")


def launch_source_settings(
    env: Mapping[str, str] | None = None,
) -> tuple[bool, bool, tuple[str, ...]]:
    """(product_hunt, bluesky, Product Hunt topics) of this instance (ADR-085): both sources on
    unless `PIGTAIL_SELECTION_PRODUCT_HUNT` / `PIGTAIL_SELECTION_BLUESKY` say false; topics from
    `PIGTAIL_PH_TOPICS` (comma-separated slugs), else `PH_TOPICS`. They go into the
    pre-registered parameters, so changing one after a pre-registration fails the gate."""
    import os

    e = os.environ if env is None else env
    raw = (e.get(PH_TOPICS_ENV) or "").strip()
    topics = tuple(t.strip().lower() for t in raw.split(",") if t.strip()) if raw else PH_TOPICS
    return _flag(e.get(PH_FLAG_ENV), True), _flag(e.get(BSKY_FLAG_ENV), True), topics


@dataclass(frozen=True)
class View:
    """How one headline view reads the brief's success definition (ADR-083): which metric
    stands for attention, which anchored cases form the population, and how losers are
    matched. Selection-version semantics: the brief itself is unchanged."""

    key: str  # plain | follow_through | launch | launch_undeclared
    label: str
    attention_metric: str | None  # None: the brief's own attention metric
    population: Literal["anchored", "declared_launch", "undeclared_launch"]
    matching: Literal["lsm", "pre_launch"]
    extra_alternatives: tuple[str, ...] = ()  # attention metrics tried as sensitivity checks
    secondary: tuple[str, ...] = ()  # reported next to the view's outcome, never ranked on
    # which anchor the view reads: the case's §2.2 anchor, or view B's launch-event anchor
    # (`CaseInput.launch_case`, ADR-084)
    anchor: Literal["case", "launch_event"] = "case"
    # the SD of the calipers and standardized differences: the full shortlisted pool (ADR-084)
    # or, for the selection-v4 comparison view only, the level's winners and loser pool
    sd_basis: Literal["full_pool", "winners_and_pool"] = "full_pool"
    # only numeric differences exclude a pair from the headline (ADR-084); the plain
    # comparison view keeps the selection-v5 rule (a language mismatch counts as 1)
    categorical_excludes: bool = False
    headline: bool = True

    def definition(self, d: Definition) -> Definition:
        if self.attention_metric is None:
            return d
        return replace(d, metrics={**d.metrics, "attention": self.attention_metric})

    @property
    def caliper_covariates(self) -> tuple[tuple[str, float], ...]:
        if self.matching == "lsm":
            return (("lsm", LSM_CALIPER_SD),)
        return (("age_log10", AGE_CALIPER_SD), ("prelaunch_log", PRELAUNCH_CALIPER_SD))

    @property
    def distance_numeric(self) -> tuple[str, ...]:
        return ("lsm", "age_log10") if self.matching == "lsm" else ("age_log10", "prelaunch_log")

    @property
    def categorical(self) -> tuple[str, ...]:
        """Categorical distance terms (a mismatch or a missing value costs 1)."""
        return ("language",) if self.matching == "lsm" else ("language", "category")

    @property
    def balance_categorical(self) -> tuple[str, ...]:
        """Categorical balance rows (SMD per level): the distance terms, the language group
        and the distribution surface (ADR-084); `install_path` is a multi-label row."""
        if self.key == "plain":
            return self.categorical
        return (*self.categorical, "language_group", "surface")

    @property
    def balance_multilabel(self) -> tuple[str, ...]:
        return () if self.key == "plain" else ("install_path",)

    @property
    def exemplar_order(self) -> str:
        return "lsm" if self.matching == "lsm" else "prelaunch_log"

    def params(self, ctx: Context) -> dict[str, Any]:
        numeric = list(self.distance_numeric)
        if "founder_audience_bucket" not in ctx.exact_match:
            numeric.append("audience_band")
        balance = [*numeric]
        if "launch_half_year" not in ctx.exact_match:
            balance.append("launch_quarter")
        return {
            "label": self.label,
            "headline": self.headline,
            "attention_metric": self.attention_metric or "brief's attention metric",
            "anchor": {
                "case": "the case's outcome-model §2.2 anchor (launch or burst, ADR-077.3, "
                "ADR-081)",
                "launch_event": "view B's launch-event anchor (VIEW_B_ANCHOR_RULE, ADR-084)",
            }[self.anchor],
            "population": {
                "anchored": "anchored field and reference cases (outcome-model §3)",
                "declared_launch": "field and reference cases whose view-B anchor is a declared "
                "launch event (rules show_hn, launch_hn, release_launch)",
                "undeclared_launch": "field and reference cases without a declared launch event, "
                "anchored on the first external mention or the first public release "
                "(rules undeclared:first_mention, undeclared:first_release; flagged "
                "undeclared_launch, reported separately, never in the declared-launch headline)",
            }[self.population],
            "matching": {
                "exact": list(ctx.exact_match),
                "calipers": {
                    **{f"{c}_sd": w for c, w in self.caliper_covariates},
                    "quarter": QUARTER_CALIPER,
                },
                "sd_basis": self.sd_basis,
                "sd_rule": SD_RULE if self.sd_basis == "full_pool" else "winners and loser pool",
                "distance": [*(f"|d{c}|/SD" for c in numeric), "|dquarter|"]
                + [f"1[{c} differs or missing]" for c in self.categorical],
                "balance_covariates": [
                    *balance,
                    *self.balance_categorical,
                    *self.balance_multilabel,
                ],
                "headline_exclusion": (
                    "a pair is excluded from the headline only when its standardized "
                    "difference exceeds headline_exclusion_smd on a numeric covariate ("
                    + ", ".join(balance)
                    + "); categorical rows (language, language group, category, surface, "
                    "install path) never exclude and are labelled balance_limited when they "
                    "miss the target"
                    if not self.categorical_excludes
                    else "SMD > headline_exclusion_smd on any balance covariate (a language "
                    "mismatch or missing language counts as 1)"
                ),
                "missing_caliper_value": "not eligible",
                "exemplar_order": f"nearest {self.exemplar_order}",
            },
            "sensitivity_extra": [f"metric:attention:{m}" for m in self.extra_alternatives]
            + (["pairs:same_language_group"] if self.key != "plain" else []),
            "secondary": list(self.secondary),
        }


VIEW_PLAIN = View(
    "plain",
    "attention as the brief defines it, LSM matching (selection-v4 semantics; comparison only)",
    None,
    "anchored",
    "lsm",
    sd_basis="winners_and_pool",
    categorical_excludes=True,
    headline=False,
)
VIEW_FOLLOW_THROUGH = View(
    "follow_through",
    "A: follow-through relative to launch size (headline view 1)",
    FT_RESID,
    "anchored",
    "lsm",
    extra_alternatives=(FT_LOGRATIO, PLAIN_STARS),
    secondary=(PLAIN_STARS, LAUNCH_SIZE, FOLLOW_STARS),
)
VIEW_LAUNCH = View(
    "launch",
    "B: launch size among declared launches (headline view 2)",
    LAUNCH_SIZE,
    "declared_launch",
    "pre_launch",
    secondary=(HN_POINTS, PH_VOTES, PH_COMMENTS, REDDIT_REACH),
    anchor="launch_event",
)
VIEW_LAUNCH_UNDECLARED = View(
    "launch_undeclared",
    "B, undeclared launches: launch size among repos anchored on their first external mention "
    "or first public release (reported separately, not a headline)",
    LAUNCH_SIZE,
    "undeclared_launch",
    "pre_launch",
    secondary=(HN_POINTS, PH_VOTES, PH_COMMENTS, REDDIT_REACH),
    anchor="launch_event",
    headline=False,
)
HEADLINE_VIEWS = (VIEW_FOLLOW_THROUGH, VIEW_LAUNCH)
VIEWS = (VIEW_FOLLOW_THROUGH, VIEW_LAUNCH, VIEW_LAUNCH_UNDECLARED)

Status = Literal["observed", "pending", "unknown", "not_applicable"]
CasePanel = Literal["field", "reference", "exemplar"]
Outcome = Literal["qualifies", "fails", "undetermined"]
AnomalyFlag = Literal["true", "false", "unknown"]
Role = Literal[
    "winner",
    "matched_loser",
    "qualified_not_selected",
    "unrankable",
    "loser_pool_unmatched",
    "undetermined",
    "no_anchor",
    "outside_widening",
    "exemplar",
    "exemplar_matched_loser",
    "not_in_view",  # anchored, but outside this view's population (view B: a burst anchor)
]
ROLES: tuple[str, ...] = Role.__args__  # type: ignore[attr-defined]


class SelectionError(ValueError):
    pass


# --- inputs -------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Value:
    """One metric value of one candidate (outcome-model §1.1): status, tag and, when `observed`,
    the value. `group` partitions the percentile population (the downloads ecosystem, §3)."""

    status: Status
    value: float | None = None
    tag: str = "unknown"  # verified | estimated | self_reported | unknown (ADR-014)
    reason: str | None = None
    group: str | None = None
    # the count the zero floor (§3) checks when the value itself is not a count (view A's
    # residual and log-ratio: stars on days 3..29, ADR-083); None: the value itself
    floor_basis: float | None = None

    def to_dict(self) -> dict[str, Any]:
        d = {
            "value": _r(self.value),
            "status": self.status,
            "tag": self.tag,
            "reason": self.reason,
            "group": self.group,
        }
        if self.floor_basis is not None:
            d["floor_basis"] = _r(self.floor_basis)
        return d


NO_SOURCE = Value("unknown", reason="no_source")


@dataclass(frozen=True)
class Anchor:
    """The case anchor T (outcome-model §2.2)."""

    type: Literal["launch", "burst"]
    at: datetime
    precision: Literal["hour", "day"]
    # show_hn | launch_hn | velocity-v0; view B also release_launch | first_mention |
    # first_release (ADR-084)
    source: str
    # launches: discovery | lookup:url | lookup:title (ADR-081); view B also github_release |
    # hn_story | hn_comment
    via: str | None = None
    rule: str | None = None  # view B's anchor rule (`ANCHOR_RULE_LABELS`); None: §2.2 anchor
    ref: str | None = None  # view B: the HN item id or the release tag of the anchoring event

    @property
    def undeclared(self) -> bool:
        return self.rule is not None and self.rule.startswith("undeclared:")

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "type": self.type,
            "at": self.at.isoformat(),
            "precision": self.precision,
            "source": self.source,
            "via": self.via,
        }
        if self.rule is not None:
            d["rule"] = self.rule
            d["ref"] = self.ref
            d["undeclared_launch"] = self.undeclared
        return d


@dataclass(frozen=True)
class Covariates:
    """Matching covariates (outcome-model §5.6), measured at or before T except LSM."""

    lsm: float | None = None  # log10(1 + raw stars on endpoint days 0..2 of the window, ADR-083)
    launch_quarter: int | None = None  # year * 4 + quarter index (UTC quarter of T)
    launch_half_year: str | None = None  # e.g. "2025H2"
    age_log10: float | None = None  # log10(days from repo creation to T)
    audience_band: str = "unknown"  # CB-10 reach band; "unknown" is its own level (O14)
    language: str | None = None
    launch_type: str | None = None  # show_hn | launch_hn | burst (ADR-057.1 exemplar matching)
    # stars before launch (view B, ADR-083): net stars on the endpoint days from the repo's
    # creation day to the day before the anchor's first day; None (unknown) when the stored
    # series doesn't reach the creation day, has a gap, or the creation date is unknown
    prelaunch_stars: int | None = None
    prelaunch_log: float | None = None  # log10(1 + max(0, prelaunch_stars))
    prelaunch_reason: str | None = None  # why it is unknown
    # distribution surface (ADR-084, coded before any outcome; `pigtail.briefs.surface`)
    surface: str = "unknown"
    install_paths: tuple[str, ...] = ("unknown",)

    @property
    def language_group(self) -> str:
        return language_group(self.language)

    def to_dict(self) -> dict[str, Any]:
        return {
            "lsm": _r(self.lsm),
            "launch_quarter": self.launch_quarter,
            "launch_half_year": self.launch_half_year,
            "age_log10": _r(self.age_log10),
            "audience_band": self.audience_band,
            "language": self.language,
            "language_group": self.language_group,
            "launch_type": self.launch_type,
            "prelaunch_stars": self.prelaunch_stars,
            "prelaunch_log": _r(self.prelaunch_log),
            "prelaunch_reason": self.prelaunch_reason,
            "surface": self.surface,
            "install_paths": list(self.install_paths),
        }


def language_group(language: str | None) -> str:
    """The fixed language group of a GitHub primary language (`LANGUAGE_GROUPS`, exact names):
    `js_ts`, `python`, `go`, `rust`, else `other` (a missing language included)."""
    return LANGUAGE_GROUPS.get(language or "", "other")


@dataclass(frozen=True)
class CaseInput:
    ref: str  # candidate_ref (`gh:owner/name`)
    panel: CasePanel = "field"
    distance: int = 0  # 0 = core field (R4.10)
    named_index: int | None = None
    anchor: Anchor | None = None
    anchor_reason: str | None = None
    values: Mapping[str, Value] = field(default_factory=dict)
    business: Mapping[str, Value] = field(default_factory=dict)
    covariates: Covariates = field(default_factory=Covariates)
    star_anomaly_flag: AnomalyFlag = "unknown"
    anomaly: Mapping[str, Any] = field(default_factory=dict)
    # the same repo as view B sees it (ADR-084): anchored on its launch-event anchor, with its
    # values and covariates relative to that anchor; None: derived from `anchor` (a Show HN /
    # Launch HN anchor is a declared launch, a burst is none), for inputs built by hand
    launch_case: CaseInput | None = None
    # view B: every launch event after the anchoring one (time, kind, item id or tag)
    relaunch_events: tuple[Mapping[str, Any], ...] = ()
    # view B: the first declared launch event before the window, when there is one (reason
    # `launched_before_window`, ADR-085 addendum 3)
    pre_window_launch: Mapping[str, Any] | None = None

    @property
    def is_reference(self) -> bool:
        return self.panel == "reference"

    def for_view_b(self) -> CaseInput:
        """This case as view B reads it (`launch_case`, else derived from the §2.2 anchor:
        a declared Show HN / Launch HN anchor keeps its rule, a burst anchor is dropped)."""
        if self.launch_case is not None:
            return self.launch_case
        a = self.anchor
        if a is not None and a.type == "launch":
            rule = a.rule or (a.source if a.source in DECLARED_RULES else "show_hn")
            return replace(self, anchor=replace(a, rule=rule))
        reason = "burst anchor: never used in view B" if a is not None else self.anchor_reason
        no = Value("unknown", reason="no_anchor")
        return replace(
            self, anchor=None, anchor_reason=reason, values=dict.fromkeys(self.values, no)
        )


# --- the success definition ---------------------------------------------------------------------
@dataclass(frozen=True)
class Definition:
    """The effective success definition (R18.1 `success`, after any fallback step)."""

    primary: str
    floors: Mapping[str, float | None]  # rankable dimension -> percentile floor (primary incl.)
    metrics: Mapping[str, str]  # dimension -> metric id
    business_minimum: tuple[str, ...] = ()
    if_not_applicable: Mapping[str, str] = field(default_factory=dict)
    weights: Mapping[str, float] | None = None
    no_measurable_adoption: str = "use_community_as_primary_and_flag"
    accept_self_reported: bool = False
    rank_by: str | None = None  # sensitivity A: rank by this dimension instead

    @classmethod
    def from_brief(cls, brief: Brief) -> Definition:
        s = brief.success
        floors: dict[str, float | None] = {}
        if s.primary != "business":
            floors[s.primary] = THRESHOLD_PERCENTILE[s.primary_threshold]
        for dim, t in s.minimums.items():
            floors[dim] = THRESHOLD_PERCENTILE[t]
        return cls(
            primary=s.primary,
            floors=floors,
            metrics={d: s.metric(d) for d in DIMENSIONS},
            business_minimum=tuple(s.business_minimum),
            if_not_applicable={str(k): v for k, v in s.if_not_applicable.items()},
            weights={str(k): w for k, w in s.weights.items()} if s.weights else None,
            no_measurable_adoption=s.fallbacks.no_measurable_adoption,
            accept_self_reported=s.accept_self_reported,
        )

    def star_metric_used(self) -> bool:
        """Sensitivity D runs only when a star metric is in the definition (§8.1)."""
        dims = {self.primary, *self.floors, *(self.weights or {})}
        return any(self.metrics.get(d, "").startswith("att.stars") for d in dims)

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary": self.primary,
            "floors": {d: _r(v) for d, v in sorted(self.floors.items())},
            "metrics": dict(sorted(self.metrics.items())),
            "business_minimum": list(self.business_minimum),
            "if_not_applicable": dict(sorted(self.if_not_applicable.items())),
            "weights": None if self.weights is None else dict(sorted(self.weights.items())),
            "no_measurable_adoption": self.no_measurable_adoption,
            "accept_self_reported": self.accept_self_reported,
            "rank_by": self.rank_by,
        }


@dataclass(frozen=True)
class Context:
    """Brief settings the selection reads (R4.3, R4.8, R4.10, ADR-053.2, ADR-054, ADR-057)."""

    brief_id: str
    brief_version: int
    winners: int = 20
    losers: int = 20
    exact_match: tuple[str, ...] = ("founder_audience_bucket", "launch_half_year")
    smd_target: float = 0.25
    headline_exclusion_smd: float = 0.5
    max_distance: int = 0
    min_winners: int = 10
    fallback_steps: tuple[str, ...] = ()
    losers_per_exemplar: int = 2
    exemplar_match_on: tuple[str, ...] = ("launch_type", "launch_period", "audience_bucket")
    sensitivity: tuple[str, ...] = ("primary_swap", "band_shift", "weights", "fake_star_filter")
    view: View = VIEW_PLAIN  # the view `select` computes (ADR-083); not a brief setting
    # view B's launch sources (ADR-085): on unless the instance turns them off before the
    # pre-registration (`PIGTAIL_SELECTION_PRODUCT_HUNT`, `PIGTAIL_SELECTION_BLUESKY`); a source
    # that applies must run, or the selection is refused (fail closed)
    product_hunt: bool = True
    bluesky: bool = True
    ph_topics: tuple[str, ...] = PH_TOPICS

    @classmethod
    def from_brief(cls, brief: Brief, env: Mapping[str, str] | None = None) -> Context:
        if brief.version is None:
            raise SelectionError("select on a stored brief version")
        tfw = brief.success.fallbacks.too_few_winners
        ph, bsky, topics = launch_source_settings(env)
        return cls(
            brief_id=brief.brief_id,
            brief_version=brief.version,
            winners=brief.panel.winners,
            losers=brief.panel.losers,
            exact_match=tuple(brief.panel.exact_match),
            smd_target=brief.panel.smd_target,
            headline_exclusion_smd=brief.panel.headline_exclusion_smd,
            max_distance=min(MAX_WIDENING, len(brief.field.widening_steps)),
            min_winners=tfw.min_winners,
            fallback_steps=tuple(tfw.steps),
            losers_per_exemplar=brief.distribution_exemplars.losers_per_exemplar,
            exemplar_match_on=tuple(brief.distribution_exemplars.match_on),
            sensitivity=tuple(dict.fromkeys(brief.success.sensitivity)),
            product_hunt=ph,
            bluesky=bsky,
            ph_topics=topics,
        )

    @property
    def required_launch_sources(self) -> tuple[str, ...]:
        """The launch sources whose data view B's anchor needs (`product_hunt`, `bluesky`)."""
        on = (("product_hunt", self.product_hunt), ("bluesky", self.bluesky))
        return tuple(k for k, v in on if v)

    def view_b_limitations(self) -> list[str]:
        out = list(VIEW_B_LIMITATIONS)
        if not self.product_hunt:
            out.append(PH_OFF_LIMITATION)
        if not self.bluesky:
            out.append(BSKY_OFF_LIMITATION)
        return out

    def tie(self, ref: str) -> str:
        """Deterministic tie-break key (outcome-model §5.4)."""
        return hashlib.sha256(f"{self.brief_id}:{self.brief_version}:{ref}".encode()).hexdigest()

    def params(self) -> dict[str, Any]:
        """Everything that defines the selection, hashed into `selection_params_sha256` (R8.2):
        the brief's panel settings, the anchor rules (with the live hash of their code,
        ADR-083), the launch lookup and its title confirmation rule (with the Haiku prompt's
        fingerprint and the resolved model id), view B's launch-event anchor and its
        limitations, the distribution-surface coding, the language groups and the pattern
        rule, the SD rule and views A, B (declared and undeclared) and C (ADR-084). The same
        for every view of one selection."""
        from pigtail.briefs.confirm import confirmation_params
        from pigtail.briefs.launch_sources import launch_source_params
        from pigtail.briefs.outcomes import anchor_rule_source_sha256
        from pigtail.briefs.surface import surface_params

        return {
            "selection_version": SELECTION_VERSION,
            "anchor_rule_source_sha256": anchor_rule_source_sha256(),
            "title_confirmation": confirmation_params(),
            "follow_through": {
                "metric_version": FOLLOW_THROUGH_METRIC_VERSION,
                "rule": FOLLOW_THROUGH_RULE,
                "launch_days": list(LAUNCH_DAYS),
                "follow_days": list(FOLLOW_DAYS),
            },
            "views": {v.key: v.params(self) for v in VIEWS},
            "view_b_anchor": {
                "rule": VIEW_B_ANCHOR_RULE,
                "rules": list(ANCHOR_RULE_LABELS),
                "count_labels": list(ANCHOR_COUNT_LABELS),
                "pre_window_rule": PRE_WINDOW_RULE,
                "release_launch_pattern": RELEASE_LAUNCH_PATTERN,
                "bluesky_launch_text": "the post's text (record.text), whole, in memory only",
                "release_launch_text": "release name, and the first 300 characters of the body",
                "tie_break": "same instant: " + " < ".join(DECLARED_RULES) + ", then ref",
                "limitations": self.view_b_limitations(),
                "known_bias": VIEW_B_BIAS,
                "secondary_launch_size": [HN_POINTS, PH_VOTES, PH_COMMENTS, REDDIT_REACH],
            },
            "launch_sources": launch_source_params(
                product_hunt=self.product_hunt, bluesky=self.bluesky, ph_topics=self.ph_topics
            ),
            "distribution_surface": surface_params(),
            "language_groups": {
                "mapping": dict(sorted(LANGUAGE_GROUPS.items())),
                "labels": list(LANGUAGE_GROUP_LABELS),
                "else": "other (any other GitHub primary language, or none)",
                "sensitivity": "per headline view, the headline pairs restricted to pairs whose "
                "two sides share a language group: counts and balance on that subset",
                "pattern_rule": PATTERN_LANGUAGE_RULE,
            },
            "headline_exclusion_rule": "numeric standardized differences only (owner option 1, "
            "ADR-084): language, language group, category, surface and install paths stay in "
            "the balance table, labelled balance_limited when they miss the target, and never "
            "exclude a pair",
            "sd_rule": SD_RULE,
            "context_view": {
                "label": CONTEXT_LABEL,
                "rule": "per view: its final winners against every shortlisted non-winner of "
                "the brief (field and reference repos: the unmatched pool, matched losers, "
                "undetermined, unrankable, outside-widening, not-in-view and no-anchor repos; "
                "exemplars are outside the field), no matching; statistics over those with an "
                "observed value, with the counts with and without one; covariate SMDs of the "
                "winners against those non-winners",
            },
            "outcome_model_version": OUTCOME_MODEL_VERSION,
            "analysis_params_version": PARAMS_VERSION,
            "min_population": MIN_POPULATION,
            "winners": self.winners,
            "losers": self.losers,
            "winners_min": WINNERS_MIN,
            "losers_min": LOSERS_MIN,
            "exact_match": list(self.exact_match),
            "smd_target": self.smd_target,
            "headline_exclusion_smd": self.headline_exclusion_smd,
            "calipers": {"lsm_sd": LSM_CALIPER_SD, "quarter": QUARTER_CALIPER},
            "matching": MATCHING_RULE,
            "anchor_rule_version": ANCHOR_RULE_VERSION,
            "anchor_rule": ANCHOR_RULE,
            "launch_lookup": LAUNCH_LOOKUP,
            "max_distance": self.max_distance,
            "too_few_winners": {
                "min_winners": self.min_winners,
                "steps": list(self.fallback_steps),
            },
            "exemplars": {
                "losers_per_exemplar": self.losers_per_exemplar,
                "match_on": list(self.exemplar_match_on),
            },
            "sensitivity": list(self.sensitivity),
            "tie_break": TIE_BREAK,
        }


# --- percentiles and qualification (§3, §5.3, §5.4, §6) -----------------------------------------
@dataclass
class Qual:
    outcome: Outcome
    checks: list[dict[str, Any]]
    score: float | None
    rank_basis: str
    flags: list[str]


@dataclass
class Evaluation:
    definition: Definition
    pct: dict[str, dict[str, float | None]]  # metric -> ref -> percentile
    populations: dict[str, dict[str, Any]]  # metric -> counts (n per group, statuses)
    qual: dict[str, Qual]  # ref -> qualification (population cases only)
    # values derived from the population (view A's residual and log-ratio), per case
    derived: dict[str, dict[str, Value]] = field(default_factory=dict)
    fit: dict[str, Any] | None = None  # view A's OLS fit, when a derived metric is used

    def value(self, c: CaseInput, metric: str) -> Value:
        v = self.derived.get(c.ref, {}).get(metric)
        return v if v is not None else c.values.get(metric, NO_SOURCE)


# --- view A's follow-through metric (ADR-083) ----------------------------------------------------
def _ln1p0(x: float) -> float:
    return math.log1p(max(0.0, x))


def _pending_or_unknown(a: Value, b: Value) -> Value:
    """The status of a value derived from `a` and `b` when either isn't observed."""
    for v in (a, b):
        if v.status == "pending":
            return Value("pending", reason=v.reason or "horizon_not_reached")
    for v in (a, b):
        if v.status != "observed" or v.value is None:
            return Value("unknown", reason=f"{v.status}:{v.reason}" if v.reason else v.status)
    raise AssertionError("both observed")


def follow_through_fit(
    cases: Sequence[CaseInput],
) -> tuple[dict[str, dict[str, Value]], dict[str, Any]]:
    """View A's metrics (`FOLLOW_THROUGH_RULE`) for every case in `cases`: the OLS residual
    (`FT_RESID`) of Y = ln(1 + stars on days 3..29) on X = ln(1 + stars on days 0..2), fitted
    on the cases with both values observed, and the log-ratio Y - X (`FT_LOGRATIO`). Closed
    form, sums in `candidate_ref` order (`math.fsum`), so the result is deterministic. No fit
    (every residual unknown, hence no percentiles) below `MIN_POPULATION` fit cases or when
    the launch sizes have zero variance."""
    ordered = sorted(cases, key=lambda c: c.ref)
    pts: list[tuple[str, float, float]] = []
    for c in ordered:
        lv, fv = c.values.get(LAUNCH_SIZE, NO_SOURCE), c.values.get(FOLLOW_STARS, NO_SOURCE)
        ok = lv.status == "observed" and fv.status == "observed"
        if ok and lv.value is not None and fv.value is not None:
            pts.append((c.ref, _ln1p0(lv.value), _ln1p0(fv.value)))
    n = len(pts)
    fit: dict[str, Any] = {
        "metric_version": FOLLOW_THROUGH_METRIC_VERSION,
        "n": n,
        "form": "OLS ln(1+stars d3..29) on ln(1+stars d0..2), closed form",
        "status": "ok",
        "intercept": None,
        "slope": None,
    }
    a = b = None
    if n < MIN_POPULATION:
        fit["status"] = "small_population"
    else:
        mx = math.fsum(p[1] for p in pts) / n
        my = math.fsum(p[2] for p in pts) / n
        sxx = math.fsum((p[1] - mx) ** 2 for p in pts)
        sxy = math.fsum((p[1] - mx) * (p[2] - my) for p in pts)
        if sxx <= FIT_EPS:
            fit["status"] = "zero_variance"
        else:
            b = sxy / sxx
            a = my - b * mx
            fit["intercept"], fit["slope"] = _r(a), _r(b)
    out: dict[str, dict[str, Value]] = {}
    label = "derived from raw star-history (unfiltered, anomaly-checked)"
    for c in ordered:
        xv, yv = c.values.get(LAUNCH_SIZE, NO_SOURCE), c.values.get(FOLLOW_STARS, NO_SOURCE)
        if not (xv.status == "observed" and yv.status == "observed"):
            miss = _pending_or_unknown(xv, yv)
            out[c.ref] = {FT_RESID: miss, FT_LOGRATIO: miss}
            continue
        assert xv.value is not None and yv.value is not None
        x, y = _ln1p0(xv.value), _ln1p0(yv.value)
        ratio = Value("observed", round(y - x, FT_ROUND), "verified", label, None, yv.value)
        if a is None or b is None:
            resid = Value("unknown", reason=f"no_fit:{fit['status']}")
        else:
            resid = Value(
                "observed", round(y - (a + b * x), FT_ROUND), "verified", label, None, yv.value
            )
        out[c.ref] = {FT_RESID: resid, FT_LOGRATIO: ratio}
    return out, fit


def percentiles(
    cases: Sequence[CaseInput], metric: str
) -> tuple[dict[str, float | None], dict[str, Any]]:
    """Mid-rank percentiles of `metric` over the `observed` values of `cases`, per group
    (outcome-model §3): `p = 100 (n_below + 0.5 n_equal) / n`; `None` below MIN_POPULATION."""
    groups: dict[str, list[tuple[str, float]]] = {}
    statuses: dict[str, int] = {}
    for c in cases:
        v = c.values.get(metric, NO_SOURCE)
        statuses[v.status] = statuses.get(v.status, 0) + 1
        if v.status == "observed" and v.value is not None:
            groups.setdefault(v.group or "", []).append((c.ref, float(v.value)))
    out: dict[str, float | None] = {}
    ns: dict[str, int] = {}
    for g in sorted(groups):
        items = groups[g]
        n = len(items)
        ns[g or "all"] = n
        vals = sorted(v for _, v in items)
        for ref, x in items:
            if n < MIN_POPULATION:
                out[ref] = None
                continue
            lo = bisect.bisect_left(vals, x)
            eq = bisect.bisect_right(vals, x) - lo
            out[ref] = 100.0 * (lo + 0.5 * eq) / n
    reference = len(cases)
    unknown = statuses.get("unknown", 0)
    info = {
        "reference_n": reference,
        "n_by_group": ns,
        "statuses": dict(sorted(statuses.items())),
        "small_population": [g for g, n in ns.items() if n < MIN_POPULATION],
        "over_half_unknown": reference > 0 and unknown / reference > 0.5,
    }
    return out, info


def _substitute_community(c: CaseInput, d: Definition, dim: str) -> bool:
    """ADR-053.2 `no_measurable_adoption: use_community_as_primary_and_flag`."""
    return (
        dim == "adoption"
        and d.primary == "adoption"
        and d.no_measurable_adoption == "use_community_as_primary_and_flag"
        and c.values.get(d.metrics["adoption"], NO_SOURCE).status == "not_applicable"
    )


def _check(
    c: CaseInput, dim: str, floor: float, d: Definition, pct: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    metric = d.metrics[dim]
    if _substitute_community(c, d, dim):
        metric = d.metrics["community"]
    v = c.values.get(metric, NO_SOURCE)
    rec: dict[str, Any] = {"dimension": dim, "metric": metric, "floor": _r(floor)}
    rec["status"] = v.status
    if metric != d.metrics[dim]:
        rec["substituted_for"] = d.metrics[dim]
    if v.status == "not_applicable":
        skip = d.if_not_applicable.get(dim) == "skip" or (
            dim == "adoption" and d.no_measurable_adoption == "skip_threshold"
        )
        rec["result"] = "waived" if skip else "fail"
        rec["reason"] = "not_applicable"
        return rec
    if v.status != "observed" or v.value is None:
        rec["result"] = "undetermined"
        rec["reason"] = v.status if v.reason is None else f"{v.status}:{v.reason}"
        return rec
    p = pct.get(metric, {}).get(c.ref)
    rec["percentile"] = _r(p)
    if p is None:
        rec["result"] = "undetermined"
        rec["reason"] = "small_population"
    elif (v.value if v.floor_basis is None else v.floor_basis) <= 0:
        rec["result"] = "fail"
        rec["reason"] = "zero_floor"
    else:
        rec["result"] = "pass" if p >= floor - 1e-9 else "fail"
    return rec


def _business_check(c: CaseInput, signal: str, d: Definition) -> dict[str, Any]:
    v = c.business.get(signal, NO_SOURCE)
    rec: dict[str, Any] = {"dimension": "business", "metric": f"biz.{signal}", "status": v.status}
    if v.status != "observed" or v.value is None:
        rec["result"] = "undetermined"
        rec["reason"] = v.status if v.reason is None else f"{v.status}:{v.reason}"
    elif v.tag == "self_reported" and not d.accept_self_reported:
        rec["result"] = "undetermined"
        rec["reason"] = "self_reported_not_accepted"
    else:
        rec["result"] = "pass" if v.value > 0 else "fail"
    return rec


def _score(
    c: CaseInput, d: Definition, pct: Mapping[str, Mapping[str, float | None]]
) -> tuple[float | None, str]:
    def dim_pct(dim: str) -> float | None:
        metric = d.metrics["community"] if _substitute_community(c, d, dim) else d.metrics[dim]
        v = c.values.get(metric, NO_SOURCE)
        if v.status != "observed":
            return None
        return pct.get(metric, {}).get(c.ref)

    if d.rank_by is None and d.weights:
        total = 0.0
        for dim, w in sorted(d.weights.items()):
            p = dim_pct(dim)
            if p is None:
                return None, "composite"
            total += w * p
        return total, "composite"
    dim = d.rank_by or d.primary
    if dim == "business":
        v = c.values.get(BUSINESS_COUNT, NO_SOURCE)
        ok = v.status == "observed" and v.value is not None
        return (float(v.value) if ok and v.value is not None else None), BUSINESS_COUNT
    basis = d.metrics["community"] if _substitute_community(c, d, dim) else d.metrics[dim]
    return dim_pct(dim), basis


def evaluate(d: Definition, population: Sequence[CaseInput]) -> Evaluation:
    """Percentiles over `population` and each population case's qualification and score. When
    the definition uses view A's derived metrics, they are computed from this population first
    (`follow_through_fit`), so every population the definition is evaluated on gets its own
    fit (ADR-083)."""
    derived: dict[str, dict[str, Value]] = {}
    fit: dict[str, Any] | None = None
    if {FT_RESID, FT_LOGRATIO} & set(d.metrics.values()):
        derived, fit = follow_through_fit(population)
        population = [replace(c, values={**c.values, **derived[c.ref]}) for c in population]
    pct: dict[str, dict[str, float | None]] = {}
    pops: dict[str, dict[str, Any]] = {}
    for dim in sorted(d.metrics):
        metric = d.metrics[dim]
        if dim == "business":
            continue
        pct[metric], pops[metric] = percentiles(population, metric)
        if fit is not None and metric in (FT_RESID, FT_LOGRATIO):
            pops[metric]["fit"] = fit
    qual: dict[str, Qual] = {}
    for c in population:
        checks = [
            _check(c, dim, fl, d, pct) for dim, fl in sorted(d.floors.items()) if fl is not None
        ]
        checks += [_business_check(c, s, d) for s in d.business_minimum]
        results = {x["result"] for x in checks}
        outcome: Outcome
        if "fail" in results:
            outcome = "fails"
        elif "undetermined" in results:
            outcome = "undetermined"
        else:
            outcome = "qualifies"
        flags: list[str] = []
        if _substitute_community(c, d, d.primary):
            flags.append("adoption_not_measurable_community_as_primary")
        if any(x["result"] == "waived" for x in checks):
            flags.append("threshold_waived_not_applicable")
        score, basis = _score(c, d, pct)
        qual[c.ref] = Qual(outcome, checks, score, basis, flags)
    return Evaluation(d, pct, pops, qual, derived, fit)


# --- selection at one widening level ------------------------------------------------------------
@dataclass(frozen=True)
class Pair:
    pair_id: int
    panel: Literal["field", "exemplar"]
    winner: str  # the winner, or the exemplar
    loser: str
    round: int
    distance: float | None
    diffs: Mapping[str, float | None]  # standardized difference per balance covariate
    excluded_on: tuple[str, ...]
    same_language_group: bool | None = None  # both sides in one language group (ADR-084)

    @property
    def headline(self) -> bool:
        return self.panel == "field" and not self.excluded_on


@dataclass
class Level:
    distance: int
    definition: Definition
    evaluation: Evaluation
    eligible: list[CaseInput]
    ranked: list[CaseInput]  # rankable qualifiers, in rank order
    unrankable: list[CaseInput]
    loser_pool: list[CaseInput]
    winners: list[CaseInput]
    pairs: list[Pair]
    sds: dict[str, float | None]

    def counts(self) -> dict[str, int]:
        q = self.evaluation.qual
        return {
            "eligible": len(self.eligible),
            "qualifiers": sum(1 for c in self.eligible if q[c.ref].outcome == "qualifies"),
            "rankable_qualifiers": len(self.ranked),
            "winners": len(self.winners),
            "loser_pool": len(self.loser_pool),
            "matched_losers": len(self.pairs),
            "undetermined": sum(1 for c in self.eligible if q[c.ref].outcome == "undetermined"),
        }


def _numeric_covariates(ctx: Context) -> list[str]:
    """The view's numeric balance covariates: LSM and repo age (views plain and A), or repo
    age and stars before launch (view B: pre-launch characteristics only, ADR-083); plus the
    audience band and quarter when they are not exact-matched."""
    covs = list(ctx.view.distance_numeric)
    if "founder_audience_bucket" not in ctx.exact_match:
        covs.append("audience_band")
    if "launch_half_year" not in ctx.exact_match:
        covs.append("launch_quarter")
    return covs


CATEGORICAL = ("language",)  # view plain and A; view B adds "category" (`View.categorical`)
NUMERIC_ALL = ("lsm", "age_log10", "audience_band", "launch_quarter", "prelaunch_log")


def _num(c: CaseInput, cov: str) -> float | None:
    x = c.covariates
    if cov == "audience_band":
        b = BAND_ORDINAL.get(x.audience_band)
        return None if b is None else float(b)
    v = getattr(x, cov)
    return None if v is None else float(v)


def _cat(c: CaseInput, cov: str) -> str | None:
    """A categorical covariate: the primary language, or the category (view B): `core` for the
    core field (relevance distance 0), `adjacent` for a widening step (ADR-083; the only
    category pigtail has for every candidate)."""
    if cov == "category":
        return "core" if c.distance == 0 else "adjacent"
    if cov == "language_group":
        return c.covariates.language_group
    v = getattr(c.covariates, cov)
    return None if v is None else str(v)


def _exact_key(c: CaseInput, key: str) -> Any:
    x = c.covariates
    return {
        "founder_audience_bucket": x.audience_band,
        "audience_bucket": x.audience_band,
        "launch_half_year": x.launch_half_year,
        "launch_period": x.launch_half_year,
        "launch_type": x.launch_type,
    }[key]


def _sd(values: Iterable[float | None]) -> float | None:
    vs = [v for v in values if v is not None]
    return statistics.stdev(vs) if len(vs) >= 2 else None


def _std_diff(a: float | None, b: float | None, sd: float | None) -> float | None:
    if a is None or b is None:
        return None
    delta = abs(a - b)
    if not sd:
        return 0.0 if delta == 0 else math.inf
    return delta / sd


def _pair_diffs(
    w: CaseInput, lo: CaseInput, ctx: Context, sds: Mapping[str, float | None]
) -> tuple[dict[str, float | None], tuple[str, ...]]:
    """Standardized difference of one pair per balance covariate, and the covariates on which it
    exceeds `headline_exclusion_smd` (ADR-054.1). Since selection-v6 (ADR-084, owner option 1)
    only the numeric covariates can exclude a pair; categorical ones (language, language group,
    category, surface) are recorded as 0 (same) or 1 (different or missing) for the case-level
    view and never exclude. The plain comparison view keeps the selection-v5 rule, where a
    language (or category) mismatch counts as 1 and excludes (ADR-078)."""
    diffs: dict[str, float | None] = {}
    numeric = _numeric_covariates(ctx)
    for cov in numeric:
        diffs[cov] = _std_diff(_num(w, cov), _num(lo, cov), sds.get(cov))
    for cov in ctx.view.balance_categorical:  # missing on either side: a mismatch (ADR-078)
        a, b = _cat(w, cov), _cat(lo, cov)
        diffs[cov] = 0.0 if a is not None and a == b else 1.0
    can_exclude = set(diffs) if ctx.view.categorical_excludes else set(numeric)
    excluded = tuple(
        k
        for k, v in sorted(diffs.items())
        if k in can_exclude and v is not None and v > ctx.headline_exclusion_smd
    )
    return diffs, excluded


def _caliper_ok(w: CaseInput, lo: CaseInput, sds: Mapping[str, float | None], ctx: Context) -> bool:
    """The view's calipers (`View.caliper_covariates`: LSM for views plain and A; repo age and
    stars before launch for view B) and `|Δquarter| <= 1`. A value missing on either side
    fails the caliper (it can't be checked)."""
    for cov, width in ctx.view.caliper_covariates:
        a, b = _num(w, cov), _num(lo, cov)
        if a is None or b is None:
            return False
        sd = sds.get(cov)
        if abs(a - b) > (width * sd if sd else 0.0) + 1e-12:
            return False
    qa, qb = w.covariates.launch_quarter, lo.covariates.launch_quarter
    return qa is not None and qb is not None and abs(qa - qb) <= QUARTER_CALIPER


def _distance(w: CaseInput, lo: CaseInput, ctx: Context, sds: Mapping[str, float | None]) -> float:
    """outcome-model §5.6: standardized |Δ| per numeric covariate of the view (a missing value
    costs one SD), + |Δquarter| + 1 per categorical covariate that differs or is missing
    (language; view B also the category). Repo age at T (`age_log10`) is a numeric term in
    every view; LSM only in views plain and A, stars before launch only in view B."""
    d = 0.0
    covs = list(ctx.view.distance_numeric)
    if "founder_audience_bucket" not in ctx.exact_match:
        covs.append("audience_band")
    for cov in covs:
        s = _std_diff(_num(w, cov), _num(lo, cov), sds.get(cov))
        d += 1.0 if s is None or math.isinf(s) else s
    qa, qb = w.covariates.launch_quarter, lo.covariates.launch_quarter
    d += abs(qa - qb) if qa is not None and qb is not None else 1.0
    for cov in ctx.view.categorical:  # missing counts as different (ADR-078)
        a, b = _cat(w, cov), _cat(lo, cov)
        d += 0.0 if a is not None and a == b else 1.0
    return d


def match_losers(
    winners: Sequence[CaseInput],
    pool: Sequence[CaseInput],
    ctx: Context,
    *,
    prefer_headline: bool = True,
    sds: Mapping[str, float | None] | None = None,
) -> tuple[list[Pair], dict[str, float | None]]:
    """R4.3 / ADR-054.1 nearest-neighbour matching without replacement, refined by ADR-078
    (before any outcome sort; outcome-model §5.6 allows it).

    Eligible losers are those with the same `exact_match` keys inside the calipers. Round 1:
    each winner in rank order takes one eligible loser, preferring one whose pair passes the
    headline rule (ADR-054.1), then the nearest by distance, then the hash. Further rounds (while
    fewer than `panel.losers` are matched) first hand out only headline-passing losers, then any
    eligible loser, so extra losers don't crowd out pairs that can be reported in the headline
    and every matchable winner still gets one loser first. `prefer_headline=False` is the
    selection-v1 rule (nearest only, every round), kept for the ADR-078 comparison.

    `sds` are the SDs of the calipers and standardized differences: since selection-v6 the full
    shortlisted pool's (`pool_sds`, ADR-084); None computes them over winners and pool (the
    selection-v5 rule, kept for the plain comparison view)."""
    if sds is None:
        sds = pool_sds([*winners, *pool])
    sds = dict(sds)
    available = sorted(pool, key=lambda c: c.ref)
    used: set[str] = set()
    pairs: list[Pair] = []
    groups: dict[str, int] = {}
    rnd = 1

    def one_round(headline_only: bool) -> bool:
        progress = False
        for w in winners:
            if len(pairs) >= ctx.losers:
                break
            best: tuple[tuple[int, float, str], CaseInput] | None = None
            for cand in available:
                if cand.ref in used:
                    continue
                if any(_exact_key(w, k) != _exact_key(cand, k) for k in ctx.exact_match):
                    continue
                if not _caliper_ok(w, cand, sds, ctx):
                    continue
                _, excl = _pair_diffs(w, cand, ctx, sds)
                if headline_only and excl:
                    continue
                miss = 1 if prefer_headline and excl else 0
                key = (miss, round(_distance(w, cand, ctx, sds), 12), ctx.tie(cand.ref))
                if best is None or key < best[0]:
                    best = (key, cand)
            if best is None:
                continue
            (_, dist, _), loser = best
            used.add(loser.ref)
            diffs, excluded = _pair_diffs(w, loser, ctx, sds)
            gid = groups.setdefault(w.ref, len(groups) + 1)  # a winner and its losers
            same = w.covariates.language_group == loser.covariates.language_group
            pairs.append(Pair(gid, "field", w.ref, loser.ref, rnd, dist, diffs, excluded, same))
            progress = True
        return progress

    one_round(headline_only=False)  # round 1: every matchable winner gets a loser first
    for headline_only in (True, False) if prefer_headline else (False,):
        while len(pairs) < ctx.losers:
            rnd += 1
            if not one_round(headline_only):
                rnd -= 1  # nothing matched in this round: its number is reused
                break
    return pairs, sds


def pool_sds(cases: Iterable[CaseInput]) -> dict[str, float | None]:
    """SD per numeric covariate over `cases` with an observed value (sample SD; None below 2
    values). With the brief's full shortlisted pool (field and reference repos) this is the
    selection-v6 caliper basis (`SD_RULE`, ADR-084)."""
    cs = list(cases)
    return {cov: _sd(_num(c, cov) for c in cs) for cov in NUMERIC_ALL}


def select_level(
    cases: Sequence[CaseInput],
    ev: Evaluation,
    distance: int,
    ctx: Context,
    sds: Mapping[str, float | None] | None = None,
) -> Level:
    q = ev.qual
    eligible = [
        c
        for c in cases
        if c.ref in q and c.panel in ("field", "reference") and c.distance <= distance
    ]
    quals = [c for c in eligible if q[c.ref].outcome == "qualifies"]
    ranked = sorted(
        (c for c in quals if q[c.ref].score is not None),
        key=lambda c: (-(q[c.ref].score or 0.0), ctx.tie(c.ref)),
    )
    unrankable = [c for c in quals if q[c.ref].score is None]
    winners = ranked[: ctx.winners]
    pool = [c for c in eligible if q[c.ref].outcome == "fails"]
    pairs, used_sds = match_losers(winners, pool, ctx, sds=sds)
    return Level(
        distance, ev.definition, ev, eligible, ranked, unrankable, pool, winners, pairs, used_sds
    )


def apply_step(d: Definition, step: str) -> Definition:
    """ADR-053.2 fallback steps (validated against the brief by `Success`)."""
    floors = dict(d.floors)
    if step == "relax_primary_to_top_third":
        floors[d.primary] = THRESHOLD_PERCENTILE["top_third"]
        return replace(d, floors=floors)
    dim = step.removeprefix("drop_").removesuffix("_minimum")
    if dim == "business":
        return replace(d, business_minimum=())
    floors.pop(dim, None)
    return replace(d, floors=floors)


# --- exemplar panel (ADR-057.1) -----------------------------------------------------------------
def match_exemplars(
    exemplars: Sequence[CaseInput], pool: Sequence[CaseInput], ctx: Context, first_id: int
) -> tuple[list[Pair], list[dict[str, Any]]]:
    """1-2 losers per exemplar, exactly matched on `match_on` (never on field), nearest LSM
    first (view B: nearest stars before launch, since launch size is its outcome, ADR-083),
    ties by the hash; losers are not reused."""
    order = ctx.view.exemplar_order
    used: set[str] = set()
    pairs: list[Pair] = []
    log: list[dict[str, Any]] = []
    for ex in sorted(
        exemplars, key=lambda c: (c.named_index if c.named_index is not None else 99, c.ref)
    ):
        entry: dict[str, Any] = {"named_index": ex.named_index, "matched": 0}
        if ex.anchor is None:
            entry["reason"] = "no_anchor"
            log.append(entry)
            continue
        options = [
            c
            for c in sorted(pool, key=lambda c: c.ref)
            if c.ref not in used
            and all(_exact_key(ex, k) == _exact_key(c, k) for k in ctx.exemplar_match_on)
        ]

        def gap(c: CaseInput, ex: CaseInput = ex) -> float:
            a, b = _num(ex, order), _num(c, order)
            return math.inf if a is None or b is None else abs(a - b)

        options.sort(key=lambda c: (gap(c), ctx.tie(c.ref)))
        gid = first_id + len({p.winner for p in pairs})  # an exemplar and its losers
        for c in options[: ctx.losers_per_exemplar]:
            used.add(c.ref)
            g = gap(c)
            pairs.append(
                Pair(
                    gid,
                    "exemplar",
                    ex.ref,
                    c.ref,
                    1,
                    None if math.isinf(g) else g,
                    {f"{order}_abs": None if math.isinf(g) else g},
                    (),
                )
            )
            entry["matched"] += 1
        if entry["matched"] < ctx.losers_per_exemplar:
            entry["reason"] = "too_few_exact_matches"
        log.append(entry)
    return pairs, log


# --- balance diagnostics (§5.6, §9.2) -----------------------------------------------------------
def _var(xs: Sequence[float]) -> float:
    return statistics.variance(xs) if len(xs) >= 2 else 0.0


def smd_numeric(w: Sequence[float], lo: Sequence[float]) -> dict[str, Any]:
    if not w or not lo:
        return {"smd": None, "variance_ratio": None, "n_winners": len(w), "n_losers": len(lo)}
    mw, ml = statistics.fmean(w), statistics.fmean(lo)
    vw, vl = _var(w), _var(lo)
    pooled = math.sqrt((vw + vl) / 2)
    smd = (mw - ml) / pooled if pooled > 0 else (0.0 if mw == ml else None)
    return {
        "smd": _r(smd),
        "variance_ratio": _r(vw / vl) if vl > 0 else None,
        "mean_winners": _r(mw),
        "mean_losers": _r(ml),
        "n_winners": len(w),
        "n_losers": len(lo),
    }


def smd_categorical(w: Sequence[str], lo: Sequence[str]) -> dict[str, Any]:
    if not w or not lo:
        return {"smd": None, "levels": {}, "n_winners": len(w), "n_losers": len(lo)}
    levels: dict[str, Any] = {}
    worst = 0.0
    for lv in sorted(set(w) | set(lo)):
        pw, pl = w.count(lv) / len(w), lo.count(lv) / len(lo)
        den = math.sqrt((pw * (1 - pw) + pl * (1 - pl)) / 2)
        s = (pw - pl) / den if den > 0 else (0.0 if pw == pl else None)
        levels[lv] = {"p_winners": _r(pw), "p_losers": _r(pl), "smd": _r(s)}
        worst = math.inf if s is None else max(worst, abs(s))
    return {
        "smd": None if math.isinf(worst) else _r(worst),
        "levels": levels,
        "n_winners": len(w),
        "n_losers": len(lo),
        "form": "max |SMD| over levels",
    }


def smd_multilabel(w: Sequence[tuple[str, ...]], lo: Sequence[tuple[str, ...]]) -> dict[str, Any]:
    """Per-level SMD of a set-valued covariate (install paths, ADR-084): each level is the
    share of units whose set contains it, compared like a binary covariate; the row's SMD is
    the largest |SMD| over the levels."""
    if not w or not lo:
        return {"smd": None, "levels": {}, "n_winners": len(w), "n_losers": len(lo)}
    levels: dict[str, Any] = {}
    worst = 0.0
    for lv in sorted({x for s in [*w, *lo] for x in s}):
        pw = sum(1 for s in w if lv in s) / len(w)
        pl = sum(1 for s in lo if lv in s) / len(lo)
        den = math.sqrt((pw * (1 - pw) + pl * (1 - pl)) / 2)
        s_ = (pw - pl) / den if den > 0 else (0.0 if pw == pl else None)
        levels[lv] = {"p_winners": _r(pw), "p_losers": _r(pl), "smd": _r(s_)}
        worst = math.inf if s_ is None else max(worst, abs(s_))
    return {
        "smd": None if math.isinf(worst) else _r(worst),
        "levels": levels,
        "n_winners": len(w),
        "n_losers": len(lo),
        "form": "max |SMD| over levels; a level is 'the set contains it'",
    }


def _cov_balance(ws: Sequence[CaseInput], ls: Sequence[CaseInput], ctx: Context) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for cov in _numeric_covariates(ctx):
        wv = [v for c in ws if (v := _num(c, cov)) is not None]
        lv = [v for c in ls if (v := _num(c, cov)) is not None]
        out[cov] = smd_numeric(wv, lv)
    for cov in ctx.view.balance_categorical:
        wc = [s for c in ws if (s := _cat(c, cov))]
        lc = [s for c in ls if (s := _cat(c, cov))]
        out[cov] = smd_categorical(wc, lc)
    for cov in ctx.view.balance_multilabel:  # install_path
        out[cov] = smd_multilabel(
            [c.covariates.install_paths for c in ws], [c.covariates.install_paths for c in ls]
        )
    numeric = set(_numeric_covariates(ctx))
    for k, rec in out.items():
        s = rec["smd"]
        rec["meets_target"] = None if s is None else abs(s) < ctx.smd_target
        rec["label"] = "balance_limited" if rec["meets_target"] is False else None
        rec["kind"] = "numeric" if k in numeric else "categorical"
        rec["excludes_pairs"] = k in numeric or ctx.view.categorical_excludes
    return out


def same_language_group_subset(
    lvl: Level, by_ref: Mapping[str, CaseInput], ctx: Context
) -> dict[str, Any]:
    """The per-view sensitivity alternative "same-language-group pairs only" (ADR-084): the
    view's headline pairs restricted to pairs whose two sides share a language group, with
    their counts and balance (each matched winner counted once, as in `balance`)."""
    head = [p for p in lvl.pairs if p.panel == "field" and p.headline]
    same = [p for p in head if p.same_language_group]
    ws = [by_ref[r] for r in sorted({p.winner for p in same})]
    ls = [by_ref[p.loser] for p in same]
    return {
        "key": "pairs:same_language_group",
        "kind": "pair_subset",
        "ran": True,
        "rule": "headline pairs whose winner and loser share a language group (js_ts, python, "
        "go, rust, other)",
        "headline_pairs": len(head),
        "same_group_headline_pairs": len(same),
        "matched_winners": len(ws),
        "by_group": _count(by_ref[p.winner].covariates.language_group for p in same),
        "balance": _cov_balance(ws, ls, ctx) if same else {},
        "pattern_rule": PATTERN_LANGUAGE_RULE,
    }


def balance(lvl: Level, by_ref: Mapping[str, CaseInput], ctx: Context) -> dict[str, Any]:
    pairs = [p for p in lvl.pairs if p.panel == "field"]
    matched_w = [by_ref[r] for r in sorted({p.winner for p in pairs})]
    matched_l = [by_ref[p.loser] for p in pairs]
    exact: dict[str, int] = {}
    for k in ctx.exact_match:
        exact[k] = sum(
            1 for p in pairs if _exact_key(by_ref[p.winner], k) != _exact_key(by_ref[p.loser], k)
        )
    excluded_by: dict[str, int] = {}
    for p in pairs:
        for k in p.excluded_on:
            excluded_by[k] = excluded_by.get(k, 0) + 1
    after = _cov_balance(matched_w, matched_l, ctx)
    rule = (
        f"a pair differing by > {ctx.headline_exclusion_smd:g} SD on a numeric covariate is "
        "shown only in the case-level view; categorical covariates never exclude (ADR-054.1, "
        "ADR-084)"
        if not ctx.view.categorical_excludes
        else f"a pair differing by > {ctx.headline_exclusion_smd:g} SD on any covariate "
        "(a language mismatch or a missing language counts as 1) is shown only in the "
        "case-level view (ADR-054.1, ADR-078)"
    )
    out: dict[str, Any] = {
        "view": ctx.view.key,
        "covariates": [
            *_numeric_covariates(ctx),
            *ctx.view.balance_categorical,
            *ctx.view.balance_multilabel,
        ],
        "form": "standardized mean difference, pooled SD; variance ratio; no p-values",
        "winner_counting": "after matching, each matched winner counts once, even when it has "
        "two losers (unweighted, ADR-078)",
        "target": ctx.smd_target,
        "after_matching": after,
        "before_matching": _cov_balance(lvl.winners, lvl.loser_pool, ctx),
        "covariates_missing_target": sorted(
            k for k, v in after.items() if v["meets_target"] is False
        ),
        "exact_match": {
            "keys": list(ctx.exact_match),
            "violations": exact,
            "ok": all(v == 0 for v in exact.values()),
        },
        "pairs": len(pairs),
        "headline_pairs": sum(1 for p in pairs if p.headline),
        "headline_excluded": {
            "rule": rule,
            "pairs": sum(1 for p in pairs if not p.headline),
            "by_covariate": dict(sorted(excluded_by.items())),
        },
        "unmatched_winners": sum(1 for c in lvl.winners if c.ref not in {p.winner for p in pairs}),
        "sd": {k: _r(v) for k, v in sorted(lvl.sds.items())},
        "sd_basis": ctx.view.sd_basis,
    }
    if ctx.view.key != "plain":
        out["same_language_group"] = same_language_group_subset(lvl, by_ref, ctx)
    return out


# --- sensitivity (§8, R4.9) ---------------------------------------------------------------------
def _ladder_step(floor: float, direction: int) -> tuple[bool, float | None]:
    """One step looser (-1) or tighter (+1) on {none, 25, 50, 75, 90, 95}; `top_third` sits
    between 50 and 75. Returns (possible, new floor)."""
    numeric = [x for x in LADDER if x is not None]
    if direction < 0:
        below = [x for x in numeric if x < floor - 1e-9]
        return True, (below[-1] if below else None)
    above = [x for x in numeric if x > floor + 1e-9]
    return (True, above[0]) if above else (False, None)


def alternatives(
    d: Definition, ctx: Context, base: Definition | None = None
) -> tuple[list[tuple[str, Definition, bool]], list[dict[str, Any]]]:
    """(key, definition, exclude_flagged) per alternative that runs, plus notes for the ones that
    don't apply (§8.1), each with its reason. `base` is the brief's own definition: a minimum a
    fallback step dropped (ADR-053.2) gets a note saying so instead of silently disappearing
    (M22 verifier round 2)."""
    runs: list[tuple[str, Definition, bool]] = []
    notes: list[dict[str, Any]] = []
    if "band_shift" in ctx.sensitivity and base is not None:
        for dim, fl in sorted(base.floors.items()):
            if fl is not None and d.floors.get(dim) is None:
                notes.append(
                    {
                        "key": f"band_shift:{dim}",
                        "ran": False,
                        "reason": f"not applicable: the {dim} minimum was dropped by the fallback "
                        f"step drop_{dim}_minimum (ADR-053.2)",
                    }
                )
    if "primary_swap" in ctx.sensitivity:
        ranked_on = d.rank_by or d.primary
        for other in RANKABLE:
            if other != ranked_on:
                runs.append((f"primary_swap:{other}", replace(d, rank_by=other), False))
    if "band_shift" in ctx.sensitivity:
        numeric = {k: v for k, v in sorted(d.floors.items()) if v is not None}
        loose_all: dict[str, float | None] = dict(d.floors)
        tight_all: dict[str, float | None] = dict(d.floors)
        for dim, fl in numeric.items():
            for name, direction in (("looser", -1), ("tighter", 1)):
                ok, new = _ladder_step(fl, direction)
                if not ok:
                    notes.append(
                        {
                            "key": f"band_shift:{dim}:{name}",
                            "ran": False,
                            "reason": "not applicable: already at the top of the ladder",
                        }
                    )
                    continue
                runs.append(
                    (f"band_shift:{dim}:{name}", replace(d, floors={**d.floors, dim: new}), False)
                )
                (loose_all if direction < 0 else tight_all)[dim] = new
        if numeric:
            runs.append(("band_shift:all:looser", replace(d, floors=loose_all), False))
            if tight_all != dict(d.floors):
                runs.append(("band_shift:all:tighter", replace(d, floors=tight_all), False))
        else:
            notes.append(
                {
                    "key": "band_shift",
                    "ran": False,
                    "reason": "not applicable: no percentile threshold in the final definition",
                }
            )
    if "weights" in ctx.sensitivity:
        if d.weights:
            runs.append(("weights:primary_only", replace(d, weights=None), False))
            eq = {k: 1.0 / len(d.weights) for k in sorted(d.weights)}
            runs.append(("weights:equal", replace(d, weights=eq), False))
        else:
            notes.append(
                {
                    "key": "weights",
                    "ran": False,
                    "reason": "not applicable: the brief uses no weights",
                }
            )
    # the view's own metric alternatives (ADR-083: view A, log-ratio and plain att.stars@30);
    # part of the selection version, so they run whatever the brief's sensitivity list says
    attention_used = "attention" in {d.primary, *d.floors, *(d.weights or {})}
    for m in ctx.view.extra_alternatives:
        key = f"metric:attention:{m}"
        if attention_used:
            runs.append((key, replace(d, metrics={**d.metrics, "attention": m}), False))
        else:
            notes.append(
                {
                    "key": key,
                    "ran": False,
                    "reason": "not applicable: attention is not in the final definition",
                }
            )
    if "fake_star_filter" in ctx.sensitivity:
        if d.star_metric_used():
            runs.append(("exclude_anomaly_flagged", d, True))
        else:
            notes.append(
                {
                    "key": "exclude_anomaly_flagged",
                    "ran": False,
                    "reason": "D not applicable: no star metric in the success definition",
                }
            )
    return runs, notes


def _status(c: CaseInput, ev: Evaluation, winners: set[str], excluded: set[str]) -> str:
    if c.ref in excluded:
        return "excluded_anomaly_flagged"
    if c.ref in winners:
        return "winner"
    q = ev.qual.get(c.ref)
    if q is None:
        return "not_in_population"
    if q.outcome == "qualifies":
        return "qualifier" if q.score is not None else "unrankable"
    return "non_qualifier" if q.outcome == "fails" else "undetermined"


def jaccard(a: set[str], b: set[str]) -> float | None:
    u = a | b
    return None if not u else len(a & b) / len(u)


def sensitivity(
    cases: Sequence[CaseInput],
    population: Sequence[CaseInput],
    lvl: Level,
    ctx: Context,
    base: Definition | None = None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Summary (counts only) and per-case statuses and flags (§8.2, §8.3)."""
    base_w = {c.ref for c in lvl.winners}
    base_l = {p.loser for p in lvl.pairs if p.panel == "field"}
    per_case: dict[str, dict[str, Any]] = {
        r: {"status": {}, "flags": [], "changed_by": []} for r in sorted(base_w | base_l)
    }
    by_ref = {c.ref: c for c in cases}
    runs, notes = alternatives(lvl.definition, ctx, base)
    results: list[dict[str, Any]] = list(notes)
    overlaps: list[float] = []
    stable = set(base_w)
    for key, d, exclude in runs:
        excluded = {c.ref for c in population if exclude and c.star_anomaly_flag == "true"}
        if exclude and not excluded:
            results.append(
                {"key": key, "ran": False, "reason": "not applicable: no flagged candidates"}
            )
            continue
        pop = [c for c in population if c.ref not in excluded]
        ev = evaluate(d, pop)
        checked = None
        if d.rank_by is not None and d.rank_by != "business":
            checked = d.metrics[d.rank_by]
        elif key.startswith("metric:attention:"):
            checked = d.metrics["attention"]
        if checked is not None:
            observed = ev.populations.get(checked, {}).get("statuses", {}).get("observed", 0)
            if not observed:
                results.append(
                    {
                        "key": key,
                        "ran": False,
                        "reason": f"not applicable: no observed {checked} value in the "
                        "population (no connector or no data)",
                    }
                )
                continue
        alt = select_level(pop, ev, lvl.distance, replace(ctx, losers=0))
        wins = {c.ref for c in alt.winners}
        j = jaccard(base_w, wins)
        if j is not None:
            overlaps.append(j)
        stable &= wins
        results.append(
            {
                "key": key,
                "ran": True,
                "qualifiers": alt.counts()["qualifiers"],
                "rankable_qualifiers": len(alt.ranked),
                "winners": len(wins),
                "jaccard": _r(j),
                "left_baseline": len(base_w - wins),
                "joined": len(wins - base_w),
                "excluded_anomaly_flagged": len(excluded),
            }
        )
        for r, rec in per_case.items():
            st = _status(by_ref[r], ev, wins, excluded)
            rec["status"][key] = st
            changed = (r in base_w and st != "winner") or (
                r in base_l and st in ("winner", "qualifier", "unrankable")
            )
            if st == "excluded_anomaly_flagged":
                if "excluded_anomaly_flagged" not in rec["flags"]:
                    rec["flags"].append("excluded_anomaly_flagged")
            elif changed:
                rec["changed_by"].append(key)
                flag = "sensitive_to_star_anomaly" if exclude else "definition_sensitive"
                if flag not in rec["flags"]:
                    rec["flags"].append(flag)
    ran = [r for r in results if r.get("ran")]
    summary = {
        "alternatives": results,
        "ran": len(ran),
        "min_jaccard": _r(min(overlaps)) if overlaps else None,
        "mean_jaccard": _r(statistics.fmean(overlaps)) if overlaps else None,
        "share_winners_stable": (_r(len(stable) / len(base_w)) if base_w and ran else None),
        "definition_sensitive": sum(
            1 for v in per_case.values() if "definition_sensitive" in v["flags"]
        ),
        "sensitive_to_star_anomaly": sum(
            1 for v in per_case.values() if "sensitive_to_star_anomaly" in v["flags"]
        ),
        "excluded_anomaly_flagged": sum(
            1 for v in per_case.values() if "excluded_anomaly_flagged" in v["flags"]
        ),
        "note": "descriptive; losers are not re-matched; the baseline winner set never changes",
    }
    for v in per_case.values():
        v["flags"].sort()
    return summary, per_case


# --- the whole selection ------------------------------------------------------------------------
@dataclass
class Selection:
    params: dict[str, Any]
    base_definition: Definition
    level: Level
    steps: list[dict[str, Any]]
    exemplar_pairs: list[Pair]
    exemplar_log: list[dict[str, Any]]
    cases: list[dict[str, Any]]
    summary: dict[str, Any]
    balance: dict[str, Any]
    sensitivity: dict[str, Any]
    inputs_hash: str
    result_hash: str = ""
    view_cases: list[CaseInput] = field(default_factory=list)  # the inputs as this view read them

    def to_dict(self) -> dict[str, Any]:
        return {
            "params": self.params,
            "summary": self.summary,
            "balance": self.balance,
            "sensitivity": self.sensitivity,
            "cases": self.cases,
            "inputs_hash": self.inputs_hash,
            "result_hash": self.result_hash,
        }


def _r(x: float | None) -> float | None:
    if x is None:
        return None
    if math.isinf(x) or math.isnan(x):
        return None
    return round(float(x), ROUND)


def _input_row(c: CaseInput) -> dict[str, Any]:
    row: dict[str, Any] = {
        "ref": c.ref,
        "panel": c.panel,
        "distance": c.distance,
        "named_index": c.named_index,
        "anchor": None if c.anchor is None else c.anchor.to_dict(),
        "anchor_reason": c.anchor_reason,
        "values": {k: v.to_dict() for k, v in sorted(c.values.items())},
        "business": {k: v.to_dict() for k, v in sorted(c.business.items())},
        "covariates": c.covariates.to_dict(),
        "star_anomaly_flag": c.star_anomaly_flag,
    }
    if c.relaunch_events:
        row["relaunch_events"] = [dict(e) for e in c.relaunch_events]
    if c.pre_window_launch is not None:
        row["pre_window_launch"] = dict(c.pre_window_launch)
    if c.launch_case is not None:
        row["launch_case"] = _input_row(c.launch_case)
    return row


def inputs_hash(cases: Sequence[CaseInput]) -> str:
    """SHA-256 of the canonical inputs (order-independent), view B's launch-event cases
    included (ADR-084)."""
    return sha256_json([_input_row(c) for c in sorted(cases, key=lambda c: c.ref)])


def anchor_rule(c: CaseInput) -> str:
    """View B's anchor rule label of a case (`ANCHOR_RULE_LABELS`; `none` without an anchor)."""
    if c.anchor is None:
        return "none"
    return c.anchor.rule or c.anchor.source


def anchor_rule_counts(cases: Iterable[CaseInput]) -> dict[str, int]:
    """How many cases each view-B anchor rule anchored (every label shown, zeros included), and
    of the cases without an anchor (`none`), those launched before the window, by the kind of
    their first declared launch event (`launched_before_window:<kind>`, ADR-085 addendum 3)."""
    counts = dict.fromkeys(ANCHOR_COUNT_LABELS, 0)
    for c in cases:
        r = anchor_rule(c)
        counts[r] = counts.get(r, 0) + 1
        if c.anchor is None and c.anchor_reason == PRE_WINDOW_REASON:
            kind = str((c.pre_window_launch or {}).get("kind") or "unknown")
            k = f"{PRE_WINDOW_REASON}:{kind}"
            counts[k] = counts.get(k, 0) + 1
    return counts


def in_population(c: CaseInput, view: View) -> bool:
    """Whether an anchored field or reference case belongs to the view's population."""
    if c.anchor is None:
        return False
    if view.population == "declared_launch":
        return anchor_rule(c) in DECLARED_RULES
    if view.population == "undeclared_launch":
        return c.anchor.undeclared
    return True


def _step_entry(step: str, lvl: Level, **extra: Any) -> dict[str, Any]:
    return {
        "step": step,
        "distance": lvl.distance,
        "floors": {d: _r(v) for d, v in sorted(lvl.definition.floors.items())},
        "business_minimum": list(lvl.definition.business_minimum),
        **lvl.counts(),
        **extra,
    }


def _anchor_type(c: CaseInput) -> str | None:
    return None if c.anchor is None else c.anchor.type


def select(
    cases_in: Iterable[CaseInput],
    ctx: Context,
    base: Definition,
    *,
    notes: Sequence[str] = (),
) -> Selection:
    """Outcome sort, winners, matched losers, exemplar losers, balance and sensitivity of one
    view (`ctx.view`, ADR-083; default `VIEW_PLAIN`). `base` is the brief's own definition; the
    view replaces its attention metric (`View.definition`). `notes` are warnings from the input
    stage (for example launch-lookup counts), reported with the selection's own."""
    view = ctx.view
    brief_def = base
    base = view.definition(base)
    cases = sorted(cases_in, key=lambda c: c.ref)
    refs = [c.ref for c in cases]
    if len(set(refs)) != len(refs):
        raise SelectionError("a candidate appears twice in the selection input")
    if view.anchor == "launch_event":  # view B reads its own launch-event anchor (ADR-084)
        cases = [c.for_view_b() for c in cases]
    by_ref = {c.ref: c for c in cases}
    anchored = [c for c in cases if c.panel in ("field", "reference") and c.anchor is not None]
    population = [c for c in anchored if in_population(c, view)]
    in_pop = {c.ref for c in population}
    # SD basis (ADR-084): the brief's full shortlisted pool, every field and reference repo
    # with an observed value, whatever its anchor or role; the plain view keeps winners + pool
    field_pool = [c for c in cases if c.panel in ("field", "reference")]
    sds: dict[str, float | None] | None = None
    if view.sd_basis == "full_pool":
        sds = pool_sds(field_pool)

    # R4.10 then ADR-053.2: every step is logged with its counts (R4.10, ADR-055.5)
    d = base
    lvl = select_level(population, evaluate(d, population), 0, ctx, sds)
    steps = [_step_entry("baseline", lvl, rule="brief success definition, core field (R4.8)")]

    def short(level: Level) -> bool:
        return len(level.winners) < WINNERS_MIN or len(level.pairs) < LOSERS_MIN

    while short(lvl):
        if lvl.distance >= ctx.max_distance:
            if ctx.max_distance == 0:
                steps.append(
                    {
                        "step": "widen",
                        "applied": False,
                        "rule": "R4.10",
                        "reason": "the brief declares no widening steps",
                    }
                )
            break
        reason = (
            f"fewer than {WINNERS_MIN} winners or matched losers "
            f"({len(lvl.winners)}, {len(lvl.pairs)})"
        )
        before = len(lvl.eligible)
        lvl = select_level(population, lvl.evaluation, lvl.distance + 1, ctx, sds)
        added = len(lvl.eligible) - before
        steps.append(
            _step_entry(
                f"widen_to_distance_{lvl.distance}",
                lvl,
                rule="R4.10",
                reason=reason,
                added=added,
                applied=added > 0,
            )
        )
    pending = list(ctx.fallback_steps)
    while len(lvl.ranked) < ctx.min_winners and pending:
        step = pending.pop(0)
        reason = f"fewer than {ctx.min_winners} rankable qualifiers ({len(lvl.ranked)})"
        d = apply_step(lvl.definition, step)
        lvl = select_level(population, evaluate(d, population), lvl.distance, ctx, sds)
        steps.append(_step_entry(step, lvl, rule="ADR-053.2", reason=reason))

    winners = {c.ref for c in lvl.winners}
    field_losers = {p.loser for p in lvl.pairs}
    q = lvl.evaluation.qual
    exemplars = [c for c in cases if c.panel == "exemplar"]
    ex_pool = [
        c
        for c in population
        if q[c.ref].outcome == "fails" and c.ref not in winners and c.ref not in field_losers
    ]
    first_ex = max((p.pair_id for p in lvl.pairs), default=0) + 1
    ex_pairs, ex_log = match_exemplars(exemplars, ex_pool, ctx, first_ex)
    sens_summary, sens_cases = sensitivity(cases, population, lvl, ctx, base)

    rank = {c.ref: i + 1 for i, c in enumerate(lvl.ranked)}
    eligible = {c.ref for c in lvl.eligible}
    unrankable = {c.ref for c in lvl.unrankable}
    # one matched set per winner (or exemplar): the winner row names the set and its size, each
    # loser row carries its own distance and standardized differences to that winner
    # Each side's anchor type is stored so that the report can restrict H1-type contrasts
    # (MC-01) to launch-anchored pairs (ADR-081): losers need a declared launch, winners don't.
    pair_of: dict[str, dict[str, Any]] = {}
    for p in [*lvl.pairs, *ex_pairs]:
        wt, lt = _anchor_type(by_ref[p.winner]), _anchor_type(by_ref[p.loser])
        head = pair_of.setdefault(
            p.winner,
            {
                "pair_id": p.pair_id,
                "panel": p.panel,
                "side": "winner" if p.panel == "field" else "exemplar",
                "losers": 0,
                "anchor_type": wt,
            },
        )
        head["losers"] += 1
        pair_of[p.loser] = {
            "pair_id": p.pair_id,
            "panel": p.panel,
            "side": "loser",
            "anchor_type": lt,
            "anchor_types": {"winner": wt, "loser": lt},
            "launch_anchored": wt == "launch" and lt == "launch",
            "round": p.round,
            "distance": _r(p.distance),
            "diffs": {k: _r(v) for k, v in sorted(p.diffs.items())},
            "headline": p.headline if p.panel == "field" else None,
            "excluded_on": list(p.excluded_on),
            "same_language_group": p.same_language_group,
        }
    ex_losers = {p.loser for p in ex_pairs}
    out_cases: list[dict[str, Any]] = []
    roles: dict[str, int] = dict.fromkeys(ROLES, 0)
    for c in cases:
        role: Role
        if c.panel == "exemplar":
            role = "exemplar"
        elif c.anchor is None:
            role = "no_anchor"
        elif c.ref not in in_pop:
            role = "not_in_view"
        elif c.ref in winners:
            role = "winner"
        elif c.ref in field_losers:
            role = "matched_loser"
        elif c.ref in ex_losers:
            role = "exemplar_matched_loser"
        elif c.ref not in eligible:
            role = "outside_widening"
        elif c.ref in unrankable:
            role = "unrankable"
        elif q[c.ref].outcome == "qualifies":
            role = "qualified_not_selected"
        elif q[c.ref].outcome == "fails":
            role = "loser_pool_unmatched"
        else:
            role = "undetermined"
        roles[role] += 1
        qc = q.get(c.ref)
        out_cases.append(
            {
                "candidate_ref": c.ref,
                "panel": c.panel,
                "distance": c.distance,
                "named_index": c.named_index,
                "is_reference": c.is_reference,
                "role": role,
                "rank": rank.get(c.ref) if c.ref in eligible else None,
                "score": _r(qc.score) if qc else None,
                "rank_basis": qc.rank_basis if qc else None,
                "anchor": None if c.anchor is None else c.anchor.to_dict(),
                "anchor_reason": c.anchor_reason,
                "values": _values_out(c, lvl.evaluation),
                "qualification": None
                if qc is None
                else {"outcome": qc.outcome, "checks": qc.checks, "flags": qc.flags},
                "covariates": c.covariates.to_dict(),
                "star_anomaly": {"flag": c.star_anomaly_flag, **dict(c.anomaly)},
                "pair": pair_of.get(c.ref),
                "sensitivity": sens_cases.get(c.ref),
                **(
                    {
                        "anchor_rule": anchor_rule(c),
                        "undeclared_launch": c.anchor is not None and c.anchor.undeclared,
                        "relaunch_events": [dict(e) for e in c.relaunch_events],
                        **(
                            {"pre_window_launch": dict(c.pre_window_launch)}
                            if c.pre_window_launch is not None
                            else {}
                        ),
                    }
                    if view.anchor == "launch_event"
                    else {}
                ),
            }
        )

    warnings: list[str] = list(notes)
    field_cases = [c for c in cases if c.panel != "exemplar"]
    no_anchor = sum(1 for c in field_cases if c.anchor is None)
    if no_anchor:
        warnings.append(f"no anchor: {no_anchor} of {len(field_cases)} shortlisted")
    ev = lvl.evaluation
    dims_used = [
        d for d in RANKABLE if d in lvl.definition.floors or d in (lvl.definition.weights or {})
    ]
    if lvl.definition.primary in RANKABLE and lvl.definition.primary not in dims_used:
        dims_used.append(lvl.definition.primary)
    for dim in dims_used:
        info = ev.populations.get(lvl.definition.metrics[dim])
        if info is None:
            continue
        ns = dict(info["n_by_group"]) or {"all": 0}
        for g, n in sorted(ns.items()):
            if n < MIN_POPULATION:
                label = dim if g == "all" else f"{dim} ({g})"
                warnings.append(
                    f"{label} population {n} < {MIN_POPULATION} (minimum): no percentiles"
                )
    if len(lvl.winners) < WINNERS_MIN:
        warnings.append(f"fewer winners than the minimum ({WINNERS_MIN}): {len(lvl.winners)}")
    if len(lvl.ranked) < ctx.winners:
        warnings.append(
            f"fewer rankable qualifiers ({len(lvl.ranked)}) than panel.winners ({ctx.winners}); "
            "all of them are winners"
        )
    if len(lvl.pairs) < min(ctx.losers, len(lvl.winners)):
        warnings.append(f"only {len(lvl.pairs)} matched losers for {len(lvl.winners)} winners")
    if len(steps) > 1 and any(s.get("applied", True) for s in steps[1:]):
        warnings.append(
            "the panel was widened or the thresholds relaxed (see steps); core-field findings "
            "are reported separately (R4.10)"
        )
    left_out = len(anchored) - len(population)
    if left_out:
        why = {
            "declared_launch": "their view-B anchor is an undeclared launch (first mention or "
            "first release), reported in launch_undeclared (ADR-084)",
            "undeclared_launch": "their view-B anchor is a declared launch, reported in the "
            "launch view (ADR-084)",
        }.get(view.population, "outside the view's population")
        warnings.append(f"{left_out} anchored cases left out of the {view.key} view: {why}")
    if ev.fit is not None and ev.fit["status"] != "ok":
        warnings.append(
            f"follow-through fit not made ({ev.fit['status']}, n = {ev.fit['n']}): no "
            "residuals, so no percentiles on the follow-through metric (ADR-083)"
        )
    affected = {
        dim: sum(
            1
            for c in population
            if any(
                x["dimension"] == dim and x["result"] == "undetermined"
                for x in ev.qual[c.ref].checks
            )
        )
        for dim in DIMENSIONS
    }
    summary = {
        "brief_id": ctx.brief_id,
        "brief_version": ctx.brief_version,
        "view": view.key,
        "view_label": view.label,
        "shortlist_n": len(cases),
        "anchored_n": len(anchored),
        "reference_population_n": len(population),
        "no_anchor": no_anchor,
        "anchors": _count(
            "none" if c.anchor is None else f"{c.anchor.type}:{c.anchor.via or c.anchor.source}"
            for c in field_cases
        ),
        "pairs_by_anchor": _count(
            f"{_anchor_type(by_ref[p.winner])}/{_anchor_type(by_ref[p.loser])}" for p in lvl.pairs
        ),
        "headline_pairs_launch_anchored": sum(
            1
            for p in lvl.pairs
            if p.headline
            and _anchor_type(by_ref[p.winner]) == "launch"
            and _anchor_type(by_ref[p.loser]) == "launch"
        ),
        "roles": {k: v for k, v in roles.items() if v},
        "sd_basis": view.sd_basis,
        "sd_n": {
            cov: sum(1 for c in field_pool if _num(c, cov) is not None)
            if view.sd_basis == "full_pool"
            else None
            for cov in NUMERIC_ALL
        },
        **(
            {
                "anchor_rules": anchor_rule_counts(field_cases),
                "relaunch_events": sum(len(c.relaunch_events) for c in field_cases),
                "cases_with_relaunch": sum(1 for c in field_cases if c.relaunch_events),
                "limitations": ctx.view_b_limitations(),
                "known_bias": VIEW_B_BIAS,
                "incomplete_sources": _count(
                    str(c.anchor_reason).split(":", 1)[1]
                    for c in field_cases
                    if c.anchor is None and str(c.anchor_reason or "").startswith(INCOMPLETE_PREFIX)
                ),
            }
            if view.anchor == "launch_event"
            else {}
        ),
        "final_distance": lvl.distance,
        "core_field_only": all(by_ref[r].distance == 0 for r in winners | field_losers),
        "brief_definition": brief_def.to_dict(),
        "base_definition": base.to_dict(),
        "final_definition": lvl.definition.to_dict(),
        "steps": steps,
        "counts": lvl.counts(),
        "undetermined_by_dimension": affected,
        "populations": ev.populations,
        "reference_cases": {
            "n": sum(1 for c in cases if c.is_reference),
            "roles": _count(r["role"] for r in out_cases if r["is_reference"]),
        },
        "exemplars": {
            "n": len(exemplars),
            "pairs": len(ex_pairs),
            "log": ex_log,
        },
        "star_anomaly": {
            "label": "unfiltered, anomaly-checked",
            "winners": _count(by_ref[r].star_anomaly_flag for r in sorted(winners)),
            "matched_losers": _count(by_ref[r].star_anomaly_flag for r in sorted(field_losers)),
        },
        "warnings": warnings,
    }
    bal = balance(lvl, by_ref, ctx)
    if "same_language_group" in bal:  # the per-view pair-subset alternative (ADR-084)
        sub = bal["same_language_group"]
        sens_summary["alternatives"].append(
            {k: v for k, v in sub.items() if k not in ("balance", "pattern_rule")}
        )
    sel = Selection(
        params=ctx.params(),
        base_definition=base,
        level=lvl,
        steps=steps,
        exemplar_pairs=ex_pairs,
        exemplar_log=ex_log,
        cases=out_cases,
        summary=summary,
        balance=bal,
        sensitivity=sens_summary,
        inputs_hash=inputs_hash(cases),
        view_cases=cases,
    )
    # the result hash covers the selection, not the input-stage notes (e.g. launch-lookup counts,
    # which are not stored): a recompute from stored data reproduces it (R4.8)
    hashed = {k: v for k, v in sel.to_dict().items() if k != "result_hash"}
    hashed["summary"] = {
        **hashed["summary"],
        "warnings": [w for w in hashed["summary"]["warnings"] if w not in notes],
    }
    sel.result_hash = sha256_json(hashed)
    return sel


def _count(items: Iterable[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for x in items:
        out[x] = out.get(x, 0) + 1
    return dict(sorted(out.items()))


def _values_out(c: CaseInput, ev: Evaluation) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for metric, v in sorted({**c.values, **ev.derived.get(c.ref, {})}.items()):
        rec = v.to_dict()
        if metric in ev.pct:
            rec["percentile"] = _r(ev.pct[metric].get(c.ref))
        out[metric] = rec
    for s, v in sorted(c.business.items()):
        out[f"biz.{s}"] = v.to_dict()
    return out


# --- views A, B and C together (ADR-083) ---------------------------------------------------------
def _stats(xs: Sequence[float]) -> dict[str, Any]:
    if not xs:
        return {"n": 0, "median": None, "mean": None}
    return {"n": len(xs), "median": _r(statistics.median(xs)), "mean": _r(statistics.fmean(xs))}


def context_view(sel: Selection, ctx: Context | None = None) -> dict[str, Any]:
    """View C for one view: its final winners against **every shortlisted non-winner** of the
    brief (ADR-084: field and reference repos that are not winners of this view, whatever
    their role, no-anchor and undetermined repos included; exemplars are outside the field),
    with no matching (descriptive context, never a headline): counts, the median and mean of
    the view's outcome and of the star measures on each side over the observed values, the
    number of non-winners with and without a value per measure, and the covariate SMDs of the
    winners against the non-winners."""
    lvl = sel.level
    ev = lvl.evaluation
    matched = {p.loser for p in lvl.pairs if p.panel == "field"}
    winners = {c.ref for c in lvl.winners}
    field_cases = [c for c in sel.view_cases if c.panel in ("field", "reference")]
    non_winners = [c for c in field_cases if c.ref not in winners]
    primary_metric = lvl.definition.metrics.get(lvl.definition.primary)
    metrics = list(
        dict.fromkeys(
            m
            for m in (
                primary_metric,
                LAUNCH_SIZE,
                FOLLOW_STARS,
                PLAIN_STARS,
                HN_POINTS,
                PH_VOTES,
                PH_COMMENTS,
            )
            if m is not None
        )
    )

    def observed(cs: Sequence[CaseInput], m: str) -> list[float]:
        out = []
        for c in cs:
            v = ev.value(c, m)
            if v.status == "observed" and v.value is not None:
                out.append(float(v.value))
        return out

    covs = (
        _cov_balance(lvl.winners, non_winners, replace(ctx, view=_view_of(sel)))
        if ctx is not None
        else sel.balance["before_matching"]
    )
    return {
        "label": CONTEXT_LABEL,
        "view": sel.summary["view"],
        "comparison": "final winners vs every shortlisted non-winner of the brief (field and "
        "reference repos, no-anchor and undetermined included), no matching",
        "winners": len(lvl.winners),
        "non_winners": len(non_winners),
        "non_winners_by_role": _count(
            r["role"]
            for r in sel.cases
            if r["panel"] in ("field", "reference") and r["candidate_ref"] not in winners
        ),
        "loser_pool": len(lvl.loser_pool),
        "loser_pool_unmatched": sum(1 for c in lvl.loser_pool if c.ref not in matched),
        "outcomes": {
            m: {
                "winners": _stats(observed(lvl.winners, m)),
                "non_winners": _stats(observed(non_winners, m)),
                "non_winners_with_value": len(observed(non_winners, m)),
                "non_winners_without_value": len(non_winners) - len(observed(non_winners, m)),
            }
            for m in metrics
        },
        "covariates": covs,
        "note": "descriptive; the non-winners are not matched, so differences mix the outcome "
        "with launch timing, audience, age and field",
    }


def _view_of(sel: Selection) -> View:
    key = sel.summary["view"]
    return next((v for v in (*VIEWS, VIEW_PLAIN) if v.key == key), VIEW_PLAIN)


@dataclass
class Selections:
    """Views A and B (headline) and C (context) of one brief version (ADR-083). Each headline
    view has its own result hash; `result_hash` covers the parameters, the inputs, both view
    hashes and the context view."""

    params: dict[str, Any]
    views: dict[str, Selection]
    context: dict[str, Any]
    summary: dict[str, Any]
    inputs_hash: str
    result_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "params": self.params,
            "summary": self.summary,
            "views": {k: v.to_dict() for k, v in self.views.items()},
            "context": self.context,
            "inputs_hash": self.inputs_hash,
            "result_hash": self.result_hash,
        }


def select_views(
    cases_in: Iterable[CaseInput],
    ctx: Context,
    base: Definition,
    *,
    notes: Sequence[str] = (),
) -> Selections:
    """The selection-v6 result (ADR-083, ADR-084): view A (follow-through), view B (launch,
    declared launches: the headline) with its undeclared-launch sub-population (reported
    separately), and the context view C for each. `notes` (input-stage warnings, e.g. the
    launch lookup's counts) go into the overall summary and each view's warnings."""
    cases = sorted(cases_in, key=lambda c: c.ref)
    views = {v.key: select(cases, replace(ctx, view=v), base, notes=notes) for v in VIEWS}
    context = {
        "label": CONTEXT_LABEL,
        "views": {v.key: context_view(views[v.key], replace(ctx, view=v)) for v in VIEWS},
    }
    params = next(iter(views.values())).params
    field_cases = [c for c in cases if c.panel != "exemplar"]
    b_cases = [c.for_view_b() for c in field_cases]
    view_warnings = [
        f"{k}: {w}" for k, s in views.items() for w in s.summary["warnings"] if w not in notes
    ]
    summary: dict[str, Any] = {
        "brief_id": ctx.brief_id,
        "brief_version": ctx.brief_version,
        "selection_version": SELECTION_VERSION,
        "shortlist_n": len(cases),
        "no_anchor": sum(1 for c in field_cases if c.anchor is None),
        "anchors": _count(
            "none" if c.anchor is None else f"{c.anchor.type}:{c.anchor.via or c.anchor.source}"
            for c in field_cases
        ),
        "view_b_anchor_rules": anchor_rule_counts(b_cases),
        "undeclared_launch": sum(
            1 for c in b_cases if c.anchor is not None and c.anchor.undeclared
        ),
        # repos whose view-B anchor is unknown because a launch source was incomplete (ADR-085)
        "view_b_incomplete": _count(
            str(c.anchor_reason).split(":", 1)[1]
            for c in b_cases
            if c.anchor is None and str(c.anchor_reason or "").startswith(INCOMPLETE_PREFIX)
        ),
        "views": {
            k: {
                "label": s.summary["view_label"],
                "headline": s.params["views"].get(k, {}).get("headline", True),
                "result_hash": s.result_hash,
                "population_n": s.summary["reference_population_n"],
                "winners": s.summary["counts"]["winners"],
                "matched_losers": s.summary["counts"]["matched_losers"],
                "headline_pairs": s.balance["headline_pairs"],
                "final_distance": s.summary["final_distance"],
            }
            for k, s in views.items()
        },
        "context_label": CONTEXT_LABEL,
        "warnings": [*notes, *view_warnings],
    }
    sels = Selections(params, views, context, summary, inputs_hash(cases))
    sels.result_hash = sha256_json(
        {
            "params": params,
            "views": {k: s.result_hash for k, s in views.items()},
            "context": context,
            "inputs_hash": sels.inputs_hash,
        }
    )
    return sels
