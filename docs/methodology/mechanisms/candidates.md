# Candidate mechanism cards (seed set v0)

**Task:** M4-T1b · **Written:** 2026-09-25 · **Author role:** `analyst`, working with researcher discipline
**Requirements:** PRD F9 (R9.1–R9.3), §9.3; codebook `0.1.0` §7 (card schema) and §10 (C11a/C11b).
**Machine-readable copy:** `schemas/mechanisms/candidates-v0.json`. If this file and the JSON disagree, that is a bug. Until it is fixed, the JSON wins for coding tools and this file wins for meaning (the same rule as the codebook).
**Data version:** none. No case has been coded against these cards. **Code commit when written:** `4f55e73`.
**Citations:** `[n]` means reference n in `docs/research/literature.md` §8. Every number quoted here is copied from that review, and the review is the source. Nothing here adds a number the review doesn't state.

---

## 0. What these cards are, and what they aren't

- **They are hypotheses, not findings.** All 13 cards are `status: candidate`, `confidence: low` (codebook §7.2). None has a supporting case, a contradicting case, a loser contrast or an effect estimate. Those fields say **"unknown — not yet tested"**.
- **The sources are practitioner writing and observational research.** Practitioner sources ([63][64][66][67]) are anecdotal and survivor-biased: they have no control group (LR §5). The academic sources are mostly observational without matched controls ([8][39][40]). The one matched design is Fang et al. [11]. Every number below describes **what the source reported**. None of them is an effect estimate for the card, and none may be copied into `effect_estimate`.
- **Why they exist now.** The M4 pilot needs cards to code `mechanism_support.presence` (C11a) and `direction` (C11b) against (pilot pre-registration §4.5). Each card therefore has a **presence test** written in codebook fields.
- **Promotion is out of scope here.** A card leaves `candidate` only under PRD §9.3 in M6. Pilot cases are exploratory and never enter a card's `history` (pilot pre-registration §1).
- **Anti-pattern cards.** MC-12 and MC-13 describe behaviour that PRD §4 rules out (fake stars, vote solicitation). They exist so that manipulation can be **detected and contrasted**. They can never be recommended: F10 and R12.4 exclude them whatever their status.
- **How preconditions are labelled.** Each precondition carries its origin:
  - `[LR n]`: the literature review states it;
  - `[design]`: a v0 choice made here so the card can be tested, **not** taken from the literature. The pilot may revise these.

## 1. Fields that are the same on every card in v0

These values apply to **each** card below. The JSON repeats them on every card, as codebook §7.1 requires.

| Field | Value on every v0 card |
|---|---|
| `version` | `0.1.0` |
| `status` | `candidate` |
| `confidence` | `low` (codebook §7.2: every candidate) |
| `participation_motive` | unknown — no `stated_reason` codes exist yet. It may only be summarised from item-level `stated_reason` codes (codebook §7.1) |
| `supporting_cases` | none (empty) |
| `contradicting_cases` | none (empty) |
| `loser_contrast` | unknown — not yet tested |
| `effect_estimate` | unknown — not yet tested |
| `applicable_strata` | insufficient evidence (no coded cases) |
| `saturation_trend` | `insufficient_evidence` |
| `deciding_field_alpha` | not yet measured (M4-T3 pilot, fields C3, C4, C9, C11a, C11b) |
| `sensitivity_flags` | none raised (nothing has been tested) |
| `unobservable_channels` | Reddit, X, YouTube, Product Hunt, Lobste.rs, dev.to, Juejin, Zhihu, Bilibili (GAP, ADR-010). **Also held under ADR-022 until its controls exist:** HN, Bluesky, V2EX, Discord. Loser contrasts must use the same channels on both sides (codebook §7.1). |
| `sequence_timing` offsets | unknown — the median and range need supporting cases. Each card gives only the hypothesised **order** of events. |
| `history` | `[{date: 2026-09-25, version: 0.1.0, change: "created; seeded from literature review", case_id: null, direction: null, codebook_version: 0.1.0}]` |

**Presence and direction.** These follow codebook §7.4:
- `present` requires every precondition test **and** at least one sequence element, each supported by evidence.
- `absent` requires positive evidence of absence in an observable channel.
- Anything else is `unknown`.
- A winner without the mechanism is `neutral`, never `contradicts`.

