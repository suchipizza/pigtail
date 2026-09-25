# pigtail codebook

**Version:** 0.1.0 (semver; see §13 and the CHANGELOG at the end) · **Status:** draft for the M4 pilot. Nothing here has been checked against coded data yet.
**Task:** M4-T1 · **Requirements:** PRD F5 (R5.1–R5.5), F6 (R6.1–R6.3), F7 (R7.1–R7.4), F9 (R9.1–R9.3), §9.2, §9.3; compliance control CB-11 (docs/compliance/dpia.md §9).
**ADRs this depends on:** ADR-010 (source clearances), ADR-012 (stars from the stargazers API), ADR-014 (tags and status, nothing imputed), ADR-015 (time anchor T), ADR-017 (provisional categories), ADR-019 (outcome thresholds), ADR-022 (interim privacy holds).
**Machine-readable enums:** `schemas/codebook/v0.1.0.json` (`codebook v0.1.0`). If this document and the JSON disagree, that is a bug. Until it is fixed, the JSON wins for validators and this document wins for meaning.
**Author role:** `analyst`, working with researcher discipline. `LR [n]` means reference n in `docs/research/literature.md` §8. `SM` means `docs/research/source-matrix.md`. `OM` means `docs/specs/outcome-model.md`.
**Data version:** none. The codebook comes before any data. No case has been coded and no outcome has been looked at.
**Code commit:** `e4988ec`, read on 2026-09-25. The working tree had uncommitted M1 changes; none of them touch coding.

---

## 0. How to read this codebook

