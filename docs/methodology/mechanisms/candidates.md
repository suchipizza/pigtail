# Seed pattern hypotheses (starter list, v0.4.0)

**Task:** M4-T1b (v0.1.0, as "candidate mechanism cards"); M11 (v0.3.0, reframed); M21 verifier fix (v0.4.0, MC-12) · **Written:** 2026-09-25; reframed 2026-09-26 · **Author role:** `analyst`, working with researcher discipline
**Version:** 0.4.0 (see the changelog at the end) · **Requirements:** PRD F20 (R20.1–R20.3), §5.2; PRD F9 retired ("the seed candidate cards … remain useful as seed codes for patterns"); WORK_ORDER v2.0 §4.2 (M4 carried: "the seed candidate cards (as seed pattern codes)"); codebook `0.4.0` §7 (patterns) and §10 (C11a); outcome model v2.1 §4 (star anomaly checks, MC-12).
**Machine-readable copy:** `schemas/mechanisms/candidates-v0.json` (file version 0.4.0; the file name is kept because frozen pre-registrations cite it). If this file and the JSON disagree, that is a bug. Until it is fixed, the JSON wins for coding tools and this file wins for meaning (the same rule as the codebook). The v0.2.0 text ("candidate mechanism cards") is in git history: `git show archive/global-collection:docs/methodology/mechanisms/candidates.md`.
**Data version:** none. No case has been coded against any of these hypotheses. **Code commit:** `4f55e73` (v0.1.0); `0330bd4` (v0.3.0); `731cfe3` (v0.4.0).
**Citations:** `[n]` means reference n in `docs/research/literature.md` §8. Every number quoted here is copied from that review, and the review is the source. Nothing here adds a number the review doesn't state.

---

## 0. What this list is, and what it isn't

- **A starter list of pattern hypotheses a brief can check.** Each entry (MC-01 to MC-13; the `MC` ids are kept for traceability) describes something a project might do around its launch, with a **presence test** written in codebook fields. A brief picks the entries that fit its neighbourhood, may adapt their `[design]` thresholds, and may add its own, **before its outcome sort** (codebook §7.2; pre-registered for the pilot). The brief's report then shows each checked hypothesis as a **neighbourhood pattern**: n among its winners and among its matched losers, the loser contrast, the counterexamples and the per-field reliability labels (codebook §7.1, PRD R20.1).
- **Not global cards, and not findings.** There is no global mechanism library, no `candidate`/`promoted` status, no confidence level and no global history (ADR-047.3, ADR-049.7, PRD R20.2). Results live only in each brief's report and are never written back to this file. None of these entries has a supporting case, a counterexample or a contrast here.
- **Why reframe rather than retire.** PRD v2.0 (F9) and WORK_ORDER v2.0 (§4.2) carry these entries forward as seed codes for patterns. Their literature basis and their presence tests don't depend on the global scope; what did (status, confidence, promotion, strata, global history) is removed. Marking the list historical would force every brief to rewrite the same tests from the literature review.
- **The sources are practitioner writing and observational research.** Practitioner sources ([63][64][66][67]) are anecdotal and survivor-biased: they have no control group (LR §5). The academic sources are mostly observational without matched controls ([8][39][40]). The one matched design is Fang et al. [11]. Every number below describes **what the source reported**. None of them is an effect estimate for a pattern, and none may be copied into a pattern's `event_study`.
- **Anti-patterns.** MC-12 and MC-13 describe behaviour that PRD §4 rules out (fake stars, vote solicitation). They exist so that manipulation can be **detected and contrasted**. They are `recommendable: false`: F10 and R12.4 exclude them from plans and assets.
- **How preconditions are labelled.** Each precondition carries its origin:
  - `[LR n]`: the literature review states it;
  - `[design]`: a v0 choice made here so the hypothesis can be tested, **not** taken from the literature. A brief may change it before its outcome sort.

## 1. What every hypothesis carries

