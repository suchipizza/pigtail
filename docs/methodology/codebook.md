# pigtail codebook

**Version:** 0.3.0 (semver; see §13 and the CHANGELOG at the end) · **Status:** draft for per-brief neighbourhood analysis (PRD v2.0, ADR-047). Nothing here has been checked against coded data yet. No version (0.1.0, 0.2.0 or 0.3.0) has been used for coding, unitizing or any derived event.
**Task:** M4-T1 (v0.1.0); M4-T1d and M4-T2c (v0.2.0); M11 (v0.3.0, re-scope) · **Requirements:** PRD F5 (R5.1–R5.5), F6 (R6.1–R6.3), F7 (R7.1–R7.5), F20 (R20.1–R20.3), R4.9, §5.1–5.2, §9.2; compliance control CB-11 (docs/compliance/dpia.md §9).
**ADRs this depends on:** ADR-010 (source clearances), ADR-014 (tags and status, nothing imputed), ADR-015 (time anchor T), ADR-017 (provisional categories), ADR-022 (interim privacy holds), ADR-027 (√μ baseline floor), ADR-032 (star series from the star-history endpoint; supersedes ADR-012), ADR-035 (`raw` star-history series), ADR-047 (brief-driven neighbourhood analysis; items 3, 7, 8), ADR-049 (items 1, 6, 7, 8, 12).
**Machine-readable enums:** `schemas/codebook/v0.3.0.json` (`codebook v0.3.0`). `schemas/codebook/v0.1.0.json` and `v0.2.0.json` are superseded and kept unchanged as records. If this document and the JSON disagree, that is a bug. Until it is fixed, the JSON wins for validators and this document wins for meaning. The v0.2.0 text of this document is in git history (`git show archive/global-collection:docs/methodology/codebook.md`).
**Author role:** `analyst`, working with researcher discipline. `LR [n]` means reference n in `docs/research/literature.md` §8. `SM` means `docs/research/source-matrix.md`. `OM` means `docs/specs/outcome-model.md` (v2).
**Data version:** none. The codebook comes before any data. No case has been coded and no outcome has been looked at.
**Code commit:** `e4988ec`, read on 2026-09-25 (v0.1.0); `535ddd0` for v0.2.0; `0330bd4` for v0.3.0. The burst derivation matches `outcome-model.md` §2.1 and the day baseline in `src/pigtail/capture/detection_v1.py` (`daily_baseline`) as of `535ddd0`; M11 deletes global breakout detection, and the per-repo burst code that survives (ADR-047.6, PRD R19.5) must keep this derivation.

**Scope of this version.** Codebook 0.3.0 serves one brief at a time. A brief's deep forensics (its 15–25 winners and 15–25 matched losers, PRD R4.8) are coded with it, double-coded with adjudication (R7.2), and its agreement is reported per field in that brief's report (R7.5). There is no global case store to code, no gate G1, no global mechanism library and no promotion rule (ADR-047.3, ADR-047.7, WORK_ORDER v2.0 §4.3). Patterns are per brief (§7, PRD F20).

---

## 0. How to read this codebook

1. **Definitional choices are not findings.** Every threshold, window and band in this codebook (burst gaps, trigger windows, reach bands, trigger-confidence criteria, the pattern-reporting minimums of §7.3) is a v0 design choice. None of them is an empirical result. A change goes through a codebook version bump with a changelog entry (§13). A brief pins the codebook version before its outcome sort; a change made for a brief after its outcome sort is labelled **exploratory** in that brief's report (WORK_ORDER §6, analyst rule).
2. **Two kinds of fields.** *Derived* fields are computed by code from evidence: for example source, timestamps, the burst and quiet segmentation, and reach bands. They are tested, not double-coded. *Coded* fields are assigned by an LLM or a human coder. Only coded fields enter Krippendorff's α (§10).
3. **Every coded value follows the rules in §11.** It must carry evidence_ids and verbatim quoted spans, or it is dropped. `unknown` is a legitimate answer. Absence of evidence is never coded as `false` or `absent`, unless the rule for that field says so explicitly.
4. **Privacy rules (§12) bind every section.** Where a section's definition would conflict with §12, §12 wins.

---

## 1. Units of analysis

| Unit | What it is | Id | Who creates it |
|---|---|---|---|
| `evidence` | One captured document with a content hash (PRD §7, `schemas/v0/evidence.schema.json`) | `ev_…` | Connector (derived) |
| `item` | One post, comment, page, release, series or record inside an evidence snapshot. Most evidence records hold exactly one item. A thread snapshot holds many. | `evidence_id` + locator | Parser (derived) |
| `case` | One growth episode of one repo (`schemas/v0/case.schema.json`) | `case_…` | Capture (derived) |
| `event` | A timeline event (§3) | `evt_…` | Burst and quiet: derived. Prep, launch, relaunch and pivot: coded |
| `node`, `edge` | Spread-graph elements (§4) | `node_…`, `edge_…` | Candidate pairs: derived. Type and level: coded |
| `asset` | One instance of a spreadable asset (§5) | `ast_…` | Instances: parser, where possible. Category: coded |
| `trigger_candidate` | A candidate trigger for one burst (§6) | `trg_…` | Candidates: derived by the window rule. Type and confidence: coded |
| `pattern_support` | (case, pattern) link within one brief (§7.4). Replaces `mechanism_support` (0.2.0). | `ps_…` | Presence: coded. Role-based reading (supporting case or counterexample): derived from the case's role in the brief |

**Unitizing rule.** Wherever it is possible, units are cut out *deterministically* by code before any coding happens: items, burst segments, candidate edge pairs, trigger candidates, and asset instances that a parser can extract (images, GIFs, links, headings, titles). Both coders then code the same units, so α is computed on identical units. Units that only an LLM can cut out (phrases, benchmark claims, stated reasons) are not core fields (§10.3).

---

## 2. Evidence types and reliability scale (R5.1)

### 2.1 Evidence type (`evidence_type`, derived from the connector plus the parser; a coder may correct it)

| Value | Definition | Typical sources (clearance per ADR-010) |
|---|---|---|
| `platform_metric` | A machine-generated count or series about a project, published by the platform that hosts it | GitHub star-history daily counts (TM-33 under TM-02; ADR-032.3), hourly star-count snapshots (GraphQL `stargazerCount`, TM-02) where they exist (the pre-re-scope watch-list cache, ADR-032.1; the watch list itself is deleted, ADR-047.6), registry downloads (TM-07 to TM-11), deps.dev dependents (TM-12), HN points and comment counts (TM-03/04), HN rank polls (TM-04) |
| `platform_event` | A machine-generated event record | GitHub release, tag or commit record (TM-02); GH Archive event (TM-01), used for discovery signals only and never for stars, forks, issues or PRs (ADR-047.8) |
| `project_artifact` | Content the project published on its own channels: the repo, its site, its docs, and accounts or blogs linked *from* the repo or site | README at a commit, release notes, changelog, docs page, project blog post, pricing page (TM-02, TM-29) |
| `community_post` | A post or comment on a community platform | HN story or comment (TM-03/04), Bluesky post (TM-06, held by ADR-022), V2EX topic or reply (TM-19, off by default) |
| `editorial` | Content from a publication with its own name (masthead): a newsletter issue, a third-party blog, a press article, a curated list | Direct fetch under TM-29, press entered by the operator (TM-31) |
| `archive_capture` | A Wayback capture of any of the types above. The coder also records the type of the captured content in `captured_type`. | Wayback CDX (TM-13, off by default until H2 Q5) |
| `operator_entry` | A manual entry that cites a URL plus a date plus a short quote (TM-31) | Operator |

`capture_mode` (derived): `api_json | rendered_html | screenshot | archive | operator_entry`.

### 2.2 Claim basis (`claim_basis`, coded per coded item)
An evidence record can be a faithful capture of what someone *said* about something else. The claim basis records how the coded fact relates to the snapshot:
- `observed`: the snapshot is the fact itself. Examples: a star timestamp; an HN story's `created_at`; the README text at a commit, used for "the README said X".
- `first_party_statement`: the project states something about its own actions or results. Example: "we launched on HN yesterday", or an MRR figure.
- `reported`: a third party states something about another fact. Example: "this was all over X last week".

### 2.3 Reliability scale (`reliability`, ordinal; the same enum as `evidence.reliability`)
Reliability says **how verifiably this evidence shows the fact it is cited for**. There are three ordered levels. `unknown` is not a level: it counts as missing in ordinal α (§10.2).

| Level | Ordinal | Operational anchor (every condition must hold) | Examples |
|---|---|---|---|
| `high` | 3 | (a) `claim_basis = observed`; (b) captured as raw machine-readable data from the platform that hosts the fact (`capture_mode = api_json`), or as a project artifact fetched at a pinned commit or version; (c) timestamp precision is hour or better where time matters | A star-history API response cited for the star count on the endpoint days it covers; an hourly GraphQL star-count snapshot cited for the count at its capture hour; HN item JSON with `created_at`; a GitHub release record; the README fetched at commit `c` and cited for "the README said X at c" |
| `medium` | 2 | Any one of: (a) `observed`, but captured as `rendered_html`, `screenshot` or `archive`; (b) data from a secondary aggregator whose method is documented (deps.dev dependents); (c) timestamp precision is only day-level where time matters; (d) `first_party_statement` about the project's own *actions*, such as "we posted X on date D" | A project blog post captured as HTML with a visible date; a Wayback capture of a pricing page; a docs page stating the release date; a star-history day count cited for *when within that day* stars arrived (day precision, condition c) |
| `low` | 1 | Any one of: (a) `claim_basis = reported`; (b) `first_party_statement` about *results* (users, revenue, growth) that no source verifies; (c) no date, or a date that can't be checked; (d) the snapshot quotes or paraphrases a source that was not itself captured | "Went viral on X" in a comment; a self-reported MRR figure (ADR-021); an undated landing page |
| `unknown` | — | Not yet assessed, or the assessment isn't possible | — |