1. **Definitional choices are not findings.** Every threshold, window and band in this codebook (burst gaps, trigger windows, reach bands, confidence criteria) is a v0 design choice. None of them is an empirical result. The pilot (M4-T3) calibrates them once, and after that they change only through an ADR and a held-out re-run (ADR-019's policy, applied to the codebook in §13).
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
| `mechanism_support` | (case, mechanism) link (§7) | `ms_…` | Coded |

**Unitizing rule.** Wherever it is possible, units are cut out *deterministically* by code before any coding happens: items, burst segments, candidate edge pairs, trigger candidates, and asset instances that a parser can extract (images, GIFs, links, headings, titles). Both coders then code the same units, so α is computed on identical units. Units that only an LLM can cut out (phrases, benchmark claims, stated reasons) are not core fields in v0.1.0 (§10.3).

---

## 2. Evidence types and reliability scale (R5.1)

### 2.1 Evidence type (`evidence_type`, derived from the connector plus the parser; a coder may correct it)

| Value | Definition | Typical sources (clearance per ADR-010) |
|---|---|---|
| `platform_metric` | A machine-generated count or series about a project, published by the platform that hosts it | Stargazers API (TM-02), registry downloads (TM-07 to TM-11), deps.dev dependents (TM-12), HN points and comment counts (TM-03/04), HN rank polls (TM-04) |
| `platform_event` | A machine-generated event record | GH Archive event (TM-01), GitHub release, tag or commit record (TM-02) |
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
| `high` | 3 | (a) `claim_basis = observed`; (b) captured as raw machine-readable data from the platform that hosts the fact (`capture_mode = api_json`), or as a project artifact fetched at a pinned commit or version; (c) timestamp precision is hour or better where time matters | Stargazers API page with `starred_at`; HN item JSON with `created_at`; a GitHub release record; the README fetched at commit `c` and cited for "the README said X at c" |
| `medium` | 2 | Any one of: (a) `observed`, but captured as `rendered_html`, `screenshot` or `archive`; (b) data from a secondary aggregator whose method is documented (deps.dev dependents); (c) timestamp precision is only day-level where time matters; (d) `first_party_statement` about the project's own *actions*, such as "we posted X on date D" | A project blog post captured as HTML with a visible date; a Wayback capture of a pricing page; a docs page stating the release date |
| `low` | 1 | Any one of: (a) `claim_basis = reported`; (b) `first_party_statement` about *results* (users, revenue, growth) that no source verifies; (c) no date, or a date that can't be checked; (d) the snapshot quotes or paraphrases a source that was not itself captured | "Went viral on X" in a comment; a self-reported MRR figure (ADR-021); an undated landing page |
| `unknown` | — | Not yet assessed, or the assessment isn't possible | — |

Decision rules:
1. **Rate the weakest link.** If conditions from two levels apply, take the lower level.
2. **Corroboration can raise a `reported` claim to at most `medium`.** This requires that a second, independent evidence record at `medium` or above shows the same fact. Both evidence ids are cited. The raise applies only to the coded item, not to the evidence record.
3. **Manipulation flags don't change reliability. They are recorded next to it** (`manipulation_flag`, §2.4). A faithfully captured vote-ring post is still high-reliability evidence *that the post exists*.
4. **Relation to the schema.** `schemas/v0/evidence.schema.json` currently describes the levels by capture mode only. This codebook's anchors are the operative definition. The record-level `evidence.reliability` stores the level for `claim_basis = observed`. Each coded item stores its own effective reliability after applying rules 1 and 2. (A schema-description update is listed in the open issues.)

### 2.4 Manipulation flags (`manipulation_flag`, multi-valued)
- `fake_star_campaign_suspected`: the repo has a StarScout campaign flag (LR [20]; OM §4). This is a *suspicion*, never proof, and it is never named in a public output (LR §2.1).
- `vote_solicitation`: the snapshot shows a request to upvote or comment. Show HN guidelines: "Please don't ask friends to upvote or comment" (LR [65]).
- `undisclosed_paid_promotion_suspected`: a reported claim of paid promotion that the item does not disclose. This value is always `low` reliability.
- `disclosed_paid_promotion`: an ad or sponsorship the item itself discloses. This is not a violation, but it confounds effect estimates (LR [64]).
- `none_observed`.

---

## 3. Event taxonomy (R5.2)

### 3.1 Two layers
Events sit in two layers that may overlap in time:
- **Attention layer: `burst | quiet`.** Derived by code from the scoring star series (`starscout_filtered` from the stargazers API, ADR-012, OM §4). The two values split the case window with no gaps. Not double-coded.
- **Action layer: `prep | launch | relaunch | pivot`.** Coded from evidence. These are things the project did.

The case window is [T − 90 d, T + 365 d] (T per ADR-015). Anything outside the window is recorded only if a rule below needs it (for example a prior launch for `relaunch`).

Every event records: `id`, `type`, `start`, `end` (null for point events), `time_precision` (`hour | day | unknown`), `evidence_ids`, quoted spans (§11), coder provenance, and `codebook_version`.

### 3.2 `burst` (derived)
- **Definition.** A period in which star velocity is abnormally high against the repo's own baseline.
- **Onset.** The detection rule is `velocity-v0`: ≥ 100 filtered stars in 48 h and z ≥ 3 against the 30-day baseline. The onset hour is taken from OM §2.1, unchanged.
- **End (v0 choice).** The start of the first run of 3 consecutive UTC days on each of which filtered stars are ≤ `μ_d + 3·√μ_d`. `μ_d` is the pre-onset 30-day baseline mean of stars per day; when `μ_d = 0`, use `μ_d + 1`.
- **Merging (v0 choice).** Two bursts separated by fewer than 7 days are one burst with `multi_peak = true`.
- **Required evidence.** The stargazers-API snapshot(s) that cover the segment. The locator is a JSON pointer to the counts (§11.2). Also recorded: the series version and `gharchive_coverage_ratio` (ADR-009).
- **Descriptive attribute `burst_shape`:** `sudden_fast_decay | gradual_build | mixed | unknown`. It follows the exogenous and endogenous relaxation classes of Crane & Sornette (LR [35]). The v0 rule: `sudden_fast_decay` when the peak day falls within 48 h of onset and velocity is below 50% of peak within 7 days; `gradual_build` when the peak is more than 7 days after onset; `mixed` otherwise. This attribute is descriptive only. LR §3.4 warns that fits will be noisy on sparse star series, and H-L3 tests this rather than assuming it.
- **Boundary cases.**
  - A burst caused by a fake-star campaign is still a `burst`. The case carries `manipulation_flag`, and the raw-versus-filtered re-run is flagged (ADR-020).
  - A download spike without a star spike is not a `burst` in v0.1.0. It is recorded as an exploratory observation.

### 3.3 `quiet` (derived)
- **Definition.** Every maximal interval of at least 7 days inside the case window that is not part of a burst.
- **Attribute:** `phase = pre_first_burst | inter_burst | post_last_burst`.
- **Required evidence.** The same series as `burst`.
- **Boundary cases.**
  - An interval shorter than 7 days between bursts has already been merged into the burst (§3.2).
  - Days whose source coverage is missing (for example a GH Archive hole, ADR-009) are `unknown`, not `quiet`. They split the quiet interval.

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

### 4.1 Status in v0.1.0: community and publication level only
- ADR-022 holds all account-level spread graphs until LQ-8 is answered. LQ-8's default is "community- and publication-level graphs only".
- So in v0.1.0 the `account` node type is **defined but disabled**. The JSON has `"account_nodes_enabled": false`.
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

Otherwise the edge is `unsupported`. It is stored for review and excluded from structural virality (LR [29]), from breadth counts (H-L4, LR [31]) and from mechanism evidence.

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

### 4.6 Public-figure exception (conservative; **inactive for natural persons in v0.1.0**)
- LQ-7 is open. Its default is: "Only organisations and publications are named, and only in the private UI. No natural person is named in any output."
- v0.1.0 therefore defines the criteria below but does not apply them to natural persons. Applying them needs the LQ-7 answer plus an ADR.
- **Proposed criteria for a natural person.** All of the following must hold:
  1. The person speaks in a **professional public-communication role** about software. Examples: a journalist or editor for a publication with a masthead, or an official spokesperson or DevRel speaking *for an organisation*.
  2. The coded activity is part of that public role (FADP Art. 31(2)(f): "public activities").
  3. The role is documented in a snapshot from a cleared source. The person's own professional page, or the publication's masthead, counts.
  4. The person is not a maintainer of a matched loser, and does not own a repo on a personal account in the panel (LQ-7 context).
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

---

## 6. Trigger attribution for bursts (R5.5)

### 6.1 Candidates (derived)
For each burst with onset `t0` (hour precision):
- **Proximal window (v0 choice):** items timestamped in `[t0 − 48 h, t0 + 6 h]`.
  - The +6 h tolerance absorbs day-precision timestamps and hourly binning.
  - The 48 h look-back matches the short windows used in prior HN-launch studies: 3 days in LR [8]; 24 h, 48 h and 7 d in LR [40].
- **Distal window:** `[t0 − 7 d, t0 − 48 h)`. Only for publications with a known lag (newsletters, weekly digests) or for items that a proximal item explicitly cites. The rationale for the lag must be quoted.
- **Candidate items:** every captured item in these windows that mentions the repo (URL, name, or owner/name), plus the project's own `release` records.

### 6.2 Trigger types (`trigger_type`, coded, nominal)
| Value | Definition |
|---|---|
| `hn_front_page` | An HN story about the repo that reached the front page. Shown by an own rank poll (rank ≤ 30, a config assumption and **unverified**, OM O7) or by the Algolia `front_page` tag (semantics undocumented, OM §1) |
| `hn_post` | An HN story with no front-page evidence (`unknown` when polling coverage is missing, never "not on the front page") |
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

**Attribution confidence (`trigger_confidence`, ordinal):**
- `high`: the rank-1 candidate meets all of:
  - it is in the proximal window, with `t0 − 24 h ≤ time ≤ t0 + 2 h` at hour precision;
  - `links_repo = yes`;
  - band `r3` or above, or `hn_front_page`;
  - no other candidate in the proximal window has an equal or higher band.
- `medium`: the rank-1 candidate is in the proximal window with `links_repo = yes`, but one of these holds:
  - another candidate has a comparable band (co-triggers: list them all);
  - the timestamp has only day precision;
  - or it is outside the ±24 h / +2 h core.
- `low`: any of:
  - only distal candidates exist;
  - the rank-1 candidate has no repo link;
  - the only evidence is `inferred`;
  - the trigger is `none_observed` or `unknown`.

**Attribution is association, not causation.** Causal effects of trigger types come only from R8.1 event studies with robust estimators (LR [50][51][53]) and matched comparisons (LR [11]). Neither a card nor a report may call a coded trigger "the cause" on the strength of §6 alone.

### 6.4 Unobservable channels
Every attribution record carries `unobservable_channels`: the gap sources that could hold a trigger under current terms (ADR-010: Reddit, X, YouTube, Product Hunt, Lobste.rs, dev.to, Juejin, Zhihu, Bilibili; plus sources held by ADR-022 at coding time). This follows SM §5 item 8: missing data is not a null effect.

---

## 7. Mechanism cards (R9.1–R9.3, PRD §9.3)

### 7.1 Card schema (every field is required; unknown values are written `unknown`)
| Field | Content |
|---|---|
| `id`, `name`, `version`, `status` | `status` is `candidate` or `promoted` (§7.3) |
| `description` | What the mechanism is, in operational terms |
| `participation_motive` | Why people took part and shared. It is summarised **only** from item-level `stated_reason` codes (the enum is in the JSON; the reasons refer to attributes of the *project*, never of the person) and cites them. It is never inferred about individuals. |
| `preconditions` | A list. Each item has a statement **and** a test written against codebook fields. Example: "`cli_devtools` module active AND an `install_one_liner` asset exists at T". Preconditions that can't be tested block promotion. |
| `required_assets` | Asset categories from §5 |
| `sequence_timing` | An ordered list of action-layer event types and trigger types, with typical offsets relative to T (median and range over supporting cases) |
| `outcome_dimensions` | Any of `attention | adoption | community | business` (PRD §5.3; no composite) |
| `supporting_cases` | `case_id`s, each with its `mechanism_support` record (§7.4) and outcome class |
| `contradicting_cases` | `case_id`s, each with a required written explanation |
| `loser_contrast` | Prevalence among winners and among their matched losers (with n), the difference and its CI, the estimator, the pre-registration reference, and `exploratory` if it was not pre-registered |
| `effect_estimate` | Estimator (not plain TWFE with staggered triggers, LR §4.2), estimate, 90% CI, data version, report path |
| `confidence` | `low | medium | high` (§7.2) |
| `applicable_strata` | The strata cells with n and coverage. "insufficient evidence" where n is too small (R10.2) |
| `saturation_trend` | `rising | stable | declining | insufficient_evidence`, from R8.4 / R16.2 (a hazard or effect trend across cohorts) |
| `unobservable_channels` | As in §6.4. Loser contrasts must use the same channels on both sides (SM §5 item 8). |
| `deciding_field_alpha` | α, with CI, for each promotion-deciding field that the card relies on (§10.4) |
| `sensitivity_flags` | For example `sensitive_to_fake_star_filter` (ADR-020), `sensitive_to_threshold_version` |
| `modules` | The adaptive modules the card is scoped to (§8) |
| `history` | Append-only list of {date, version, change, case_id, direction (`supports | contradicts`), codebook_version} (R9.3) |

### 7.2 Confidence levels (PRD §9.3 says they are defined here)
Confidence is ordinal and is tied to the promotion status:
- **`low`:** every `candidate` card. A promoted card also drops to `low` while any sensitivity flag in `sensitivity_flags` is open and unresolved.
- **`medium`:** a `promoted` card (all four §9.3 conditions hold) that does not meet every `high` condition.
- **`high`:** a `promoted` card that also meets **all** of these conditions:
  1. ≥ 10 supporting winner cases from ≥ 3 strata;
  2. **both** routes of §9.3's second condition hold: the winner-versus-loser prevalence difference *and* an event-study effect whose 90% CI excludes zero;
  3. every promotion-deciding field it relies on has α ≥ 0.80 with the CI lower bound ≥ 0.70 (LR [71]: ≥ 0.800 is the conventional level for firm conclusions; primary source unverified);
  4. no sensitivity flag is raised;
  5. every contradicting case has an explanation that is itself coded from evidence, not asserted.

These are v0 definitional choices (ADR needed, see the return notes). They make confidence stricter; they never loosen §9.3.

### 7.3 Promotion rule (PRD §9.3, restated verbatim; this codebook does not relax it)
> A mechanism becomes `promoted` only if all of the following hold:
> - ≥ 5 supporting winner cases from ≥ 2 strata.
> - A higher prevalence among winners than among their matched losers, or an event-study effect whose 90% CI excludes zero.
> - Contradicting cases are listed and explained.
> - Its preconditions are stated in a testable form.
>
> Otherwise it stays `candidate`.

How the codebook applies it (these rules add conditions, never remove them):
1. "Supporting winner case" means a case with outcome class `winner` (OM, ADR-019) and a `mechanism_support` record with `presence = present` and `direction = supports`.
2. "Strata" are PRD R4.2 cells. The category counts as a stratum only at the level of §8's taxonomy version.
3. Prevalence is computed from `mechanism_support.presence` (core field C11a). `unknown` counts in neither the numerator nor the denominator. The share of `unknown` is reported on each side, and if the two sides differ by more than 20 percentage points the contrast is flagged.
4. **Reliability gate:** a field that fails the α gate (§10.4) cannot feed promotion.
5. A card that is promoted and later fails a condition goes back to `candidate`. The history records why.

### 7.4 `mechanism_support` (coded per case × card)
- **`presence`** (C11a, nominal): `present | absent | unknown`.
  - `present` needs every precondition test and at least one sequence element from the card, supported by evidence.
  - `absent` needs positive evidence that the element was missing *in an observable channel*. Example: the README at T is captured and has no install one-liner.
  - Otherwise the value is `unknown`.
- **`direction`** (C11b, nominal): `supports | contradicts | neutral`.
  - `supports`: present, and the outcome dimension moved as the card predicts.
  - `contradicts`: present, but the predicted outcome did not follow. Example: a matched loser that ran the mechanism.
  - `neutral`: absent or unknown. **A winner without the mechanism is not a contradiction.** Cards do not claim necessity.
- **`strength`:** `strong` if every precondition and sequence element is supported by `supported`-level evidence (edges §4.4; reliability ≥ medium). Otherwise `weak`.

---

## 8. Adaptive modules (R6.2)

**How activation works:**
- Activation is decided on inputs frozen at T, the same inputs as for the category (OM §3.1). The exception is `relaunch_pivot`, which uses the timeline.
- Modules can co-activate.
- Each case records `modules_active` with a reason and evidence for each module. Per-module activation is core field C5.
- A module's extra fields are coded only when that module is active. Otherwise they are `not_applicable`.

| Module | Activation criteria (any one suffices unless stated) | Extra fields |
|---|---|---|
| `ai_hype` | Primary or secondary category is `ai-apps-agents` or `ml-infra`; **or** the description, topics or README hero at T names an ML model, an LLM, or an agent framework as the core of the product | `model_dependency` (`proprietary_api | open_weights | own_model | mixed | unknown`); `adjacent_model_release` (a model release by a major lab within ±14 days of T, with evidence; the reason for this field is that hype-cycle timing is a known confounder: fake stars are concentrated in AI/LLM, LR [17][20], and see H-L1); `benchmark_claim_present`; `demo_type`; `fake_star_campaign_flag` (from OM §4); `novelty_claim` (quoted span; Fang et al. link novelty to stars and to lower participation, LR [13], H-L6) |
| `b2b_oss_saas` | Evidence dated ≤ T + 90 d of any of: a paid or hosted offering from the same project, a pricing page (OM `biz.pricing_page`), a commercial or dual licence, a "book a demo" or enterprise call-to-action | `business_model` (`open_core | hosted_cloud | dual_license | support_services | none_observed | unknown`); `license_at_T` (SPDX); `license_changes` (events); `readme_cta` (`signup | waitlist | demo | contact_sales | none | unknown`); `launch_week` (bool, per the §3.4 campaign rule; LR [63] is a practitioner source only); `funding_mention` (`self_reported` only, ADR-021) |
| `chinese_ecosystem` | The README at T is mainly Chinese, or has a Chinese README variant; **or** the project has a V2EX mention in the case window. The location of a person is **never** used (§12 P1). | `bilingual_readme` (bool); `zh_channel_mentions` (**V2EX only**, and only once H2 Q8 clears it: ADR-010, SM §2.19); `unobservable_channels_zh` (always lists Juejin, Zhihu and Bilibili as GAP, SM §2.20–2.22; Gitee is not audited, so `unknown`); `gitee_mirror` (`unknown` unless a first-party artifact states it). **The module reports "insufficient evidence" for channel attribution by default** (SM §5 item 6). No V2EX member is profiled (TM-19 conditions; PIPL note in SM §2.19). |
| `cli_devtools` | Primary category is `devtools`, **or** the repo at T ships a command-line entry point (an install one-liner, a Homebrew formula, a `bin` entry in npm, a console script in Python, a Cargo binary) | `install_channels` (multi: `brew | cargo | npm | pip | go | docker | curl_sh | binary_release | other`); `terminal_demo_asset` (bool, §5); `alternative_to_positioning` (a quoted "X alternative" or "replaces X" span, or `none`); `shell_integration` (bool); `homebrew_series_available` (coverage, OM) |
| `corporate_backed` | The repo owner is a GitHub organisation that a first-party artifact identifies as a company or foundation; **or** the README at T states the project is maintained or sponsored by an organisation | `backing_type` (`large_company | startup | foundation | unknown`); `org_channel_use` (the organisation's own blog or accounts carried the launch: bool, with evidence); `pre_T_org_member_commit_share` (an aggregate share of `author_association ∈ {OWNER, MEMBER}`, with no individuals); `org_audience_band` (a reach band of the organisation's official channel, §4.5) |
| `relaunch_pivot` | The timeline has a `relaunch` or `pivot` event; **or** the repo was more than 365 days old at T and has evidence of an earlier launch; **or** `rename = true` | `prior_launch_ref` (event or evidence id); `gap_days` (days since the prior launch); `change_kinds` (multi: `major_version | rewrite | rename | positioning | license | category`); `prior_case_outcome_class` (if scored); `prior_case_id` |

---

## 9. Category taxonomy (reconciled with ADR-017)

**Decision: adopt the 15 provisional categories of OM §3.1 unchanged**, as `category-taxonomy v0.1.0` inside codebook v0.1.0.
- **No ids are added, removed, merged or split, and no definition changes.** The diff against ADR-017 is empty, so nothing has to be re-normalized (ADR-017's "if it does, every case is normalized again" isn't triggered).
- **What this codebook adds is only decision rules for the boundaries.** They make the existing definitions easier to apply; they don't redefine them.
- **Why not revise now:**
  - A 6-case pilot can't justify merges or splits.
  - G1 requires that fewer than 10% of categories change in the last revision round, so churn has a cost.
  - OM §9 item 8 already schedules the merge and split review for the pilot on real coverage data.

Categories (ids and coverage exactly as in OM §3.1): `ai-apps-agents`, `ml-infra`, `devtools`, `web-frontend`, `backend-libs`, `data-db`, `infra-ops`, `security`, `self-hosted-apps`, `mobile-desktop`, `lang-runtime`, `crypto-web3`, `lists-learning`, `media-games-science`, `other`.

Boundary rules, applied in this order:
1. **`lists-learning` first.** Apply the rule pre-pass (OM §3.1 step 2): names starting with `awesome-`, or READMEs that are mostly link lists. Prompt collections go here as well, even when the subject is AI.
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
7. **`other` needs a free-text note.** If `other` exceeds 10% of pilot cases, that triggers a taxonomy review.

- Secondary category: optional, from the same list.
- Assignment inputs, method, confidence threshold (< 0.6 goes to review) and double-coding: as in OM §3.1. The primary category is core field C4.

---

## 10. Reliability: core fields for Krippendorff's α (G1, PRD §9.2, R7.2)

### 10.1 Gate
- G1 requires α ≥ 0.70 on **every** core field listed in §10.3, between independent coders (two LLM passes with different prompts or models, per R7.2).
- If a human calibration sample exists (H3), the LLM–human α on it must also be ≥ 0.70.
- 0.70 falls in Krippendorff's "tentative" band (LR [71], H-L9). The pilot reports α with bootstrap 95% CIs (Hayes & Krippendorff, LR [68]; the computation follows LR [70]).
- The minimum number of pairable units per field is set in the pilot pre-registration (M4-T2). A field with too few units counts as **not assessed**, and a field that is not assessed fails G1.

### 10.2 α variant and handling of `unknown`
- **Nominal fields:** α with the nominal difference function (δ = 0 if the values are equal, 1 otherwise). `unknown` counts as a **value**, so one coder's `unknown` against another's substantive code is a disagreement. This keeps `unknown` from inflating α.
- **Ordinal fields:** α with the ordinal difference function (LR [70]). `unknown` cannot be ordered, so it is treated as **missing** in ordinal α. Each ordinal field also has a companion nominal α on the binary `known | unknown`, and **both** must pass the gate.
- **Multi-valued fields** (for example module activation, manipulation flags) are split into one binary nominal field per value.
- **Report the `unknown` rate per field and per coder**, next to α.

### 10.3 Core fields (v0.1.0)
| Id | Field | Unit | Level | α variant | Promotion-deciding (flag for M4-T0) |
|---|---|---|---|---|---|
| C1 | `reliability` (effective, per coded item; §2.3) | coded item | ordinal (high > medium > low) | ordinal α, plus nominal α on known/unknown | no |
| C2 | `first_party` (`yes | no | unknown`) | item | nominal | nominal α | no (it feeds C3) |
| C3 | `event_type_supported` (`prep | launch | relaunch | pivot | none`) | item | nominal | nominal α | **yes**: launch and relaunch sequences in cards |
| C4 | `category_primary` (§9) | case | nominal | nominal α | **yes**: strata for §9.3's "≥ 2 strata" |
| C5 | `module_active.<module>` (six binary fields) | case | nominal (binary) | nominal α per module | no |
| C6 | `edge_type` for a candidate pair (`published | redistributed | cited | replied | none`) | candidate pair | nominal | nominal α | no |
| C7 | `edge_evidence_level` (§4.4) | candidate pair | ordinal (explicit > attributed > inferred > none) | ordinal α | no |
| C8 | `asset_category` (§5) | parser-extracted asset instance | nominal | nominal α | no |
| C9 | `trigger_type` of the rank-1 candidate (§6.2) | burst | nominal | nominal α | **yes**: it defines the treatment for §9.3's event-study route (R8.1) |
| C10 | `trigger_confidence` (§6.3) | burst | ordinal | ordinal α, plus nominal α on known/unknown | no |
| C11a | `mechanism_support.presence` | case × candidate card | nominal | nominal α | **yes**: the prevalence contrast in §9.3 |
| C11b | `mechanism_support.direction` | case × candidate card | nominal | nominal α | **yes**: supporting and contradicting cases in §9.3 |

**Not core in v0.1.0.** Agreement is still reported for each of these, but none of them gates G1:
- `phrase` and `benchmark_claim` unitizing;
- `stated_reason`;
- `prep_kind`;
- `pivot_dimension`;
- `burst_shape` (derived);
- module extra fields.

Consequence: none of these fields may feed promotion until it is promoted to core in a later minor version and passes the gate.

### 10.4 Promotion-deciding fields
- The promotion-deciding fields are C3, C4, C9, C11a and C11b.
- Backlog M4-T0 is considering a stricter α ≥ 0.80 for exactly these fields (LR H-L9). This codebook **flags** them and does not decide the question.
- Until M4-T0 decides, the gate for these fields is the G1 gate (α ≥ 0.70). The `high` confidence level (§7.2) already requires α ≥ 0.80 on them.
- **In every case, a field that fails its gate cannot feed promotion** (LR H-L9 decision rule).

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
- `codebook_version` (for example `0.1.0`);
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
- **Prompt stability.** Before a prompt version is frozen for scale runs, the pipeline runs a prompt-stability check (LR [78]). Downstream mechanism tests use an LLM-label error correction, as recommended in LR [79]; the exact method goes in the M4-T2 pre-registration.

### 11.6 No tuning after outcomes
- Coders never see outcome classes while coding case, event, trigger or asset fields. Outcome classes are masked in prompts and in the review UI.
- The exception is `mechanism_support.direction`, which needs the outcome. It is coded in a separate, later pass, after `presence` is locked.

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
- **P6 — Nothing names individuals in outputs.** No private individual, matched loser, or repo owned by a personal account is named in any output (ADR-022; CB-14, CB-20). Mechanism cards refer to cases by `case_id`.
- **P7 — Retention.** Coded values derived from person-level evidence inherit `retention_class = person_level_24m` (docs/compliance/retention-policy.md). When the raw data is dropped, coded facts that carry no person identifier are kept.
- **P8 — Public repo.** No coded value, quoted span, snapshot or pseudonym is ever committed. The examples in this codebook are generic and invented. They are labelled as illustrations, not data.

---

## 13. Versioning (R6.3)

- **Semver.**
  - **Patch:** wording, examples, clarifications that change no code value.
  - **Minor:** a new enum value, field, module or core field. Existing codes stay valid.
  - **Major:** an enum value is removed, renamed, or redefined so that existing codes may change; or a threshold or window used by derived fields changes.
- **Every coded value records `codebook_version`.** A minor or major bump states in the changelog which fields must be re-coded, and on which cases.
- **Counting G1's "< 10% of codebook categories changed".**
  - The denominator is the total number of enum values across the coded enums in `schemas/codebook/<version>.json`: evidence types, event types, edge types, edge evidence levels, the reliability scale, asset categories, trigger types, modules and categories.
  - The numerator is the number of values added, removed or redefined in the last revision round.
  - The verifier computes it with a JSON diff.
- **After the pilot, the codebook freezes as v1.0.0.** A later change to a definition that affects promotion needs an ADR and a re-run on held-out cases (the same policy as ADR-019).

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