| Field | Meaning |
|---|---|
| `id`, `name`, `version` | Seed id (kept by any brief pattern built from it as `seed_id`), name, entry version |
| `description` | What the projects did |
| `literature_basis` | Reference numbers; the text says what each source reports |
| `preconditions` | Statement, test in codebook fields, origin (`[LR n]` or `[design]`) |
| `required_assets` | Asset categories from codebook §5 |
| `sequence_timing` | The hypothesised **order** of events. Offsets (median and range) come only from a brief's winners that show the pattern. |
| `presence_test` | `present` / `absent` / `unknown` rule for `pattern_support.presence` (C11a, codebook §7.4) |
| `outcome_dimensions` | The dimensions the sources associate with it; a brief's report shows results per dimension |
| `modules` | Adaptive modules it is scoped to, if any |
| `contrast_reading` | What winner-versus-matched-loser prevalence would be consistent with the hypothesis, and what would count against it |
| `recommendable` | `false` for MC-12 and MC-13 |
| `related_hypotheses` | H-L ids in the literature review |
| `unobservable_channels_default` | The GAP channels (ADR-010) and the channels held by ADR-022 as of 2026-09-26. A brief recomputes them for its own sources; the contrast uses the same channels on both sides (codebook §6.4). |

**Presence and the role reading.** These follow codebook §7.4:
- `present` requires every precondition test **and** at least one sequence element, each supported by evidence.
- `absent` requires positive evidence of absence in an observable channel.
- Anything else is `unknown`.
- Supporting cases and counterexamples are derived from presence and the case's role in the brief (a winner with it supports; a matched loser with it, or a winner without it, is a counterexample). Coders never see the role.

**Contrast readings are descriptive.** "Consistent" and "against" describe what the brief's counts would look like; with 15–25 pairs they are not tests. Where a hypothesis names an event study (R8.1), that study is pre-registered for the brief or labelled exploratory.

---

## 2. Hypotheses

### MC-01 — First-party launch post on Hacker News (Show HN / Launch HN)
- **Description:** the maker announces the project on HN with a first-party post. A burst of attention follows.
- **What the literature says:**
  - 72.7% (16) of viral-growth respondents linked their growth to successful social-media posts, "mostly Hacker News" [3].
  - For posts in the top 10% (≥ 132 upvotes), median stars were 74 in the 3 days before and 138 in the 3 days after. The comparison is pre/post with no control group [8].
  - At least 19% of AI developers promoted their projects on HN, and forks, stars and contributors rose after posting. The study is observational, and its peer-review status is unverified [39].
  - Across 138 AI/LLM launches, mean gains were +121 stars in 24 h, +189 in 48 h and +289 in 7 days. The "Show HN" tag was **not** significant (p = 0.39). The authors say the design can't establish causation [40].
  - Show HN must be something people can try [65].
- **Preconditions:**
  1. Something people can try exists when the post goes up [LR 65]. *Test:* an asset with `category ∈ {live_demo, install_one_liner}`, or a `readme_section` with `readme_kind = quickstart`, has `first_seen` ≤ the launch timestamp.
  2. The repo is public at launch [design]. *Test:* no `prep` event of kind `repo_made_public` is dated after the launch.
- **Required assets:** `title`; `tagline`; one of `live_demo`, `install_one_liner` or `readme_section:quickstart`.
- **Sequence (order only):** `prep` (any kind) → `launch` (`first_party = yes`, on the HN community node, with a quoted span containing "Show HN" or "Launch HN") → trigger `hn_post` or `hn_front_page` for the first burst → `burst`.
- **Presence test (C11a):** `present` if a `launch` event's evidence includes a `community_post` on the HN node with `first_party = yes`, **and** both precondition tests hold. `absent` needs HN coverage for the case window (HN capture enabled, with no coverage gap) and no first-party HN item.
- **Outcome dimensions:** attention (hypothesised). Adoption and community are unknown.
- **Modules:** none (general).
- **Contrast reading:**
  - *Consistent:* first-party HN launches are more prevalent among the brief's winners than among their matched losers, with `unknown` shares on the two sides within 20 pp.
  - *Against:* prevalence among matched losers is equal to or higher than among winners. That is the survivorship pattern H-L7 predicts for frequently recommended practices. [40]'s null result for the Show HN tag makes this plausible.
- **Related hypotheses:** H-L2, H-L7.