---

## 2. Cards

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
- **Loser contrast:**
  - *Supports* (§9.3 prevalence route): first-party HN launches are more prevalent among winners than among their matched losers within strata, with `unknown` shares on the two sides within 20 pp.
  - *Contradicts:* prevalence among matched losers is equal to or higher than among winners. That is the survivorship pattern H-L7 predicts for frequently recommended practices. [40]'s null result for the Show HN tag makes this plausible.
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
  - Front-page status can only be observed from pigtail's own rank polling, which starts with live capture, or from the Algolia `front_page` tag, whose semantics are undocumented (OM §1.2).
  - So retrospective cases without the tag are `unknown` (reason `coverage_gap`), never `absent`.
- **Outcome dimensions:** attention.
- **Modules:** none.
- **Loser contrast:**
  - *Supports:* the H-L2 event study (R8.1, robust estimator). Matched HN stories that stayed off the front page with similar early votes serve as controls, and the 90% CI of the 48 h star effect excludes 0.
  - *Contradicts:* that CI includes 0, or front-page prevalence doesn't differ between winners and matched losers.
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
- **Modules:** none. [40]'s sample is AI/LLM only, so any `ai_hype` stratum result is closest to the source.
- **Loser contrast:**
  - *Supports:* among cases whose earliest story is on HN, the in-window share is higher among winners than among matched losers. Sequence matters here: the contrast is conditional on both sides having an HN story.
  - *Contradicts:* the shares are equal, or the event-study effect of the posting window has a CI including 0.
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
  - Until Bluesky is cleared, this card is **`unknown` (reason `unobservable_channel` or `held`) for every case**, never `absent`.
- **Outcome dimensions:** attention and community, with community expected to be the smaller effect (H-L5).
- **Modules:** none.
- **Loser contrast:**
  - *Supports:* higher prevalence among winners than matched losers. In an event study, the star effect is positive and the contributor effect is smaller.
  - *Contradicts:* equal prevalence, or no star effect. H-L5 is falsified if the ratio of star effect to contributor effect is not greater than 2.
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
  2. There is an organisation behind the project [design; [63] is a company]. *Test:* `corporate_backed` or `b2b_oss_saas` module active. This is a scope assumption and is tested as a stratum, not assumed to be necessary.