Decision rules:
1. **Rate the weakest link.** If conditions from two levels apply, take the lower level.
2. **Corroboration can raise a `reported` claim to at most `medium`.** This requires that a second, independent evidence record at `medium` or above shows the same fact. Both evidence ids are cited. The raise applies only to the coded item, not to the evidence record.
3. **Manipulation flags don't change reliability. They are recorded next to it** (`manipulation_flag`, §2.4). A faithfully captured vote-ring post is still high-reliability evidence *that the post exists*.
4. **Relation to the schema.** `schemas/v0/evidence.schema.json` currently describes the levels by capture mode only. This codebook's anchors are the operative definition. The record-level `evidence.reliability` stores the level for `claim_basis = observed`. Each coded item stores its own effective reliability after applying rules 1 and 2. (A schema-description update is listed in the open issues.)

### 2.4 Manipulation flags (`manipulation_flag`, multi-valued)
- `fake_star_campaign_suspected`: the repo has a StarScout campaign flag, i.e. `campaign_flag = true` (LR [20]; OM §4). A flag that is `unknown` (no identity-level data, ADR-032.3) does not set it. This is a *suspicion*, never proof, and it is never named in a public output (LR §2.1).
- `vote_solicitation`: the snapshot shows a request to upvote or comment. Show HN guidelines: "Please don't ask friends to upvote or comment" (LR [65]).
- `undisclosed_paid_promotion_suspected`: a reported claim of paid promotion that the item does not disclose. This value is always `low` reliability.
- `disclosed_paid_promotion`: an ad or sponsorship the item itself discloses. This is not a violation, but it confounds effect estimates (LR [64]).
- `none_observed`.

---

## 3. Event taxonomy (R5.2)