### MC-02 — HN front-page exposure
- **Description:** an HN story about the repo reaches the front page, whoever posted it. The exposure produces a short-run star jump.
- **What the literature says:**
  - Pre/post medians of 74 → 138 stars for top-10% posts [8].
  - Launch-day gains in [40].
  - A single post ranked #17 with 68 points drew about 35–40 unique visitors per minute on the front page and about 10 per minute on page two. That is one post (n = 1) [66].
  - HN popularity is a relatively strong reflection of intrinsic quality, so quality confounds any exposure effect [42].
  - **No peer-reviewed causal estimate of front-page position on GitHub stars was found** (LR §3.6 gap; H-L2).
- **Preconditions:**
  1. The story links the repo or its site [design]. *Test:* trigger candidate with `links_repo = yes`.
  2. A conversion asset is present at story time [design]. *Test:* as MC-01 precondition 1, with the story timestamp in place of the launch.
- **Required assets:** `title`, `link` (`link_kind = repo` or `landing_page`).
- **Sequence:** trigger `hn_front_page` (the rank-1 candidate, codebook §6.3) → `burst`, with onset inside the proximal window.
- **Presence test:** `present` if C9 = `hn_front_page` for any burst **and** both preconditions hold.
  - Front-page status can only be observed from pigtail's own rank polling, which runs only during scheduled runs and launch mode (ADR-049.1), or from the Algolia `front_page` tag, whose semantics are undocumented (OM §1.2).
  - So retrospective cases without the tag are `unknown` (reason `coverage_gap`), never `absent`.
- **Outcome dimensions:** attention.
- **Modules:** none.
- **Contrast reading:**
  - *Consistent:* the H-L2 event study (R8.1, robust estimator). Matched HN stories that stayed off the front page with similar early votes serve as controls, and the 90% CI of the 48 h star effect excludes 0.
  - *Against:* that CI includes 0, or front-page prevalence doesn't differ between winners and matched losers.
  - H-L2 also predicts the effect is **smaller** than naive pre/post figures such as [8] and [40].
- **Related hypotheses:** H-L2, H-L3 (fast post-burst decay after exogenous triggers [35]).

### MC-03 — HN posting in the 12–17 UTC window
- **Description:** the first HN story about the repo is posted between 12:00 and 17:00 UTC. It gets more early stars than a similar post at another time.
- **What the literature says:** posting at 12–17 UTC was associated with about +200 stars. This comes from one observational OLS study (HC1 errors) of 138 AI/LLM launches, and the authors don't claim causation [40]. It is the only source.
- **Preconditions:**
  1. The case has an HN story about the repo with an hour-precision `created_at` [design]. *Test:* an HN item with `time_precision = hour` among the trigger candidates or the launch evidence.
- **Required assets:** `title`.
- **Sequence:** `launch` or `hn_post` with `created_at` hour ∈ [12, 17) UTC → `burst`.
- **Presence test:** `present` if the earliest HN story about the repo has a `created_at` hour in [12, 17) UTC. `absent` if its hour is outside that window. `unknown` if there is no HN story, or HN coverage is missing.
- **Outcome dimensions:** attention.
- **Modules:** none. [40]'s sample is AI/LLM only, so a brief whose neighbourhood is AI/LLM is closest to the source.
- **Contrast reading:**
  - *Consistent:* among cases whose earliest story is on HN, the in-window share is higher among winners than among matched losers. Sequence matters here: the contrast is conditional on both sides having an HN story.
  - *Against:* the shares are equal, or the event-study effect of the posting window has a CI including 0.
- **Note:** this is a timing variant of MC-01 and MC-02. If MC-01 and MC-02 aren't supported, MC-03 is uninterpretable.

### MC-04 — Social-media posts linking the repo
- **Description:** posts on social platforms that link the repo raise star counts. The effect on contributors is smaller.
- **What the literature says:**
  - Matched design (tweeted vs matched untweeted projects, before/after): about +7% stars (+1.2 stars per tweet burst) and about +2% new contributors (+1 new developer per 250 tweet bursts) [11][12]. Replication package: [81].
  - Twitter was among the most common promotion channels in 100 popular repos [7].
  - Viral-growth respondents credited successful social-media posts [3].
- **Preconditions:**
  1. At least one social-platform item in the case window links the repo [design]. *Test:* an item on a social platform's community node with `links_repo = yes`.