- **Required assets:** `launch_post` (≥ 3), `title`, `link`.
- **Sequence:** `prep` → `launch` with `campaign = true` and sub-announcements on ≥ 3 distinct UTC days within 7 days [design threshold] → one or more bursts during the campaign. It may repeat as `relaunch` events at ≥ 30 days (codebook §3.6).
- **Presence test:** `present` if a `launch` or `relaunch` has `campaign = true`, with sub-items on ≥ 3 distinct UTC days within 7 days, and precondition 1 holds. When the `b2b_oss_saas` module is active, its `launch_week` field must agree.
- **Outcome dimensions:** attention; adoption (hypothesised, from [63]'s self-reported growth claim).
- **Modules:** `b2b_oss_saas`, `corporate_backed`.
- **Loser contrast:**
  - *Supports:* higher prevalence among winners than among matched losers **within** the org-backed strata.
  - *Contradicts:* matched losers ran launch weeks at a similar rate. This is H-L7's survivorship prediction for [63].
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
  - So this card is expected to be `unknown` for almost every case. That is recorded, and not read as absence.
- **Outcome dimensions:** attention.
- **Modules:** none.
- **Loser contrast:**
  - *Supports:* higher prevalence among winners than matched losers, with MC-01 held equal.
  - *Contradicts:* equal prevalence.
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
- **Loser contrast:**
  - *Supports:* winners use more distinct first-party venues than matched losers. The count is ordinal and is compared within strata.
  - *Contradicts:* no difference.
- **Caveats:** user meetings [7] can't be observed at all. The survey attribution [3] comes from winners only (survivorship).

### MC-08 — Early cross-community breadth
- **Description:** within days of the first burst, several distinct communities or publications pick up the repo independently. This is breadth, rather than one large broadcast.
- **What the literature says:**
  - On Facebook photo reshares, early **breadth** predicted large cascade size better than depth [31].
  - Structural virality ranges from broadcast to viral spread, and popular items grow through every mix of the two [29].
  - Both sources are about social platforms other than GitHub.
- **Preconditions:** none known from the literature. As a mechanism, it would only be actionable if a precondition could be found. Until then it is a **spread pattern** used for forecasting (H-L4).
- **Required assets:** unknown.
- **Sequence:** `burst` onset → edges of type `published` or `cited`, with `supported` status (codebook §4.4), into ≥ 3 distinct `community` or `publication` nodes within [onset − 48 h, onset + 72 h] [design thresholds].
- **Presence test:** `present` if the sequence condition holds. `absent` if the observable venues are covered and fewer than 3 nodes appear. `unknown` otherwise.
- **Outcome dimensions:** attention. Carry-over to adoption and community is unknown.
- **Modules:** none.
- **Loser contrast:**
  - *Supports:* breadth is more prevalent among winners than matched losers with a similar launch-signal magnitude.
  - *Contradicts:* no difference.
  - H-L4 is also tested as a forecasting feature: forecasting pre-registration, feature F15, §5.3.
- **Caveat:** this card risks being tautological, because breadth is partly the outcome itself. It can't be promoted without a testable precondition (PRD §9.3, fourth condition).

### MC-09 — Novelty positioning
- **Description:** the project presents itself as novel, for example new in kind or a new combination. That positioning draws attention, but also comes with lower long-run participation.
- **What the literature says:** in the Python ecosystem, more novel projects get more stars. The same projects have smaller teams and a higher long-run risk of abandonment [13].
- **Preconditions:**
  1. There is a first-party novelty claim [design]. *Test:* the `ai_hype` extra field `novelty_claim` has a quoted span.
  - Outside `ai_hype`, codebook 0.1.0 has **no field** for novelty claims, so presence there is `unknown` (codebook gap; see open issues).
  - The review doesn't describe how [13] measured novelty. Whether a quoted positioning claim is the same construct is **unknown**.
- **Required assets:** `tagline` or `title` carrying the claim.
- **Sequence:** the claim is present at T → `burst`.
- **Presence test:** `present` if precondition 1 holds. `absent` if the `ai_hype` module is active, the README and tagline at T were captured, and they contain no novelty claim. `unknown` otherwise.
- **Outcome dimensions:** attention (expected +), community (expected −, T+90 returning external contributors).
- **Modules:** `ai_hype` (in v0; the codebook gap limits it to this module).
- **Loser contrast:**
  - *Supports:* among attention winners, novelty-claim cases have lower `CM90` than matched non-novel winners (H-L6), and novelty is more prevalent among attention winners than among matched losers.
  - *Contradicts:* the community difference is null (H-L6 falsified).
- **`direction` on this card:** a case counts as `supports` only when both dimensions move as predicted.

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
- **Loser contrast:**
  - *Supports:* release-triggered bursts are more prevalent among winners, **or** an R8.1 event study around release dates (named explicitly in R8.1) gives a 90% CI excluding 0.
  - *Contradicts:* equal prevalence and a null event-study effect.

### MC-11 — "Try it now" asset at launch
- **Description:** at launch, the project offers a zero-friction way to try it: a hosted demo or a one-command install. Attention then turns into adoption.
- **What the literature says:** only the Show HN rule that a submission must be something people can try [65]. That is a platform rule, **not** evidence of any effect. This card rests on the thinnest basis in the set.
- **Preconditions:**
  1. A launch exists [design]. *Test:* a `launch` event (any venue).
- **Required assets:** `live_demo` or `install_one_liner`.
- **Sequence:** the asset's `first_seen` is ≤ launch → `launch` → `burst`.
- **Presence test:** `present` if a `launch` exists and a `live_demo` or `install_one_liner` asset has `first_seen` ≤ the launch timestamp. `absent` if the README and site at launch were captured and neither asset appears. `unknown` otherwise.
- **Outcome dimensions:** attention, adoption (hypothesised).
- **Modules:** none. The `cli_devtools` module records `install_channels`.
- **Loser contrast:**
  - *Supports:* higher prevalence among winners than matched losers, and among winners a higher `AD90` than matched attention-only cases.
  - *Contradicts:* no prevalence difference.
- **Overlap:** MC-01's precondition 1 is this card's asset condition. They are coded separately, so MC-11 can be tested across all launch venues.

### MC-12 — Fake-star campaign (anti-pattern: detection and contrast only)
- **Description:** purchased or coordinated fake stars inflate star counts. PRD §4 rules this out. The card exists so that the effect can be measured and cases flagged. **It is never recommended** (F10, R12.4).
- **What the literature says:**
  - StarScout found 18,617 repos with fake-star campaigns (3.81 M fake stars after post-processing). Activity surged in 2024.
  - Most fake stars promote short-lived phishing or malware repos. The rest go mostly to AI/LLM, blockchain, tool and tutorial repos [17][20].
  - Promotion effect: a 1% rise in fake stars in month t goes with +0.07% real stars in t+1 and +0.03% in t+2. Cumulative fake stars have a negative coefficient from about t+2 onward, which the authors read as "a liability in the long term" [20].
  - Ground truth for the Stargazers Ghost Network: [22]. An industry experiment in which the authors bought stars themselves: [26].
- **Preconditions:** none. The card is detected, not chosen.
- **Required assets:** none.
- **Sequence:** a StarScout campaign month → `burst` (codebook §3.2: still a burst) → decay.
- **Presence test:** **derived, not coded.** `present` if `manipulation_flag = fake_star_campaign_suspected` (the StarScout campaign flag, OM §4). `absent` if StarScout ran for the window without a flag. `unknown` if it didn't run.
  - Because the value comes from the pipeline, its C11a units are excluded from α (pilot pre-registration §4.5).
  - The flag is a suspicion, never proof, and no repo is named publicly (LR §2.3).
- **Outcome dimensions:** attention (short-lived +), adoption (expected −, H-L8).
- **Modules:** none. Category concentration (H-L1) is tested per stratum.
- **Loser contrast:**
  - *Supports H-L8:* flagged repos show a real-star uplift lasting < 2 months and lower T+365 adoption than matched unflagged repos.
  - *Contradicts:* the adoption difference is null or positive.
- **Sensitivity flag to set when tested:** `sensitive_to_fake_star_filter` (ADR-020).

### MC-13 — Vote solicitation on HN (anti-pattern: detection and contrast only)
- **Description:** the maker asks people to upvote or comment on an HN launch. HN's guidelines forbid this. **It is never recommended.**
- **What the literature says:** only the Show HN guideline "Please don't ask friends to upvote or comment" [65]. There is no evidence of its effect. The codebook records it as a manipulation flag (codebook §2.4).
- **Preconditions:** none (detected).
- **Required assets:** none.
- **Sequence:** an item with `manipulation_flag = vote_solicitation` linked to an HN story → trigger `hn_post` or `hn_front_page` → `burst`.
- **Presence test:** `present` if any captured item carries `vote_solicitation` and references an HN story about the repo. `absent` is never coded, because solicitation happens mostly in unobservable channels (DMs, X, chat). So the value is `present` or `unknown`.
- **Outcome dimensions:** attention.
- **Modules:** none.
- **Loser contrast:** prevalence can't be compared validly, because `absent` is never observable and the §9.3 prevalence route excludes `unknown`.
  - The card is **expected to stay `candidate` permanently**, and is used only to flag cases and exclude them from MC-01 and MC-02 estimates in sensitivity analyses.
  - *Contradicts* has no defined meaning for this card.

---

## 3. Open issues
1. **Codebook gap (MC-09).** Novelty claims can only be coded inside `ai_hype`. A general `novelty_claim` field would be a minor codebook version (codebook §13), which the pilot's revision round may propose.
2. **Held and GAP channels.** MC-01, MC-02, MC-03 and MC-13 depend on HN, and MC-04 and MC-06 on social platforms (held or GAP). Until ADR-022's controls exist, those cards are `unknown` on almost every case. The pilot's C11a units for them are then pipeline-forced and excluded from α (pilot pre-registration §4.5).
3. **`[design]` thresholds need calibration.** Examples: ≥ 3 days (MC-05), ≥ 2 venues (MC-07), ≥ 3 nodes within 72 h (MC-08), ±7 days (MC-06). They are v0 choices, calibrated in the pilot's revision round. After v1.0.0 they change only under the ADR-019 policy.
4. **Overlapping cards.** MC-01/MC-02/MC-03 and MC-01/MC-11 overlap. Promotion analyses must report them jointly, so that the same cases aren't counted as independent support.