### 3.1 Two layers
Events sit in two layers that may overlap in time:
- **Attention layer: `burst | quiet`.** Derived by code from the `raw` star-history series (daily net counts from GitHub's star-history endpoint, on endpoint days; ADR-032.3, ADR-035, OM §1.2), the same series the brief's attention dimension uses (OM §1.2, §5). Hourly count snapshots, where they exist, refine burst onsets only (§3.2). The two values split the case window with no gaps, except days without series coverage (§3.3). Not double-coded.
- **Action layer: `prep | launch | relaunch | pivot`.** Coded from evidence. These are things the project did.

The case window is [T − 90 d, T + 365 d] (T per ADR-015). Anything outside the window is recorded only if a rule below needs it (for example a prior launch for `relaunch`).

Every event records: `id`, `type`, `start`, `end` (null for point events), `time_precision` (`hour | day | unknown`), `evidence_ids`, quoted spans (§11), coder provenance, and `codebook_version`.

### 3.2 `burst` (derived)
- **Definition.** A period in which star velocity is abnormally high against the repo's own baseline.
- **Series (from v0.2.0; unchanged in v0.3.0).** `raw` star-history daily net counts `n(d)` (ADR-032.3), fetched **per repo** for every case (ADR-047.8, PRD R19.5). Filtered series are not used to cut units: they are `unknown` for most windows (ADR-032.3, ADR-035). Hourly star-count snapshots of the repo, where any exist (the pre-re-scope watch-list cache, ADR-032.1), are used **only** to refine the onset hour (below), never for detection, end, merging or `quiet`, so that every case is segmented on one series at one resolution (OM §1.2). Launch-mode runs (every 3 h on launch day, PRD R19.4) don't give hourly coverage and don't refine onsets.
- **Days.** `d` is an endpoint day in `star_history_day_tz` (OM §1.2). The case window's days are the 90 endpoint days before the window's first day plus the 365 endpoint days starting at it, where the first day is the one OM §1.2 maps T to (hour-precision T: the day containing T; day-precision T: the day with T's UTC date). A day is used only after it has ended (OM §1.2). Each burst and quiet event records `day_boundary = {tz, tz_status, first_day, last_day}`.
- **Detection.** `velocity-v0` on endpoint days, as OM §2.1 applies it where only daily data exists: day `d` fires when `n(d−1) + n(d) ≥ 100` and `z ≥ 3`, with the baseline taken from the 30 endpoint days before `d−1`, converted to 48-hour sums, and σ floored at √(baseline mean) (ADR-027 item 3; thresholds JSON `burst_detection`; `daily_baseline` in `src/pigtail/capture/detection_v1.py`). A burst starts at the first firing day that is not already inside a burst. Detection runs over the whole case window, not only for the first burst.
- **Onset (OM §2.1, exactly).** The 48-hour detection window is endpoint days `d−1` and `d`.
  - Where hourly count snapshots cover that window: the onset hour is the earliest hour in the window whose net star gain exceeds `μ_h + 3·√μ_h`, where `μ_h` is the baseline mean stars per hour (`μ_h + 1` when `μ_h = 0`). The baseline is daily, so **`μ_h = μ_d / 24`**, with `μ_d` the mean stars per endpoint day over the 30-day detection baseline (orchestrator decision, 2026-09-25; OM §2.1 leaves the conversion implicit, and a follow-up asks OM to state it). DST-switch days of 23 or 25 hours are not corrected (OM §1.2). If no single hour qualifies (a diffuse rise), the onset is the start of the window. Precision `hour`.
  - Otherwise: the same rule on endpoint days with `μ_d` (baseline mean stars per endpoint day, over the same 30 days). The onset is the start of the first qualifying day among `d−1`, `d`; if neither qualifies, the start of `d−1`. Precision `day`.
  - **Agreement with T.** The burst whose detection window contains the case's recorded `T_burst` onset (OM §2.1) takes that recorded onset and its precision, so C9/C10 units and the case anchor never disagree.
  - GH Archive is never used for onsets (ADR-047.8). Onsets computed from it before the re-scope are not used.
- **End (v0 choice).** The start of the first run of 3 consecutive endpoint days after the onset day on each of which `n(d) ≤ μ_d + 3·√μ_d`. `μ_d` is the mean stars per endpoint day over the burst's detection baseline; when `μ_d = 0`, use `μ_d + 1`. The end has precision `day`.
- **Merging (v0 choice).** Two bursts separated by fewer than 7 endpoint days are one burst with `multi_peak = true`.
- **Required evidence.** The star-history snapshot(s) covering the segment and its baseline, with a JSON pointer to each day's count (§11.2). Where the onset was refined, the hourly snapshot records used, with pointers to their counts. Also recorded: `series_variant = raw`, the star-history `metric_version`, `onset_precision`, `day_boundary`, and the series completeness check result (below).
- **Series completeness check.** The star-history series of a case is **incomplete** when the connector reports an error or a missing page for any week the case window needs, or when the all-time sum of the returned days differs from the repo's `stargazers_count` fetched in the same run by more than 1 % (a v0 design choice that allows for stars added between the two requests; it was pilot amendment 1, A2, restated here because that amendment is withdrawn). Days of an incomplete week are coverage gaps (§3.3). The v0.1.0 attribute `gharchive_coverage_ratio` is dropped: it measured GH Archive against a series that no longer exists, and neither is the burst series now.
- **Descriptive attribute `burst_shape`:** `sudden_fast_decay | gradual_build | mixed | unknown`. It follows the exogenous and endogenous relaxation classes of Crane & Sornette (LR [35]). The v0 rule: `sudden_fast_decay` when the peak day falls within 48 h of onset and velocity is below 50% of peak within 7 days; `gradual_build` when the peak is more than 7 days after onset; `mixed` otherwise. On endpoint days this reads: peak day index 0 or 1 counted from the onset day; some day with index ≤ 7 below 50% of the peak count; `gradual_build` when the peak day index is > 7. This attribute is descriptive only. LR §3.4 warns that fits will be noisy on sparse star series, and H-L3 tests this rather than assuming it.
- **Boundary cases.**
  - A burst caused by a fake-star campaign is still a `burst`. The case carries `manipulation_flag` when `campaign_flag = true` (§2.4). Where a filtered variant is eligible for the whole case window (OM §1.2, §4), the segmentation may be recomputed on it and the difference reported as exploratory; it never defines units.
  - A download spike without a star spike is not a `burst`. It is recorded as an exploratory observation.
  - Star-history counts only **current** stargazers (net, survivor-biased, OM §1.2). A burst whose stars were later un-starred or deleted can shrink below the detection thresholds and then isn't a `burst`. This is a property of the series, recorded as a limitation, not corrected.

### 3.3 `quiet` (derived)
- **Definition.** Every maximal interval of at least 7 endpoint days inside the case window that is not part of a burst.
- **Attribute:** `phase = pre_first_burst | inter_burst | post_last_burst`.
- **Required evidence.** The same series as `burst`.
- **Boundary cases.**
  - An interval shorter than 7 days between bursts has already been merged into the burst (§3.2).
  - Days without series coverage are `unknown`, not `quiet`, and split the quiet interval. Examples: a star-history week the connector couldn't retrieve (§3.2 completeness check), or a day that hasn't ended yet. Days before the repo's creation have no series and are neither `burst` nor `quiet`.

### 3.4 `launch` (coded, point event)
- **Definition.** The **first** public announcement that is (a) first-party and (b) deliberate, and that presents the project to an audience with the aim of getting attention or users.
- **First-party** means one of:
  - (i) it is published on the project's own channels (§2.1 `project_artifact`); or
  - (ii) the item's own text identifies the author as a maker, for example "Show HN: I built…", "we're launching…", "our new project".
  - First-party status is never established by matching accounts across platforms (§12 rule P3).
- **Required evidence.** At least one item with `first_party = yes`, a timestamp, and a quoted span containing the announcement language. Examples: "Show HN", "Launch HN", "introducing", "today we're releasing", "we built", or a v1.0 announcement.
- **Decision rules.**
  1. **Campaigns.** Announcements from the same project within 14 days of the first one form **one** `launch` with `campaign = true`. Each announcement is listed as a sub-item. Example: a launch week, one announcement per day (LR [63]).
  2. **Must be public.** Private betas, invite-only waitlists and posts behind a login are `prep`.
  3. **Only one launch per case.** Later announcements are either part of the campaign (rule 1) or a `relaunch` (§3.6).
  4. **Relation to T.** T comes from ADR-015 and is not re-derived here. If a coded launch disagrees with the anchor used for scoring, the item goes to the review queue.
- **Boundary cases.**
  - *A third party posts the repo first* (for example someone else's Show HN or "found this"): this is **not** a launch. It is a trigger candidate (§6). If the maker never announces, the case has no `launch`, and the derived `launch_mode` is `third_party_discovered`.
  - *A GitHub release or tag without announcement language* is not a launch. It is `prep` if it comes before the launch; otherwise it is only a trigger candidate of type `release`.
  - *A maker replies in a thread someone else started, saying "author here":* not a launch. It is a `replied` edge (§4) from the project.
  - *A repo made public on the same day it is posted:* the launch is the post. Making the repo public is `prep`, with day precision.
- **Derived case attribute `launch_mode`:** `self_launched | third_party_discovered | none_observed`.

### 3.5 `prep` (coded, interval)
- **Definition.** First-party actions before the launch that produce launch assets, an audience, or launch readiness. Examples: a README overhaul, a demo or GIF, a docs site, a landing page, a waitlist, a "launching soon" post, a first tagged release, a private beta, making the repo public.
- **Window.** From 90 days before the launch until the launch. If there is no launch, the window ends at the first burst onset and the event gets `prep_without_launch = true`.
- **Required evidence.** At least one dated `project_artifact` or `platform_event` showing the action, and a quoted span or locator for it. Examples: a commit touching README.md, a release record, a Wayback capture of a landing page.
- **Granularity.** One `prep` event per action kind (the `prep_kind` enum in the JSON), with the earliest and latest dated evidence as start and end.
- **Boundary cases.**
  - Ordinary development commits are not `prep`. The action must produce something meant to be seen by an audience: a README, docs, demo, site, release or announcement.
  - A "coming soon" post that itself draws a burst is still `prep`. The burst gets it as a trigger candidate.

### 3.6 `relaunch` (coded, point event)
- **Definition.** A later first-party, deliberate public announcement that presents the project as new or substantially changed. It must come at least 30 days after the end of the previous launch or relaunch campaign.
- **Required evidence.** Everything a launch requires (§3.4), plus a quoted span with a novelty claim ("2.0", "rewritten", "now supports", "renamed to", "reintroducing"), plus a reference to the earlier launch (event id, or the evidence of an earlier launch outside the window).
- **Decision rules.**
  1. A second announcement fewer than 30 days after the previous campaign belongs to that campaign.
  2. An announcement that comes with a pivot is coded as **both** a `pivot` and a `relaunch`.
  3. If the relaunch opened its own case (OM §2.2 item 6), both cases cross-reference each other's event ids.
- **Boundary cases.**
  - A routine release announcement ("v1.4 is out") without a novelty claim aimed at new users is not a relaunch.
  - A project posted again by a *third party* is not a relaunch. It is a trigger candidate.

### 3.7 `pivot` (coded, point event)
- **Definition.** A first-party change to at least one of the following:
  - the project's primary category (§8);
  - its stated target user;
  - its stated core use case;
  - its business model or licence (for example a move from an OSI-approved licence to a source-available one, or adding a hosted paid offering as the primary model).
- **Required evidence.** Either (a) two dated first-party artifacts, one before and one after, with quoted spans showing the change; or (b) one artifact explicitly stating the change ("X is now Y", "we are refocusing on…").
- **Attribute `pivot_dimension`** (multi-valued): `category | target_user | use_case | business_model | license`.
- **Boundary cases.**
  - A rename without a change of purpose is not a pivot. Record it as `rename = true` on the case.
  - A new feature that adds a use case while the stated core stays the same is not a pivot.
  - For a licence change, the old and new SPDX identifiers are cited from the LICENSE file at two commits.

### 3.8 Precedence and overlaps
- Layers overlap freely. A launch inside a burst is expected.
- Within the action layer, a single item can support at most one of `launch` or `relaunch`. It can also support a `pivot` (rule 3.6.2).
- If an item fits none of the definitions, it is coded `none`. Its `event_type_supported` value is `none` (core field C3, §10).

---

## 4. Spread graph (R5.3)

### 4.1 Status in v0.1.0 to v0.3.0: community and publication level only
- ADR-022 holds all account-level spread graphs until LQ-8 is answered. LQ-8's default is "community- and publication-level graphs only".
- So in v0.1.0 to v0.3.0 the `account` node type is **defined but disabled**. The JSON has `"account_nodes_enabled": false`.
- An item written by an account is attached to the **venue** it appeared in:
  - the community node (for example "Hacker News" or a V2EX node); or
  - for platforms without communities, a platform-level community node (for example "Bluesky, platform-wide").
- Lifting the hold requires an ADR, the LQ-8 answer, and CB-10 plus CB-14 in place.

### 4.2 Node types
| Type | Definition | Identity |
|---|---|---|
| `project` | The case repo, plus official project channels linked from the repo or the project's site. Exactly one per case. | `repo_id`. For repos owned by personal accounts, CB-20 applies to outputs. |
| `community` | A venue where many people post: a platform, subforum or section. Examples: HN, a V2EX node, Bluesky platform-wide, a Discord server (held). | Platform plus venue id. Not personal data. |
| `publication` | An outlet with a name distinct from any natural person (a masthead): a newsletter, magazine, news site, company blog or curated list | Publication name. Organisations may be named in the private UI (LQ-7 default). |
| `account` (**disabled**) | A single platform account, person or organisation | Keyed-hash pseudonym only (PRD §10). Never merged across platforms (§12 P3). |

Rule: a newsletter or blog written under one person's own name, with no separate masthead, counts as an `account`. It is therefore held under ADR-022, not treated as a `publication`.

### 4.3 Edge types
Edges point in the direction information flowed: from the node whose item came first to the node that acted on it. Every edge is anchored on the target item's evidence, plus the source item's evidence where one exists.

| Type | Definition | Decision rule |
|---|---|---|
| `published` | The target node carried an **original** item about the project, one not derived from another item we observed. Edge `project → node`. | First appearances of the repo in a venue. A first-party post creates `project → venue` with `first_party = yes`. |
| `redistributed` | The target re-shared the source item and added nothing substantive. Examples: a repost or boost, a cross-post, submitting a blog post's link to HN, a newsletter link with only the title. | Choose this if the target's added text is empty, or consists only of the title, link, hashtags or generic boilerplate ("check this out", "cool"). |
| `cited` | The target's item references the source item **and adds its own content**. Examples: a quote-post with commentary, "via X" with a paragraph, an article that links the source. | Choose this if the target adds at least one sentence of its own beyond the boilerplate list above. |
| `replied` | The target posted a reply in the source item's thread | Coded only if (a) the replier is the `project`, or (b) the reply contains a link to the repo or its site (a new mention). All other replies are counted on the item as `reply_count` and get no edge (minimisation). |

### 4.4 Edge evidence levels (`edge_evidence_level`, ordinal)
| Level | Ordinal | Anchor |
|---|---|---|
| `explicit` | 3 | A structural link recorded by the platform, visible in the snapshot. Examples: a repost or quote record, a reply `parent` id, an HN `parent` field, a hyperlink to the source item's URL. |
| `attributed` | 2 | A textual attribution in the snapshot that names the source ("via [publication]", "saw this on HN"), without a machine link |
| `inferred` | 1 | No attribution. The link is inferred only from timing and content similarity (same URL, same title phrase, same asset hash) |
| `none` | 0 | No basis. Never stored as an edge. It is the "no edge" answer for candidate pairs. |

**Threshold for `supported` (R5.3).** An edge is `supported` if and only if all of these hold:
1. its level is `attributed` or `explicit`;
2. both endpoint items have evidence records with valid hashes, and the quoted spans or locators check out (§11);
3. the source item's timestamp comes before the target item's, taking timestamp precision into account;
4. no §12 hold applies to either endpoint.

Otherwise the edge is `unsupported`. It is stored for review and excluded from structural virality (LR [29]), from breadth counts (H-L4, LR [31]) and from pattern evidence (§7).

### 4.5 Reach bands (CB-10)
- Reach is stored **only as a band, never as an exact count**, for every node that has followers or subscribers.
- The band is computed at ingest, and the exact count is discarded.
- Bands (v0 choice, log10):
  - `r0`: unknown
  - `r1`: under 1,000
  - `r2`: 1,000 to 9,999
  - `r3`: 10,000 to 99,999
  - `r4`: 100,000 or more
- Community nodes carry item-level engagement (HN points and comments) as project-level metrics (OM §1), not as reach bands.
- A reach band is never used to rank or score an account (§12 P2).

### 4.6 Public-figure exception (conservative; **inactive for natural persons in v0.1.0 to v0.3.0**)
- LQ-7 is open. Its default is: "Only organisations and publications are named, and only in the private UI. No natural person is named in any output."
- v0.1.0 (and v0.2.0, v0.3.0) therefore defines the criteria below but does not apply them to natural persons. Applying them needs the LQ-7 answer plus an ADR.
- **Proposed criteria for a natural person.** All of the following must hold:
  1. The person speaks in a **professional public-communication role** about software. Examples: a journalist or editor for a publication with a masthead, or an official spokesperson or DevRel speaking *for an organisation*.
  2. The coded activity is part of that public role (FADP Art. 31(2)(f): "public activities").
  3. The role is documented in a snapshot from a cleared source. The person's own professional page, or the publication's masthead, counts.
  4. The person is not a maintainer of a matched loser in any brief, and does not own a repo on a personal account among any brief's cases (LQ-7 context).
  5. The operator has approved the person onto the allowlist (CB-14). The approval is recorded with the evidence_id.
- **Explicitly not sufficient:** a high reach band, a verification badge, or being "well known".
- **Even when eligible:** the person is named only in the private UI, and never in public outputs (CB-14, CB-20).

---

## 5. Asset taxonomy (R5.4)

Each asset instance records:
- `category`;
- `origin`: `first_party | third_party | unknown`;
- `first_seen` (evidence_id and timestamp);
- `appearances`: evidence_ids;
- `spread_status`:
  - `reused_by_third_party`: the asset appears in at least one item whose node is not the `project` node;
  - `first_party_only`;
  - `unknown`.

The asset gallery (R5.4) shows `reused_by_third_party` assets first. Images and GIFs are referenced by blob hash inside the snapshot and are never re-hosted publicly.

| Category | Definition | Unitized by |
|---|---|---|
| `image_screenshot` | A static image of the product's UI, output or terminal | Parser (img tags, attachments) |
| `image_diagram` | An architecture diagram, infographic or explanatory chart (not a star or benchmark chart) | Parser |
| `gif_animation` | An animated GIF, APNG or looping short clip embedded in a README or post | Parser |
| `video_demo` | A linked or embedded video longer than a loop. The link is recorded. Content on YouTube is not captured (a gap, TM-14). | Parser |
| `terminal_recording` | An asciinema-style recording, or a terminal-session cast | Parser |
| `live_demo` | A hosted try-it URL: a playground, sandbox, notebook or hosted instance | Parser (links) + coder |
| `benchmark_claim` | A quantitative performance or cost comparison claim ("3× faster than X"). Sub-fields: `metric`, `compared_to`, `claimed_value`, `method_linked` (yes, no or unknown) | Coder (non-core) |
| `benchmark_chart` | An image or table showing benchmark results | Parser + coder |
| `comparison_table` | A feature matrix against alternatives | Parser (tables) + coder |
| `title` | The headline of a launch or mention item (HN title, post headline) | Parser |
| `tagline` | The one-line pitch: the repo description, or the first sentence of the README hero | Parser |
| `phrase` | A distinctive phrase of 3 or more tokens (not a stop-phrase) that appears in 2 or more items from 2 or more distinct nodes | Coder (non-core) |
| `readme_section` | A README section. Sub-type `readme_kind`: `hero_pitch`, `quickstart`, `feature_list`, `why_alternatives`, `faq`, `badges`, `social_proof`, `call_to_action`, `roadmap`, `other` | Parser (headings) + coder |
| `install_one_liner` | A single-command install (`curl … \| sh`, `pip install x`, `brew install x`, `npx x`) | Parser + coder |
| `code_snippet` | A short usage example | Parser |
| `link` | A URL that spreads. Sub-type `link_kind`: `repo`, `docs`, `landing_page`, `launch_post`, `demo`, `benchmark_source`, `other` | Parser |
| `launch_post` | A long-form first-party announcement: a blog post or thread | Coder |
| `logo_brand` | A logo, mascot or visual identity element | Parser + coder |
| `social_proof` | A star-history chart, "used by" logos, testimonials, download badges | Parser + coder |
| `pricing_offer` | A pricing page, a paid-plan call-to-action, a hosted sign-up (B2B module) | Coder |
| `other` | Anything else. A free-text note is required. | Coder |

Boundary rules:
- A GIF that shows a terminal is `gif_animation`. `terminal_recording` is reserved for cast formats.
- A benchmark stated in the text of a title is coded twice, once as `title` and once as `benchmark_claim`, because it is two assets.
- An image containing people's faces is recorded by hash only. Its content is not described (§12 P1).

### 5.1 Case-level positioning field: `novelty_claim` (added in 0.2.0; not core)
MC-09 (novelty positioning) needs a novelty claim on every case. In v0.1.0 the field existed only as an `ai_hype` extra field (§8), so presence outside that module was always `unknown`. From v0.2.0 it is a **case-level field** coded on every case in the coded sample. For `ai_hype` cases, the module's `novelty_claim` is this same value; it is not coded twice.

- **What it codes.** Whether the project, **in its own words at T**, claims to be novel: new in kind, a new approach, or a new combination. It codes the *claim*, not whether the project is in fact novel. Fang et al. link novelty to stars and to lower long-run participation (LR [13], H-L6); whether their novelty measure and a quoted positioning claim are the same construct is **unknown** (candidates.md MC-09).
- **Inputs (frozen at T, like the category inputs of §9, so no post-T information leaks in):**
  1. the repo description at T;
  2. the README at the last commit before T;
  3. if the anchor type is `launch`, the evidence record that fixed `T_launch` (OM §2.1).
- **Values** (nominal):
  - `present`: at least one input contains a first-party span (≤ 300 characters, §11.2) with an **explicit novelty marker**, for example "the first", "the only", "a new kind of", "novel", "a new approach to", "a new way to", "reimagines", "never been possible before", "for the first time". Hedged claims ("one of the first") count.
  - `absent`: **every** input listed above was captured and none contains a qualifying span. This is a field rule that allows `absent` from the captured inputs (§0 item 3, §11.3).
  - `unknown`: otherwise, with a reason (§11.3), for example `no_evidence` when the README at T couldn't be retrieved.
- **Attribute `novelty_kind`** (multi-valued; only when `present`, otherwise `not_applicable`):
  - `new_in_kind`: claims that nothing like it existed, or that it is the first or only one, including within a stated scope ("the first open-source X", "the only X for Y");
  - `new_approach`: claims a new method for an existing task ("a new approach to", "rethinks X from scratch");
  - `new_combination`: claims novelty from joining existing things, with a novelty marker ("the first X that also does Y", "brings X to Y for the first time"); a span can carry both this and `new_in_kind`;
  - `other`: a novelty claim that fits none of these (free-text note required).
- **Not a novelty claim** (code `absent` if nothing else qualifies):
  - release or version newness ("new in v2", "now supports", "rewritten"): that is relaunch language (§3.6);
  - alternative positioning ("an open-source alternative to X", "replaces X"): recorded by `alternative_to_positioning` (§8) and `stated_reason = alternative_to_incumbent` (§7.1);
  - performance or cost superlatives without a novelty marker ("the fastest", "3× faster"): `benchmark_claim` (§5);
  - generic marketing adjectives ("modern", "next-generation", "powerful", "simple", "blazing fast");
  - anything a third party says about the project (first-party only; `claim_basis = first_party_statement`, §2.2).
- **Reliability** follows §2.3. A README fetched at a pinned commit and cited for "the README said X at c" is `high`.
- **Status.** Not a core field (§10.3): a brief codes it when one of its pattern hypotheses needs it (for example MC-09). When it is coded it is double-coded, its α is reported per field like every coded field, and a pattern resting on it carries its reliability label (§10.4).

---

## 6. Trigger attribution for bursts (R5.5)

### 6.1 Candidates (derived)
For each burst with onset `t0` (§3.2; hour or day precision):
- **Proximal window (v0 choice):** items timestamped in `[t0 − 48 h, t0 + 6 h]`.
  - The +6 h tolerance absorbs day-precision timestamps and hourly binning.
  - The 48 h look-back matches the short windows used in prior HN-launch studies: 3 days in LR [8]; 24 h, 48 h and 7 d in LR [40].
- **Day-precision onsets (from v0.2.0).** When the onset has precision `day`, `t0` is the start of the onset endpoint day (OM §2.1), and the burst may have started at any hour of that day. The proximal window is then `[t0 − 48 h, end of the onset day + 6 h]`, so items later on the onset day stay candidates. The distal window is unchanged. `time_offset_h` is still measured from `t0`, and every candidate records the onset precision. v0.1.0 assumed hour-precision onsets throughout, because the stargazers API gave per-star timestamps; star-history gives days, so retrospective cases mostly have day-precision onsets.
- **Distal window:** `[t0 − 7 d, t0 − 48 h)`. Only for publications with a known lag (newsletters, weekly digests) or for items that a proximal item explicitly cites. The rationale for the lag must be quoted.
- **Candidate items:** every captured item in these windows that mentions the repo (URL, name, or owner/name), plus the project's own `release` records.

### 6.2 Trigger types (`trigger_type`, coded, nominal)
| Value | Definition |
|---|---|
| `hn_front_page` | An HN story about the repo that reached the front page. Shown by an own rank poll (rank ≤ 30, a config assumption and **unverified**, OM O7) or by the Algolia `front_page` tag (semantics undocumented, OM §1) |
| `hn_post` | An HN story with no front-page evidence (`unknown` when polling coverage is missing, never "not on the front page"). The rank poller runs only during scheduled runs and while a brief or tracked project is in launch mode (ADR-049.1), so rank history outside those windows is a coverage gap; for retrospective cases the Algolia `front_page` tag is the only front-page evidence, labelled as such. |
| `community_post` | A post in another cleared community (V2EX; others are gaps) |
| `social_post` | A post on a social platform (Bluesky; X is a gap) |
| `publication_feature` | A newsletter, press article, third-party blog or podcast page |
| `aggregator_listing` | Inclusion in a curated or ranked listing: an awesome-list, a trending page (if a cleared capture exists), a directory |
| `upstream_mention` | A mention by a related project's own channels: docs, README or release notes of a framework or dependency |
| `release` | A project release or tag (a first-party platform event) |
| `first_party_announcement` | The project's own launch, relaunch or announcement post, on its own channel. On a community venue, use the venue type and set `first_party = yes`. |
| `paid_promotion` | A disclosed paid placement (LR [64]) |
| `manipulation_suspected` | The burst coincides with a fake-star campaign flag or a vote-solicitation flag (§2.4) |
| `none_observed` | No candidate in either window. **This does not mean "organic" or "endogenous".** Unobservable channels (§6.4) may hold the trigger. |
| `unknown` | Candidates exist, but no type can be assigned from the evidence |

### 6.3 Ranking and confidence
Each candidate records:
- `time_offset_h`: the item's time minus `t0`;
- `time_precision`;
- `links_repo`: whether the item contains the repo or site URL (yes or no);
- `reach_band` (§4.5) or the engagement band (HN points, comments);
- `first_party`;
- `edge_evidence_level` back to the project.

**Ranking (v0, lexicographic, no weighted score):**
1. Candidates in the proximal window before distal ones.
2. `links_repo = yes` before no.
3. Higher reach or engagement band first.
4. Smaller `|time_offset_h|` first.

The rank-1 candidate's `trigger_type` is the burst's coded trigger (core field C9).

`trigger_confidence` (C10, below) grades one burst's attribution. It is **not** the retired low/medium/high confidence of mechanism cards (ADR-025, retired by ADR-049.7); patterns carry no confidence level (§7).

**Attribution confidence (`trigger_confidence`, ordinal):**
- `high`: the burst onset has precision `hour`, and the rank-1 candidate meets all of:
  - it is in the proximal window, with `t0 − 24 h ≤ time ≤ t0 + 2 h` at hour precision;
  - `links_repo = yes`;
  - band `r3` or above, or `hn_front_page`;
  - no other candidate in the proximal window has an equal or higher band.
- `medium`: the rank-1 candidate is in the proximal window with `links_repo = yes`, but one of these holds:
  - another candidate has a comparable band (co-triggers: list them all);
  - the candidate's timestamp or the burst onset has only day precision;
  - or it is outside the ±24 h / +2 h core.
- `low`: any of:
  - only distal candidates exist;
  - the rank-1 candidate has no repo link;
  - the only evidence is `inferred`;
  - the trigger is `none_observed` or `unknown`.

**Attribution is association, not causation.** Causal effects of trigger types come only from R8.1 event studies with robust estimators (LR [50][51][53]) and matched comparisons (LR [11]). Neither a pattern nor a report may call a coded trigger "the cause" on the strength of §6 alone.

### 6.4 Unobservable channels
Every attribution record carries `unobservable_channels`: the gap sources that could hold a trigger under current terms (ADR-010: Reddit, X, YouTube, Product Hunt, Lobste.rs, dev.to, Juejin, Zhihu, Bilibili; plus sources held by ADR-022 at coding time). This follows SM §5 item 8: missing data is not a null effect.

---

## 7. Neighbourhood patterns (PRD F20; replaces the mechanism cards of 0.1.0–0.2.0)

A **pattern** is something a brief's cases did (a sequence, an asset, a channel, a positioning) whose presence is coded on every winner and every matched loser of that brief, and reported with its loser contrast and counterexamples (PRD §5.2, R20.1). Patterns belong to **one brief version** (R20.3). There is no global library, no `candidate`/`promoted` status, no promotion rule and no low/medium/high confidence scale (ADR-047.3, ADR-049.7, PRD R20.2). Patterns are described in descriptive language and never called validated mechanisms.

Where patterns come from:
- **Seed hypotheses.** `docs/methodology/mechanisms/candidates.md` (and `schemas/mechanisms/candidates-v0.json`) is a starter list of pattern hypotheses with presence tests written in codebook fields. A brief chooses which ones to check **before its outcome sort** and records the choice in the brief version (pre-registered for the pilot).
- **Brief-specific hypotheses.** The user or the analyst may add hypotheses for a brief, written in the same form (§7.2), also before the outcome sort.
- **Exploratory patterns.** A pattern first noticed after the outcome sort (for example while reading coded cases) is reported only under **exploratory**, with the same fields.

### 7.1 Pattern record (every field is required; unknown values are written `unknown`)
| Field | Content |
|---|---|
| `id`, `name`, `brief_id`, `brief_version`, `codebook_version` | `id` is unique within the brief. A pattern from the seed list keeps the seed id (for example `MC-01`) in `seed_id`. |
| `origin` | `seed` \| `brief_hypothesis` \| `exploratory` (exploratory = defined after the outcome sort) |
| `description` | What the cases did, in operational terms |
| `participation_motive` | Why people took part and shared. Summarised **only** from item-level `stated_reason` codes (enum in the JSON; reasons refer to attributes of the *project*, never of the person), with citations. Never inferred about individuals. |
| `preconditions` | A list. Each item has a statement **and** a test written against codebook fields. Example: "`cli_devtools` module active AND an `install_one_liner` asset exists at T". A precondition that can't be tested is listed as `untestable`, and the pattern is then reported as descriptive only (it can't feed an adaptation note, PRD R10.3). |
| `required_assets` | Asset categories from §5 |
| `sequence_timing` | An ordered list of action-layer event types and trigger types, with offsets relative to T (median and range over the winners that show the pattern, with n) |
| `outcome_dimensions` | Any of `attention \| adoption \| community \| business`; results are always shown per dimension (PRD §5.3) |
| `presence_test` | The rule that turns codebook fields into `present \| absent \| unknown` (§7.4) |
| `counts` | For winners and for matched losers separately: n present, n absent, n unknown, and n total (§7.3) |
| `loser_contrast` | Prevalence among winners and among matched losers (present ÷ (present + absent)), each with n and a Wilson 95 % interval; the difference; the matched-pair table (pairs where only the winner shows it, only the loser, both, neither, and pairs with an unknown side); the `unknown` share on each side, flagged when the two sides differ by more than 20 percentage points. Descriptive: no p-values. |
| `supporting_cases` | Winners with `presence = present`, by `case_id` |
| `counterexamples` | (a) matched losers with `presence = present`; (b) winners with `presence = absent`. Each by `case_id`. An empty list is written "none found", never left out. |
| `field_reliability` | For every coded field the presence test rests on: α (with the bootstrap 95 % CI, n pairable, and the unknown rate per coder) from this brief's double coding, and its label (§10.4) |
| `reliability_label` | `low reliability` if any field in `field_reliability` is labelled `low reliability` or `reliability not assessed`; otherwise none. Always also `LLM-coded, not human-validated` unless a human sample validated those fields (R7.5). |
| `sensitivity_flags` | The flags of the cases involved: `definition_sensitive` (the case's role changes under a sensitivity alternative, R4.9, OM §8), `sensitive_to_fake_star_filter` (OM §4), and `balance_limited` (the loser contrast rests on a matching covariate that misses the balance target, PRD §9.2) |
| `event_study` | Optional. Estimator (not plain TWFE with staggered triggers, LR §4.2), estimate and 90 % CI, data version and script path, for R8.1 event studies. Pre-registered or labelled `exploratory`. `none` when not run. |
| `evidence_label` | `insufficient evidence in this neighbourhood` when §7.3's minimums aren't met; otherwise none |
| `unobservable_channels` | As in §6.4. The contrast uses the same channels on both sides (SM §5 item 8). |
| `modules` | The adaptive modules the pattern is scoped to (§8), if any |
| `changes` | Per re-run of the brief (R20.3): the brief version and data version compared, the cases added and removed, and how `counts` and `loser_contrast` changed |
| `recommendable` | `false` for anti-patterns (seed MC-12, MC-13) and for anything PRD §4 rules out; such patterns are detected and contrasted, never recommended (F10, R12.4). Otherwise `true`. |

Patterns refer to cases by `case_id` only (§12 P6).

### 7.2 Writing a hypothesis
A hypothesis checked in a brief has, before the outcome sort: `name`, `description`, `preconditions` (with tests), `required_assets`, the expected `sequence_timing` order, `outcome_dimensions`, `modules`, a `presence_test`, and a **contrast reading**: what winner-versus-loser prevalence would be consistent with it, and what would count against it. Thresholds inside a presence test (for example "≥ 3 distinct days") are fixed in the brief version; changing one after the outcome sort makes the pattern exploratory.

### 7.3 Reporting rule (PRD R20.2)
- Every pattern is shown with its `counts`, `loser_contrast` and `counterexamples`, whatever they show. Null and negative contrasts are reported with the same prominence as positive ones (analyst rule).
- **"Insufficient evidence in this neighbourhood"** (v0 design choice): the pattern is labelled so when fewer than **10** winners or fewer than **10** matched losers have a known value (`present` or `absent`), or when fewer than **3** cases in total are `present`. It is still shown with its n. A brief may set stricter minimums in its version (never looser after the outcome sort).
- A pattern whose presence test rests on a field labelled `low reliability` or `reliability not assessed` carries that label wherever it appears (report, D1, D3 plans, D4 assets); it is not dropped (ADR-047.7).
- Overlapping patterns (for example seed MC-01, MC-02 and MC-03) are reported together, so that the same cases aren't read as independent support.
- A trigger coded under §6 is association, not cause; causal language needs an R8.1 event study (§6.3).

### 7.4 `pattern_support` (per case × pattern, within one brief)
- **`presence`** (C11a, coded, nominal): `present | absent | unknown`.
  - `present` needs every precondition test and at least one sequence element of the pattern, supported by evidence.
  - `absent` needs positive evidence that the element was missing *in an observable channel*. Example: the README at T is captured and has no install one-liner.
  - Otherwise `unknown`.
  - Some presence values are set by the pipeline, not by coders (for example seed MC-12 from the StarScout campaign flag). Such units are marked `derived` and excluded from α (§10.2).
- **Role-based reading** (derived, not coded; replaces the coded `direction`, C11b, of 0.1.0–0.2.0): from `presence` and the case's role in the brief.
  | Role | `present` | `absent` | `unknown` |
  |---|---|---|---|
  | winner | supporting case | counterexample (winner without it) | not counted |
  | matched loser | counterexample (loser with it) | consistent | not counted |

  A winner without the pattern is a counterexample in F20's sense, not a refutation: patterns don't claim necessity. Deriving this from the role, instead of coding it, means coders never need the outcome or the role (§11.6).
- **`strength`** (derived): `strong` if every precondition and sequence element is supported by `supported`-level evidence (edges §4.4; reliability ≥ medium). Otherwise `weak`.

---

## 8. Adaptive modules (R6.2)

**How activation works:**
- Activation is decided on inputs frozen at T, the same inputs as for the category (§9). The exception is `relaunch_pivot`, which uses the timeline.
- Modules can co-activate.
- Each case records `modules_active` with a reason and evidence for each module. Per-module activation is core field C5.
- A module's extra fields are coded only when that module is active. Otherwise they are `not_applicable`.

| Module | Activation criteria (any one suffices unless stated) | Extra fields |
|---|---|---|
| `ai_hype` | Primary or secondary category is `ai-apps-agents` or `ml-infra`; **or** the description, topics or README hero at T names an ML model, an LLM, or an agent framework as the core of the product | `model_dependency` (`proprietary_api | open_weights | own_model | mixed | unknown`); `adjacent_model_release` (a model release by a major lab within ±14 days of T, with evidence; the reason for this field is that hype-cycle timing is a known confounder: fake stars are concentrated in AI/LLM, LR [17][20], and see H-L1); `benchmark_claim_present`; `demo_type`; `fake_star_campaign_flag` (from OM §4); `novelty_claim` (from v0.2.0 this is the case-level field of §5.1, coded once per case and shared here) |
| `b2b_oss_saas` | Evidence dated ≤ T + 90 d of any of: a paid or hosted offering from the same project, a pricing page (OM `biz.pricing_page`), a commercial or dual licence, a "book a demo" or enterprise call-to-action | `business_model` (`open_core | hosted_cloud | dual_license | support_services | none_observed | unknown`); `license_at_T` (SPDX); `license_changes` (events); `readme_cta` (`signup | waitlist | demo | contact_sales | none | unknown`); `launch_week` (bool, per the §3.4 campaign rule; LR [63] is a practitioner source only); `funding_mention` (`self_reported` only, ADR-021) |
| `chinese_ecosystem` | The README at T is mainly Chinese, or has a Chinese README variant; **or** the project has a V2EX mention in the case window. The location of a person is **never** used (§12 P1). | `bilingual_readme` (bool); `zh_channel_mentions` (**V2EX only**, and only once H2 Q8 clears it: ADR-010, SM §2.19); `unobservable_channels_zh` (always lists Juejin, Zhihu and Bilibili as GAP, SM §2.20–2.22; Gitee is not audited, so `unknown`); `gitee_mirror` (`unknown` unless a first-party artifact states it). **The module reports "insufficient evidence" for channel attribution by default** (SM §5 item 6). No V2EX member is profiled (TM-19 conditions; PIPL note in SM §2.19). |
| `cli_devtools` | Primary category is `devtools`, **or** the repo at T ships a command-line entry point (an install one-liner, a Homebrew formula, a `bin` entry in npm, a console script in Python, a Cargo binary) | `install_channels` (multi: `brew | cargo | npm | pip | go | docker | curl_sh | binary_release | other`); `terminal_demo_asset` (bool, §5); `alternative_to_positioning` (a quoted "X alternative" or "replaces X" span, or `none`); `shell_integration` (bool); `homebrew_series_available` (coverage, OM) |
| `corporate_backed` | The repo owner is a GitHub organisation that a first-party artifact identifies as a company or foundation; **or** the README at T states the project is maintained or sponsored by an organisation | `backing_type` (`large_company | startup | foundation | unknown`); `org_channel_use` (the organisation's own blog or accounts carried the launch: bool, with evidence); `pre_T_org_member_commit_share` (an aggregate share of `author_association ∈ {OWNER, MEMBER}`, with no individuals); `org_audience_band` (a reach band of the organisation's official channel, §4.5) |
| `relaunch_pivot` | The timeline has a `relaunch` or `pivot` event; **or** the repo was more than 365 days old at T and has evidence of an earlier launch; **or** `rename = true` | `prior_launch_ref` (event or evidence id); `gap_days` (days since the prior launch); `change_kinds` (multi: `major_version | rewrite | rename | positioning | license | category`); `prior_case_role` (the earlier case's role in the same brief: `winner | matched_loser | shortlisted | not_in_brief`; replaces `prior_case_outcome_class`, whose global classes are retired, ADR-049.12); `prior_case_id` |

---

## 9. Category taxonomy (reconciled with ADR-017)

**Decision: adopt the 15 provisional categories of ADR-017 unchanged**, as `category-taxonomy v0.1.0` (inside codebook v0.1.0; unchanged in v0.2.0 and v0.3.0).
- **No ids are added, removed, merged or split, and no definition changes.** The diff against ADR-017 is empty.
- **What this codebook adds is only decision rules for the boundaries.** They make the existing definitions easier to apply; they don't redefine them.
- **What the category is for since 0.3.0.** It is a case descriptor: it activates modules (§8), helps describe a brief's neighbourhood, and can appear in a brief's field boundaries (PRD R18.1). It **no longer defines normalization cells or strata**: outcome percentiles are computed within the brief's final shortlist (ADR-049.8, OM v2 §3), and there are no global strata (PRD R4.2 retired). A change to the taxonomy is a codebook version bump (§13); there is no category-change quota (ADR-049.6).
- The coverage of each category, and the assignment method, were in OM v1 §3.1; since OM v2 they are here (the table below and the assignment rules after the boundary rules).

Categories (ids and coverage as in ADR-017 and OM v1 §3.1):

| id | Covers |
|---|---|
| `ai-apps-agents` | LLM apps, agents, AI assistants, prompt tooling |
| `ml-infra` | ML/DL frameworks, training/inference engines, model serving, vector search |
| `devtools` | CLIs, editors and plugins, build tools, testing, linters, dev productivity |
| `web-frontend` | UI frameworks, component libraries, CSS, static-site tools |
| `backend-libs` | general-purpose libraries, web/API frameworks, SDKs |
| `data-db` | databases, data engineering, analytics, BI |
| `infra-ops` | containers, orchestration, IaC, CI/CD, observability, networking |
| `security` | security tools, auth, secrets, scanners |
| `self-hosted-apps` | end-user apps, self-hosted alternatives to SaaS |
| `mobile-desktop` | mobile, desktop and cross-platform app frameworks and apps |
| `lang-runtime` | programming languages, compilers, runtimes, package managers |
| `crypto-web3` | blockchain, crypto, web3 |
| `lists-learning` | awesome-lists, tutorials, books, courses, interview prep, prompt collections |
| `media-games-science` | games, graphics, audio/video, scientific and hardware/embedded |
| `other` | anything else |

Boundary rules, applied in this order:
1. **`lists-learning` first.** Apply the rule pre-pass (assignment step 2 below): names starting with `awesome-`, or READMEs that are mostly link lists. Prompt collections go here as well, even when the subject is AI.
2. **`crypto-web3` next.** It applies if the core function depends on a blockchain, whatever the tooling type.
3. **AI split.**
   - If the product's core function *is* a model-driven application or agent, the category is `ai-apps-agents`. This holds even when the product is a CLI or devtool; the secondary category then records `devtools`.
   - If it trains, serves, runs, evaluates or indexes models or embeddings, including vector databases, the category is `ml-infra`.
4. **Function beats deployment mode.**
   - A self-hosted observability stack is `infra-ops`, not `self-hosted-apps`.
   - `self-hosted-apps` is for end-user applications: notes, media, productivity, home, and SaaS alternatives for non-developers.
5. **Imported versus run.**
   - A library imported into code is `backend-libs`, or `web-frontend` if it runs in the browser UI.
   - A tool developers run is `devtools`.
   - Package managers and compilers are `lang-runtime`.
6. **Primary purpose for security.** A scanner, auth server or secrets tool is `security`, even when it is infra-shaped.
7. **`other` needs a free-text note.** If `other` exceeds 10% of a brief's cases, that triggers a taxonomy review (a codebook version bump if anything changes).

- Secondary category: optional, from the same list.
- **Assignment** (from OM v1 §3.1, unchanged):
  1. Inputs frozen **as of T**: repo description, GitHub topics, primary language, and the first 4,000 characters of the README at the last commit before T. No post-T information.
  2. Rule pre-pass: names starting with `awesome-`, or READMEs that are mostly link lists → `lists-learning`.
  3. Otherwise an LLM classification through `LLMClient` (F15) returns one primary category, an optional secondary one, and a confidence. Confidence < 0.6 goes to the review queue (R7.3).
  4. Stored with `taxonomy_version`, `prompt_version`, model id and codebook version. The category never changes after the outcome is known, except by a taxonomy version bump applied to all of the brief's cases.
- For a brief's deep forensics the primary category is core field C4 and is double-coded like every core field (§10); the 10 % double-coded sample of OM v1 no longer applies.

---

## 10. Reliability: per-brief, per-field agreement (R7.2, R7.5, PRD §9.2)

### 10.1 Rule (replaces gate G1; ADR-047.7, ADR-049.6)
- Each brief's deep forensics are coded by two independent passes (different prompts or models, R7.2), with an adjudicator on disagreements (§11.5).
- **Krippendorff's α is computed per field, per brief**, on the values of passes A and B after citation validation and before adjudication (§11.5), and shown in the brief's report for **every** coded field, core or not.
- **Label, don't drop.** A field with α < 0.70 is labelled **`low reliability`**. Every finding that rests on it (a pattern whose presence test uses it, a contrast, an asset or trigger table) carries the label wherever it appears. Nothing is dropped because of α, and no brief is blocked by it.
- **`LLM-coded, not human-validated`** is added to every finding whose fields were coded only by LLM passes. If a human calibration sample exists (WORK_ORDER H3, optional), the LLM–human α is reported per field the same way, and a field with LLM–human α < 0.70 is labelled `low reliability` too.
- **Why 0.70.** It sits in Krippendorff's "tentative" band (LR [71], H-L9). The report also shows the bootstrap 95 % CI (§10.5) and the band (≥ 0.800 firm, 0.667–0.800 tentative; LR [71], secondary source). The 0.70 cut is the PRD's (§9.2); it is never lowered after data (pre-registration README rule 6).
- **There is no category-change quota, no attempt limit and no codebook freeze tied to α.** Revising the codebook between briefs is an ordinary version bump (§13).

### 10.2 α variant and handling of `unknown` (unchanged from 0.2.0, plus the unit exclusions formerly in the pilot pre-registration)
- **Nominal fields:** α with the nominal difference function (δ = 0 if the values are equal, 1 otherwise). `unknown` counts as a **value**, so one coder's `unknown` against another's substantive code is a disagreement. This keeps `unknown` from inflating α.
- **Ordinal fields:** α with the ordinal difference function (LR [70]). `unknown` cannot be ordered, so it is treated as **missing** in ordinal α. Each ordinal field also has a companion nominal α on the binary `known | unknown`. The field's label is `low reliability` if **either** statistic is below 0.70.
- **Multi-valued fields** (for example module activation, manipulation flags) are split into one binary nominal field per value.
- **Units excluded from α.** A unit whose value the pipeline sets before any coder sees it is not a coder judgment: `unknown` with reason `held`, `coverage_gap` from derived source-coverage metadata, `not_applicable` from an inactive module, and `derived` presence values (§7.4). These are excluded from α, and their count per field is reported.
- **Report the `unknown` rate per field and per coder**, and the `citation_failed` rate per field and pass, next to α.

### 10.3 Core fields (C1–C11a; C11b retired in 0.3.0)
Core fields are coded and double-coded in **every** brief's deep forensics. Non-core fields are coded when a brief needs them (a module is active, or a pattern hypothesis uses them) and are then double-coded and reported the same way.

| Id | Field | Unit | Level | α variant |
|---|---|---|---|---|
| C1 | `reliability` (effective, per coded item; §2.3) | coded item | ordinal (high > medium > low) | ordinal α, plus nominal α on known/unknown |
| C2 | `first_party` (`yes`, `no`, `unknown`) | item | nominal | nominal α |
| C3 | `event_type_supported` (`prep`, `launch`, `relaunch`, `pivot`, `none`) | item | nominal | nominal α |
| C4 | `category_primary` (§9) | case | nominal | nominal α |
| C5 | `module_active.<module>` (six binary fields) | case | nominal (binary) | nominal α per module |
| C6 | `edge_type` for a candidate pair (`published`, `redistributed`, `cited`, `replied`, `none`) | candidate pair | nominal | nominal α |
| C7 | `edge_evidence_level` (§4.4) | candidate pair | ordinal (explicit > attributed > inferred > none) | ordinal α, plus nominal α on known/unknown |
| C8 | `asset_category` (§5) | parser-extracted asset instance | nominal | nominal α |
| C9 | `trigger_type` of the rank-1 candidate (§6.2) | burst | nominal | nominal α |
| C10 | `trigger_confidence` (§6.3) | burst | ordinal | ordinal α, plus nominal α on known/unknown |
| C11a | `pattern_support.presence` (§7.4) | case × pattern checked in the brief | nominal | nominal α, pooled over patterns, and per pattern where §10.4's minimum is met |

- **C11b retired.** `mechanism_support.direction` (0.1.0–0.2.0) is no longer coded: the supporting-case and counterexample reading is derived from `presence` and the case's role (§7.4). The id C11b is not reused.
- **"Promotion-deciding" fields are retired** with the promotion rule (ADR-047.3, ADR-049.7). What a pattern rests on is listed per pattern in `field_reliability` (§7.1).
- **Not core.** Agreement is reported for each of these whenever they are coded: `phrase` and `benchmark_claim` unitizing; `stated_reason`; `prep_kind`; `pivot_dimension`; module extra fields; `novelty_claim` and `novelty_kind` (§5.1). `burst_shape` is derived, so it has no α.

### 10.4 Minimum units and labels
- A statistic is **assessed** only if all of these hold (restated from the withdrawn pilot pre-registration §6.3, unchanged):
  1. **n_pairable ≥ 30**: units with a value from both coders after missing values are removed (ordinal α and the known/unknown companion count units separately);
  2. **expected disagreement D_e > 0**: at least two distinct values occur;
  3. **for binary statistics** (C5 modules, known/unknown companions, per-value splits), the rarer value occurs at least 5 times in the pooled 2·n values.
- **Why 30:** for a balanced binary field one disagreement among n units moves α by about 2/n; at n = 30 that is about 0.07, at n = 10 about 0.2. This is a design choice, not a published rule.
- A statistic that isn't assessed is labelled **`reliability not assessed`** (with the reason and n), and findings resting on it carry that label, exactly as for `low reliability`.
- **Companion with no unknowns.** If neither coder used `unknown` or `not_applicable` on any unit, the companion is reported as "1.0 (degenerate: no unknowns)".
- **Small briefs.** Case-level fields (C4, C5, `novelty_claim`) have one unit per case, so a brief with 15 + 15 cases has 30 units at most, and per-pattern C11a has one unit per case. Such fields will often be `reliability not assessed`; the report says so rather than coding extra cases. A brief may code extra shortlist candidates purely to measure α (the former "reliability supplement"), if it says so in its version before the outcome sort; those codes never enter a contrast.

### 10.5 Confidence intervals
- Nonparametric bootstrap over **units** (rows of the reliability matrix), B = 10,000 resamples, percentile 95 % CI, fixed seed recorded in the report. Resamples with D_e = 0 are dropped and counted; if more than 5 % are dropped the CI is labelled "unstable".
- The α implementation must reproduce the worked examples in LR [70] before use, and the version is pinned in the report.

---

## 11. Coding rules (R6.3, R7.1–R7.4)

### 11.1 Snapshot or drop
- Every coded value cites **at least one `evidence_id`** and, for each one, a **locator** that the validator can check against the stored snapshot.
- If the citation doesn't exist, or the locator isn't found, **the pipeline drops the coded value. It is not merely flagged** (PRD §5.1, R7.1).
- A dropped value becomes `unknown`, with reason `citation_failed`.

### 11.2 Locators
| `locator_type` | Used for | Check |
|---|---|---|
| `text_span` | Text snapshots (HTML, JSON string fields, Markdown) | The quoted span, after Unicode NFC and whitespace collapsing, is an exact substring of the snapshot's extracted text, after the same deterministic redaction (ADR-006) is applied to both sides. No other normalisation is allowed. |
| `json_pointer` | Machine records (counts, timestamps, parent ids) | An RFC 6901 pointer plus the expected value. The value at the pointer must be equal. |
| `blob_hash` | Images, GIFs and other binary assets inside a snapshot | The SHA-256 of the embedded or linked blob, which must be present in the snapshot bundle |

Span rules:
- A quoted span is **minimal**: it holds only what supports the value, and at most 300 characters.
- It is copied from the **redacted** text, so it holds pseudonyms, never handles (§12).

### 11.3 `unknown`, `not_applicable`, `unobservable`
- **`unknown`:** the evidence is absent or insufficient to decide. Use it freely. **Absence is never coded as `no` or `absent`** unless a rule says so for that field; the `absent` value of C11a (§7.4) is one example. Each `unknown` carries a `reason`:
  - `no_evidence`
  - `insufficient_evidence`
  - `citation_failed`
  - `coverage_gap` (the source's window doesn't cover the date)
  - `unobservable_channel` (a GAP source, ADR-010)
  - `held` (ADR-022)
  - `conflicting_evidence`
- **`not_applicable`:** the field doesn't apply. Examples: a module field when the module is inactive; adoption when there is no package (ADR-014).
- **Nothing is imputed** (ADR-014).

### 11.4 Provenance on every coded value (R6.3, R7.4)
Each coded value records:
- `codebook_version` (for example `0.2.0`);
- `prompt_id` and `prompt_version`, or `human` for manual passes;
- `model_id` (for example `claude-opus-5`, ADR-007), or `human`;
- `llm_backend` (`subscription | api | none`);
- `coder_pass` (`A | B | adjudicator | human`);
- `run_id`;
- `coded_at`;
- `coder_confidence` (`low | medium | high`).

Values with `coder_confidence = low` go to the review queue (R7.3).

### 11.5 Double coding and adjudication (R7.2, R7.3)
- **Pass independence.**
  - Pass A and pass B use different prompts or different models, and neither sees the other's output.
  - A human coder finishes their pass before looking at any other pass (analyst rule).
- **Adjudication.**
  - The adjudicator sees both codes and the evidence, with option order randomized to counter position bias (LR [80]).
  - It returns one value with a citation, or `unknown`.
- **Review queue.** Each of these goes to the queue:
  - every disagreement on a core field;
  - every `coder_confidence = low`;
  - a random audit sample, with a fixed seed, drawn from agreed items.
- **α is computed on passes A and B before adjudication.**
- **Prompt stability.** Before a prompt version is frozen for a brief's coding, the pipeline runs a prompt-stability check (LR [78]) on development cases that are not among the brief's winners or losers. It is reported as exploratory.
- **LLM-label error.** Contrasts computed from LLM codes can be biased by coding error (LR [79]). Whether a brief applies an error correction, and with which estimator and human-labelled subsample, is fixed in its version before the outcome sort (pre-registered for the pilot). Without one, contrasts carry the `LLM-coded, not human-validated` label (§10.1).

### 11.6 Blinding and no tuning after outcomes
- Coders never see a case's role in the brief (winner, matched loser), its pair membership, its outcome values or percentiles, or the brief's success definition while coding any field. These are masked in prompts and in the review UI, and case ids are replaced by random coding ids with winners and losers interleaved.
- There is no longer an exception: the supporting-case and counterexample reading is derived from `presence` and the role after coding (§7.4), so no field needs the outcome.
- Unavoidable leakage (case-window evidence shows bursts and mention volume) is recorded as a limitation, not treated as a breach.
- The codebook version, prompts, pattern hypotheses and their presence-test thresholds are fixed in the brief version before the outcome sort. A change after it is labelled exploratory for that brief (§0 item 1).

---

## 12. Privacy rules (control CB-11; binding on every section)

- **P1 — No sensitive attributes.** No field, prompt or free-text note may code, infer or describe any of:
  - GDPR Art. 9 categories: racial or ethnic origin, political opinions, religious or philosophical beliefs, trade-union membership, genetic data, biometric data, health, sex life or sexual orientation;
  - FADP Art. 5(c) categories: religious, ideological, political or trade-union views or activities; health, intimate sphere, racial or ethnic origin; genetic and biometric data; administrative and criminal proceedings or sanctions; social-assistance measures;
  - demographic or location attributes of individuals: gender, age, nationality, location.

  Prompts instruct the model to ignore personal characteristics. Output schemas are closed (DPIA R9; LQ-9). If a snapshot happens to contain such content, it isn't coded, and quoted spans must avoid it.
- **P2 — No individual scoring.** Nothing ranks, scores or classifies a natural person. This covers influence scores, "top amplifiers" lists, and reliability ratings of people. Reach is stored only as a band (§4.5, CB-10). Trigger ranking (§6.3) ranks *items* for one burst and is never aggregated per account.
- **P3 — No cross-platform identity resolution.** Accounts on different platforms are never linked or merged, whether by name, handle similarity, avatars or writing style. First-party status comes only from the project's own channels, or from the item's own self-identification (§3.4).
- **P4 — Account-level graphs are held** (ADR-022, until LQ-8 is answered): §4.1.
- **P5 — Public-figure rule:** §4.6. It is inactive for natural persons until LQ-7 is answered and an ADR exists.
- **P6 — Nothing names individuals in outputs.** No private individual, matched loser, or repo owned by a personal account is named in any output (ADR-022; CB-14, CB-20). Patterns refer to cases by `case_id`. Seed pattern hypotheses name no repo.
- **P7 — Retention.** Coded values derived from person-level evidence inherit `retention_class = person_level_24m` (docs/compliance/retention-policy.md). When the raw data is dropped, coded facts that carry no person identifier are kept.
- **P8 — Public repo.** No coded value, quoted span, snapshot, pseudonym or user's brief is ever committed (PRD R18.9). The examples in this codebook are generic and invented. They are labelled as illustrations, not data.

---

## 13. Versioning (R6.3)

- **Semver.**
  - **Patch:** wording, examples, clarifications that change no code value.
  - **Minor:** a new enum value, field, module or core field. Existing codes stay valid.
  - **Major:** an enum value is removed, renamed, or redefined so that existing codes may change; or a threshold or window used by derived fields changes.
- **Every coded value records `codebook_version`,** and every brief report records the version it used (PRD R18.6). A minor or major bump states in the changelog which fields must be re-coded, and on which cases. A re-run of a brief under a new version re-codes the affected fields and reports what changed (R18.4, R20.3).
- **Before 1.0.0.** A change that would otherwise be major bumps the **minor** version (as for 0.2.0 and 0.3.0). It still needs a changelog entry. If a brief's pre-registration pins the old version, the change needs a dated amendment to that pre-registration stating what data had been seen.
- **When 1.0.0 comes.** Until 0.2.0, 1.0.0 was reserved for the M4 pilot's G1 freeze, which is withdrawn (ADR-049.5, ADR-049.6). 1.0.0 is now **not tied to any gate**. Proposed (open issue): tag 1.0.0 after the M15 pilot report is verified, from the version it used plus any fixes it found, before the M18 release.
- **Pinning per brief.** A brief pins the codebook version before its outcome sort. Changing a definition for a brief after its outcome sort makes the affected findings exploratory for that brief (§0 item 1). There is no held-out re-run route: ADR-019 is superseded (ADR-049.5).
- **Retired in 0.3.0:** the G1 "< 10 % of codebook categories changed" count and its denominator (ADR-024.7, superseded by ADR-049.6), and the rule that pre-attempt changes aren't a G1 revision round.

---

## CHANGELOG

- **0.1.0 (2026-09-25), M4-T1.** First draft. It defines:
  - evidence types, claim basis, the 3-level reliability scale with anchors, and manipulation flags;
  - the two-layer event taxonomy (derived `burst | quiet`; coded `prep | launch | relaunch | pivot`);
  - the spread graph at community and publication level, with account nodes disabled (ADR-022), four edge types, four evidence levels, the `supported` threshold (≥ `attributed` and hash-valid and time-ordered), reach bands (CB-10), and a conservative public-figure rule that is inactive for natural persons;
  - 21 asset categories;
  - trigger candidates, windows, 13 trigger types, lexicographic ranking and confidence;
  - the mechanism card schema, confidence levels, and §9.3 quoted verbatim;
  - six adaptive modules;
  - the category taxonomy adopted unchanged from ADR-017 / OM §3.1, with boundary rules added;
  - 12 core fields (C1–C11b) with α variants, of which C3, C4, C9, C11a and C11b are flagged as promotion-deciding;
  - coding, locator, provenance and privacy rules (CB-11).
- 2026-09-25 — errata (no version bump; codebook not yet used for coding): C7 in the JSON now lists the known/unknown companion statistic, matching §10.2 (the table and JSON had omitted it).
- 2026-09-25 — errata (no version bump; codebook not yet used for coding), M3-T8: `depends_on_adrs` in the JSON and the ADR list in the header replace ADR-012 (stargazers API, superseded; GitHub closed the stargazer lists on 2026-06-30) with ADR-032 and ADR-035. No code value changes. **Still open, not fixed by this erratum:** §2.1 and §2.3 examples and §3.1–3.2 (`burst | quiet` derivation and required evidence) still name the stargazers API and `starscout_filtered`; the burst derivation's series needs a decision before pilot unitizing (pilot amendment 1, "Not changed").
- **0.2.0 (2026-09-25), M4-T1d + M4-T2c.** Written before any coding, unitizing, derived event or outcome data. **No coding, unitizing or burst/quiet derivation ever used v0.1.0**, so there is nothing to re-code. `schemas/codebook/v0.1.0.json` is kept unchanged as a record (SHA-256 `6c955baf…28704`); the new file is `schemas/codebook/v0.2.0.json`.
  - **(a) Burst and quiet series (M4-T1d; closes the "still open" item of the M3-T8 erratum above).** §3.1–3.3: `burst | quiet` are derived from the `raw` star-history series on endpoint days (ADR-032.3, ADR-035), for every case, instead of `starscout_filtered` from the stargazers API, which no longer exists. Detection is `velocity-v0` on endpoint days, and the onset follows OM §2.1 exactly: the hour where hourly watch-list snapshots cover the 48-hour window, otherwise the day. The hourly baseline is `μ_h = μ_d / 24`. The burst that holds the case's `T_burst` takes the recorded onset. Burst end, merge gap and minimum quiet keep their numbers (3 days, 7 days, 7 days) but count endpoint days instead of UTC days, and use raw instead of filtered counts. `gharchive_coverage_ratio` is dropped from burst evidence. Coverage gaps are now missing star-history days. §6.1 gains a proximal window for day-precision onsets (`[t0 − 48 h, end of onset day + 6 h]`), and §6.3 states that `high` trigger confidence needs an hour-precision onset. v0.1.0 did not define either case, because it assumed hour-precision onsets. §2.1, §2.3 and §2.4 examples now name star-history and the hourly snapshots; no anchor or rule changes.
  - **(b) `novelty_claim` (M4-T2c; MC-09).** New case-level fields `novelty_claim` (`present | absent`, plus `unknown`) and `novelty_kind` (`new_in_kind | new_approach | new_combination | other`), §5.1. Both are non-core (§10.3). The `ai_hype` extra field of the same name now shares the case value.
  - **Why this is a minor bump (0.2.0) and not a patch (0.1.1).** Adding a field is minor under §13. Change (a) is not wording-only either: it changes the input series and day basis of a derived field. Strictly, that is the "threshold or window used by derived fields" kind of change §13 calls major, but 1.0.0 is reserved for the pilot freeze, so before 1.0.0 it bumps the minor version (§13, new bullet, as for outcome-thresholds under ADR-035). Doing (a) as 0.1.1 and (b) as 0.2.0 would pin two versions in a row with no coding between them, so both ship together as 0.2.0.
  - **G1 count.** No enum value in `g1_category_change_denominator` was added, removed or renamed. The denominator stays 79, and the verifier's JSON diff of those nine enums is empty. The new enums are non-core and outside the denominator. The `burst` and `quiet` values keep their definitions ("abnormally high star velocity against the repo's own baseline"; "≥ 7 days not in a burst"); only the series they are derived from changed. G1's "< 10% changed" is measured between revision rounds of the pilot. The pilot hasn't started, so this change isn't a round; v0.2.0 is the version going into attempt 1 (pilot amendment 2).
  - **Pilot pin:** `docs/preregistration/2026-09-25-pilot-amendment-2.md`.
- **0.3.0 (2026-09-26), M11 (re-scope to per-brief neighbourhood analysis; ADR-047, ADR-049).** Written before any coding, unitizing, derived event or outcome data; no version has ever been used for coding, so there is nothing to re-code. `schemas/codebook/v0.1.0.json` and `v0.2.0.json` stay unchanged as records; the new file is `schemas/codebook/v0.3.0.json`. The v0.2.0 pin in `docs/preregistration/2026-09-25-pilot-amendment-2.md` lapses with the withdrawal of that chain (`2026-09-26-pilot-amendment-3.md`).
  - **(a) G1 removed (ADR-047.7, ADR-049.6).** §10 rewritten: α per field, per brief, shown for every coded field; findings on a field with α < 0.70 are labelled `low reliability`, not dropped; a statistic below the minimum units is labelled `reliability not assessed`; `LLM-coded, not human-validated` label. Dropped: the "fails G1" wording, "not assessed = fail", the "< 10 % categories changed" rule and its 79-value denominator (`g1_category_change_denominator`, ADR-024.7), the pre-attempt revision-round rule, the promotion-deciding flag and the M4-T0 α ≥ 0.80 question. The minimum-units rule, prevalence floor and bootstrap (formerly in the withdrawn pilot pre-registration §6.3, §6.5) and the unit exclusions (formerly §4.5) move into §10.2, §10.4 and §10.5 unchanged.
  - **(b) Mechanism cards → neighbourhood patterns (PRD F20, ADR-047.3).** §7 rewritten: pattern record with n among winners and matched losers, loser contrast (shares with Wilson intervals, matched-pair table, unknown-share flag), counterexamples ("none found" stated), per-field reliability and labels, case sensitivity flags, optional event study, per-brief-version change record. Removed: `status` (`candidate`/`promoted`), the promotion rule (§9.3), `effect_estimate` as a promotion route, `applicable_strata`, `saturation_trend` (R16.2 retired), `deciding_field_alpha`, global `history`. New reporting minimums for "insufficient evidence in this neighbourhood" (≥ 10 known per side, ≥ 3 present; v0 design choice).
  - **(c) Confidence levels retired (ADR-049.7, retires ADR-025).** The low/medium/high card confidence and its rules are removed. `trigger_confidence` (C10) stays: it grades one burst's attribution, not a pattern.
  - **(d) `mechanism_support` → `pattern_support`; C11b retired.** C11a is now `pattern_support.presence` (unit: case × pattern checked in the brief). The coded `direction` (C11b) is replaced by a reading derived from presence and the case's role (§7.4), so coders no longer need outcomes and the outcome-aware coding pass is gone (§11.6). The id C11b is not reused.
  - **(e) No global cells or strata (ADR-049.8, ADR-049.12).** §9: the category is a case descriptor, not a normalization cell or stratum; the category table and assignment method, formerly in OM v1 §3.1, now live here. `relaunch_pivot.prior_case_outcome_class` → `prior_case_role`. Blinding (§11.6) masks role, pair membership, outcome values and the success definition instead of outcome classes.
  - **(f) Burst derivation unchanged, per repo.** §3.2 still derives `burst | quiet` from `raw` star-history endpoint days, now stated as fetched per repo (ADR-047.8, PRD R19.5). Hourly snapshots refine onsets only where they exist (pre-re-scope cache; the watch list is deleted, ADR-047.6). GH Archive is never used for onsets. The series completeness check (formerly pilot amendment 1, A2) is restated in §3.2 unchanged. No number, window or rule of the derivation changed.
  - **(g) Other wording.** Header, scope note, §0 item 1 (no pilot calibration or held-out re-run; changes after a brief's outcome sort are exploratory), §2.1 (GH Archive discovery-only), §4.6 criterion 4 (per brief), §5.1 status, §6.2 (`hn_post`: poller runs only during runs and launch mode, ADR-049.1), §11.5 (prompt stability and LLM-label error per brief), §12 P6 and P8 (patterns; no briefs in git), §13 (1.0.0 no longer tied to a gate). Privacy rules P1–P8 are otherwise unchanged.
  - **Why a minor bump (0.3.0).** Removing enum values (`status`, `confidence`, `direction`) and renaming a field are major changes under §13; before 1.0.0 they bump the minor version (§13).