- **Required assets:** `link` (`link_kind = repo`); a `tagline` or `title` in the post.
- **Sequence:** `social_post` trigger candidate(s) → `burst`. The social post doesn't need to be the rank-1 trigger.
- **Presence test:** `present` if the precondition holds and at least one such item falls in the proximal or distal window of a burst (codebook §6.1).
  - X is a GAP (ADR-010) and Bluesky is held (ADR-022).
  - Until Bluesky is cleared, this hypothesis is **`unknown` (reason `unobservable_channel` or `held`) for every case**, never `absent`.
- **Outcome dimensions:** attention and community, with community expected to be the smaller effect (H-L5).
- **Modules:** none.
- **Contrast reading:**
  - *Consistent:* higher prevalence among winners than matched losers. In an event study, the star effect is positive and the contributor effect is smaller.
  - *Against:* equal prevalence, or no star effect. H-L5 is falsified if the ratio of star effect to contributor effect is not greater than 2.
- **Caveat:** pigtail can only replicate this on Bluesky, which isn't X. Whether X-era results carry over is unknown.

### MC-05 — Launch week (multi-day announcement campaign)
- **Description:** the project ships and announces one major feature per day over consecutive days. The campaign follows a planned channel schedule, with community amplification.
- **What the literature says:**
  - Supabase describes a "Launch Week" [63]:
    - one major feature per day;
    - a minute-by-minute channel schedule;
    - community and angel amplification;
    - retrospectives.

    It also claims database growth of "47% month-on-month for the last 18 months". That figure is self-reported, from a practitioner source.
  - [67] cites coordinated launch events as one reason stars have inflated. It is a practitioner source.
- **Preconditions:**
  1. The project can ship several announceable features at once [design]. *Test:* ≥ 3 first-party announcement items in the campaign, each with a distinct quoted feature claim.
  2. There is an organisation behind the project [design; [63] is a company]. *Test:* `corporate_backed` or `b2b_oss_saas` module active. This is a scope assumption: the brief reports the pattern for its org-backed cases, and doesn't assume it is necessary.
- **Required assets:** `launch_post` (≥ 3), `title`, `link`.
- **Sequence:** `prep` → `launch` with `campaign = true` and sub-announcements on ≥ 3 distinct UTC days within 7 days [design threshold] → one or more bursts during the campaign. It may repeat as `relaunch` events at ≥ 30 days (codebook §3.6).
- **Presence test:** `present` if a `launch` or `relaunch` has `campaign = true`, with sub-items on ≥ 3 distinct UTC days within 7 days, and precondition 1 holds. When the `b2b_oss_saas` module is active, its `launch_week` field must agree.
- **Outcome dimensions:** attention; adoption (hypothesised, from [63]'s self-reported growth claim).
- **Modules:** `b2b_oss_saas`, `corporate_backed`.
- **Contrast reading:**
  - *Consistent:* higher prevalence among winners than among matched losers **among the brief's org-backed cases**.
  - *Against:* matched losers ran launch weeks at a similar rate. This is H-L7's survivorship prediction for [63].
- **Related hypotheses:** H-L7.

### MC-06 — Disclosed paid social promotion alongside a launch
- **Description:** paid promotion on a social platform runs around the same time as a community launch. Together they push the repo onto GitHub's trending page.
- **What the literature says:**
  - PostHog reports about $2,000 of paid Twitter promotion, which "combined with the successful launch on Hacker News, got our repo trending on GitHub" [64]. That is one case, from a practitioner source.
  - The review notes that paid promotion **confounds** any estimate of the HN effect.
- **Preconditions:**
  1. A first-party launch exists [design]. *Test:* a `launch` event exists.
  2. The paid promotion is disclosed [codebook §2.4]. *Test:* `manipulation_flag = disclosed_paid_promotion` on an item. `undisclosed_paid_promotion_suspected` never counts toward presence.
- **Required assets:** `launch_post` or `title`, `link`.
- **Sequence:** trigger candidate `paid_promotion` within ±7 days of the `launch` [design window] → `burst`.
- **Presence test:** `present` if both preconditions hold and the timing matches.
  - Disclosed paid posts on X are unobservable (GAP), and Bluesky is held.
  - So this hypothesis is expected to be `unknown` for almost every case. That is recorded, and not read as absence.
- **Outcome dimensions:** attention.
- **Modules:** none.
- **Contrast reading:**
  - *Consistent:* higher prevalence among winners than matched losers, with MC-01 held equal.
  - *Against:* equal prevalence.
- **Main use:** in practice, a **confounder flag** for MC-01 and MC-02 effect estimates. Cases with MC-06 present are reported separately in those event studies.
- **Not a non-goal:** disclosed paid promotion is advertising, not fake engagement (PRD §4).

### MC-07 — Active multi-channel first-party promotion
- **Description:** the maker actively promotes the project on several of its own and community channels around launch, rather than relying on one post.
- **What the literature says:**
  - 64.7% (22) of fast-growth respondents named active promotion as the main reason for their growth [3]. This is survey self-attribution.
  - Twitter, user meetings and blogs were the most common promotion channels in 100 popular repos [7].
- **Preconditions:**
  1. The project has an official channel of its own [design]. *Test:* a `project_artifact` blog, or a docs or site page linked from the repo at T.
- **Required assets:** `launch_post`, `link`.
- **Sequence:** first-party items (`first_party = yes`) appear on ≥ 2 distinct nodes (the project's own blog counts as one) within [T − 7 d, T + 30 d] [design thresholds] → one or more bursts.
- **Presence test:** `present` if the sequence condition holds with `supported`-level evidence. `absent` requires coverage of at least two observable venues showing only one first-party venue. Because many channels are unobservable (§1), expect a high `unknown` rate.
- **Outcome dimensions:** attention.
- **Modules:** none.
- **Contrast reading:**
  - *Consistent:* winners use more distinct first-party venues than matched losers. The count is ordinal and is compared across the brief's matched pairs.
  - *Against:* no difference.
- **Caveats:** user meetings [7] can't be observed at all. The survey attribution [3] comes from winners only (survivorship).

### MC-08 — Early cross-community breadth
- **Description:** within days of the first burst, several distinct communities or publications pick up the repo independently. This is breadth, rather than one large broadcast.
- **What the literature says:**
  - On Facebook photo reshares, early **breadth** predicted large cascade size better than depth [31].
  - Structural virality ranges from broadcast to viral spread, and popular items grow through every mix of the two [29].
  - Both sources are about social platforms other than GitHub.
- **Preconditions:** none known from the literature. As a pattern it is only actionable if a precondition can be found. Until then it is a descriptive **spread pattern** (H-L4) and can't feed an adaptation note (PRD R10.3).
- **Required assets:** unknown.
- **Sequence:** `burst` onset → edges of type `published` or `cited`, with `supported` status (codebook §4.4), into ≥ 3 distinct `community` or `publication` nodes within [onset − 48 h, onset + 72 h] [design thresholds].
- **Presence test:** `present` if the sequence condition holds. `absent` if the observable venues are covered and fewer than 3 nodes appear. `unknown` otherwise.
- **Outcome dimensions:** attention. Carry-over to adoption and community is unknown.
- **Modules:** none.
- **Contrast reading:**
  - *Consistent:* breadth is more prevalent among winners than matched losers with a similar launch-signal magnitude.
  - *Against:* no difference.
- **Caveat:** this hypothesis risks being tautological, because breadth is partly the outcome itself. Without a testable precondition it is reported as descriptive only (codebook §7.1, `preconditions`).

### MC-09 — Novelty positioning (entry version 0.3.0)
- **Description:** the project presents itself as novel, for example new in kind or a new combination. That positioning draws attention, but also comes with lower long-run participation.
- **What the literature says:** in the Python ecosystem, more novel projects get more stars. The same projects have smaller teams and a higher long-run risk of abandonment [13].
- **Preconditions:**
  1. There is a first-party novelty claim [design]. *Test:* the case-level `novelty_claim` (codebook §5.1, from 0.2.0) is `present`, with its quoted span.
  - `novelty_claim` is non-core (codebook 0.3.0 §10.3): a brief that checks MC-09 codes it, double-codes it, and the pattern carries its per-field reliability label.
  - The review doesn't describe how [13] measured novelty. Whether a quoted positioning claim is the same construct is **unknown**.
- **Required assets:** `tagline` or `title` carrying the claim.
- **Sequence:** the claim is present at T → `burst`.
- **Presence test:** `present` if `novelty_claim = present`; `absent` if `novelty_claim = absent`; `unknown` otherwise.
- **Outcome dimensions:** attention (expected +), community (expected −, T+90 returning external contributors).
- **Modules:** none (not limited to `ai_hype`).
- **Contrast reading:**
  - *Consistent:* novelty claims are more prevalent among the brief's winners than among their matched losers, and, among the winners, those with a claim have fewer returning external contributors at T+90 (`comm.returning_external_contributors@90`, OM §1.4) than those without (H-L6; a within-winner comparison, descriptive, meaningful only when the brief's primary dimension is attention).
  - *Against:* the community difference is null (H-L6 falsified).
- **Role reading:** as for every pattern (codebook §7.4). The v0.2.0 rule that a case `supports` only when both dimensions move as predicted is retired with the coded `direction`; the community part of H-L6 is reported as the within-winner comparison above.

### MC-10 — Release-driven attention
- **Description:** new releases produce bursts of attention.
- **What the literature says:** language, application domain and new releases are associated with star counts in 2,279 popular repos [4]. The association isn't causal and is limited to popular repos.
- **Preconditions:**
  1. The project publishes tagged releases [design]. *Test:* ≥ 2 GitHub release records before the burst being coded.
- **Required assets:** none required. `launch_post` (release notes) is optional.
- **Sequence:** `release` (platform event) → `burst`, with the release as a trigger candidate in the proximal window.
- **Presence test:** `present` if C9 = `release` for any burst and precondition 1 holds. `absent` if every burst has a rank-1 trigger other than `release` and release records are covered. `unknown` otherwise.
- **Outcome dimensions:** attention.
- **Modules:** none. `relaunch_pivot` is related (`change_kinds = major_version`).
- **Contrast reading:**
  - *Consistent:* release-triggered bursts are more prevalent among winners, **or** an R8.1 event study around release dates (named explicitly in R8.1) gives a 90% CI excluding 0.
  - *Against:* equal prevalence and a null event-study effect.

### MC-11 — "Try it now" asset at launch
- **Description:** at launch, the project offers a zero-friction way to try it: a hosted demo or a one-command install. Attention then turns into adoption.
- **What the literature says:** only the Show HN rule that a submission must be something people can try [65]. That is a platform rule, **not** evidence of any effect. This hypothesis rests on the thinnest basis in the set.
- **Preconditions:**
  1. A launch exists [design]. *Test:* a `launch` event (any venue).
- **Required assets:** `live_demo` or `install_one_liner`.
- **Sequence:** the asset's `first_seen` is ≤ launch → `launch` → `burst`.
- **Presence test:** `present` if a `launch` exists and a `live_demo` or `install_one_liner` asset has `first_seen` ≤ the launch timestamp. `absent` if the README and site at launch were captured and neither asset appears. `unknown` otherwise.
- **Outcome dimensions:** attention, adoption (hypothesised).
- **Modules:** none. The `cli_devtools` module records `install_channels`.
- **Contrast reading:**
  - *Consistent:* higher prevalence among winners than matched losers, and, among the brief's winners, higher adoption (primary-ecosystem downloads at T+90, OM §1.3) where the asset was present (descriptive).
  - *Against:* no prevalence difference.
- **Overlap:** MC-01's precondition 1 is this hypothesis's asset condition. They are coded separately, so MC-11 can be tested across all launch venues.

### MC-12 — Star inflation suspected from aggregate anomalies (fake-star proxy; anti-pattern: detection and contrast only)
- **Description:** purchased or coordinated fake stars inflate star counts. PRD §4 rules this out. This entry exists so that the effect can be measured and cases flagged. **It is never recommended** (F10, R12.4).
- **Changed in 0.4.0.** The literature's detector (StarScout) classifies individual stargazer accounts, which pigtail can no longer see for repos the operator doesn't own (outcome model (OM) v2.1 §4.1; LR §2.4). Presence now comes from pigtail's **aggregate anomaly checks** (`anomaly-v0`: a star spike with no matching forks, issues, downloads or external mentions, or an outlying stars-to-activity ratio; OM §4.2). These are a **proxy** for the campaigns the sources below describe, not the same measurement, and they are unvalidated: no precision or recall has been measured (OM §4.5). Star metrics stay "unfiltered, anomaly-checked".
- **What the literature says** (about account-level StarScout detection, not about pigtail's proxy):
  - StarScout found 18,617 repos with fake-star campaigns (3.81 M fake stars after post-processing). Activity surged in 2024.
  - Most fake stars promote short-lived phishing or malware repos. The rest go mostly to AI/LLM, blockchain, tool and tutorial repos [17][20].
  - Promotion effect: a 1% rise in fake stars in month t goes with +0.07% real stars in t+1 and +0.03% in t+2. Cumulative fake stars have a negative coefficient from about t+2 onward, which the authors read as "a liability in the long term" [20].
  - Ground truth for the Stargazers Ghost Network: [22]. An industry experiment in which the authors bought stars themselves: [26].
- **Preconditions:** none. It is detected, not chosen.
- **Required assets:** none.
- **Sequence:** an anomaly-flagged star spike (`spike_no_activity`) → `burst` (codebook §3.2: still a burst) → decay. A case flagged only by `ratio_outlier` has no dated element; it counts as `present` but contributes no sequence offset.
- **Presence test:** **derived, not coded**, from the case's `star_anomaly_flag` (OM §4.3):
  - `present` if `manipulation_flag = star_anomaly_flagged`, i.e. `star_anomaly_flag = true`: a `spike_no_activity` flag whose spike overlaps `[T − 30 d, T + k_max)`, or a `ratio_outlier` flag within the brief.
  - `absent` if `star_anomaly_flag = false`: no such flag, every spike in the window was checked against at least one activity channel (status `checked` or `no_spikes`), and the ratio check judged the case or couldn't only because it had fewer than 200 stars [design].
  - `unknown` otherwise (a spike no channel could be checked against, a missing ratio channel, or no star-history for the window).
  - Because the value comes from the pipeline, its C11a units are marked `derived` and excluded from α (codebook §10.2).
  - The flag is a suspicion, never proof, and no repo is called fake in any output (LR §2.3).
- **Outcome dimensions:** attention (short-lived +), adoption (expected −, H-L8).
- **Modules:** none. Category concentration (H-L1) shows only across briefs; within one brief the pattern is reported with its counts.
- **Contrast reading:**
  - *Consistent (H-L8):* flagged repos show a real-star uplift lasting < 2 months and lower T+365 adoption than matched unflagged repos.
  - *Against:* the adoption difference is null or positive.
  - *Proxy caveat (0.4.0):* a contrast here is about anomaly-flagged cases, not confirmed campaigns. A null contrast can't tell "no effect" from "the proxy misses campaigns or flags organic spikes" (OM §4.5), and the report says so next to the counts, with the `unknown` share on each side.
- **Sensitivity flag to set when tested:** `sensitive_to_star_anomaly` (OM §8.1 alternative D; replaces `sensitive_to_fake_star_filter`, ADR-070.4).

### MC-13 — Vote solicitation on HN (anti-pattern: detection and contrast only)
- **Description:** the maker asks people to upvote or comment on an HN launch. HN's guidelines forbid this. **It is never recommended.**
- **What the literature says:** only the Show HN guideline "Please don't ask friends to upvote or comment" [65]. There is no evidence of its effect. The codebook records it as a manipulation flag (codebook §2.4).
- **Preconditions:** none (detected).
- **Required assets:** none.
- **Sequence:** an item with `manipulation_flag = vote_solicitation` linked to an HN story → trigger `hn_post` or `hn_front_page` → `burst`.
- **Presence test:** `present` if any captured item carries `vote_solicitation` and references an HN story about the repo. `absent` is never coded, because solicitation happens mostly in unobservable channels (DMs, X, chat). So the value is `present` or `unknown`.
- **Outcome dimensions:** attention.
- **Modules:** none.
- **Contrast reading:** prevalence can't be compared validly, because `absent` is never observable and prevalence excludes `unknown` (codebook §7.1).
  - It is reported only as the count of `present` cases on each side, and is used to flag cases and to exclude them from MC-01 and MC-02 contrasts in sensitivity analyses.
  - *Against* has no defined meaning for this hypothesis.

---

## 3. Open issues
1. **Codebook gap (MC-09): closed in codebook 0.2.0**, which added the case-level `novelty_claim` field (§5.1). It stays non-core in 0.3.0: a brief checking MC-09 codes it and reports its α.
2. **Held and GAP channels.** MC-01, MC-02, MC-03 and MC-13 depend on HN, and MC-04 and MC-06 on social platforms (held or GAP). Until ADR-022's controls exist (ADR-049.2), those hypotheses are `unknown` on almost every case; their C11a units are then pipeline-forced and excluded from α (codebook §10.2), and the pattern shows "insufficient evidence in this neighbourhood".
3. **`[design]` thresholds are fixed per brief.** Examples: ≥ 3 days (MC-05), ≥ 2 venues (MC-07), ≥ 3 nodes within 72 h (MC-08), ±7 days (MC-06). A brief keeps them or sets its own in its version **before its outcome sort** (pre-registered for the pilot). A change after the outcome sort makes the pattern exploratory for that brief. There is no pilot calibration round (withdrawn, `docs/preregistration/2026-09-26-pilot-amendment-3.md`).
4. **Overlapping hypotheses.** MC-01/MC-02/MC-03 and MC-01/MC-11 overlap. Reports show them together, so that the same cases aren't read as independent support (codebook §7.3).
5. **Neighbourhood fit.** Most sources are about HN-launched, English-language, often AI/LLM projects. A brief whose neighbourhood differs (for example the `chinese_ecosystem` module, or B2B projects launched through sales channels) should add brief-specific hypotheses rather than rely on this list alone.

## Changelog
- 2026-09-25 — 0.1.0: seed set of 13 candidate cards (M4-T1b).
- 2026-09-25 — 0.2.0: MC-09's precondition test, presence test and modules now use the case-level `novelty_claim` of codebook 0.2.0 (§5.1) instead of the `ai_hype` extra field, and the card is no longer limited to `ai_hype`. The same text is in `schemas/mechanisms/candidates-v0.json` (MC-09 version 0.2.0, file version 0.2.0). Pilot amendment 2, B4. Made before any C11a unit was cut or any case coded. No other card changed.
- 2026-09-26 — 0.3.0 (M11; ADR-047.3, ADR-049.7; PRD F20): reframed from global candidate mechanism cards to a starter list of pattern hypotheses a brief can check. Removed from every entry: `status`, `confidence`, `supporting_cases`, `contradicting_cases`, `effect_estimate`, `applicable_strata`, `saturation_trend`, `deciding_field_alpha`, `sensitivity_flags`, `history` (results live only in each brief's report). "Loser contrast" (supports/contradicts) became "contrast reading" (consistent/against), with strata replaced by the brief's own cases and §9.3 references removed. MC-02's rank-polling note follows ADR-049.1. MC-08 loses the forecasting-feature reference (forecasting test withdrawn); MC-09's contrast no longer uses `CM90` or the coded `direction`; MC-11's no longer uses `AD90` or the `attention_only` class; MC-12 cites codebook §10.2 for derived units; MC-13 no longer says "stays candidate". Presence tests, preconditions, required assets, sequences and all literature statements are unchanged; ids MC-01 to MC-13 are kept. Written before any coding or outcome data.
- 2026-09-26 — 0.4.0 (M21 verifier fix; ADR-070.4, PRD R3.3 as amended; codebook 0.4.0, outcome model v2.1): MC-12 renamed "Star inflation suspected from aggregate anomalies (fake-star proxy)". Its presence test now reads the star anomaly flag (`manipulation_flag = star_anomaly_flagged`, from `anomaly-v0`, OM §4.3) instead of the StarScout campaign flag, which can't be computed without stargazer identities; `absent` needs every spike in the window checked and the ratio judged (or the case below 200 stars) [design]. The sequence starts at an anomaly-flagged spike; the contrast reading gains a proxy caveat; the sensitivity flag is `sensitive_to_star_anomaly`. Literature statements and citations [17][20][22][26] are unchanged and now say they describe account-level detection. No other entry changed. Written before any coding or outcome data.
