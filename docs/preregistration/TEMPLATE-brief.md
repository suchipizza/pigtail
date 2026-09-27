# Pre-registration template: one brief version (copy, fill in, commit, push)

> **This file is a template, not a pre-registration.** Copy it to
> `docs/preregistration/YYYY-MM-DD-brief-<short-slug>.md` (the slug must not reveal the brief:
> `brief-1`, `brief-2` is fine), fill it in, commit it, **push it**, then record it:
>
> ```
> pigtail brief preregister <brief-id> --print-hashes          # values for §1
> git add docs/preregistration/<file> && git commit && git push
> pigtail brief preregister <brief-id> --file docs/preregistration/<file> --commit <sha>
> ```
>
> The selection stage (`pigtail run --brief <id>`, the outcome sort) refuses to run until the
> brief version has a recorded pre-registration (PRD R8.2, ADR-065, outcome-model §5.8; exit
> code 7). Delete this quote block in your copy.

**Requirements:** PRD R8.2 (per-brief pre-registration), R4.3, R4.8, R4.9, R18.8; outcome-model
v2.1 §5.8; codebook §7.2 (writing a hypothesis); pre-registration README rules 1–7.
**Written:** YYYY-MM-DD · **Author role:** `analyst` or the brief's user
**Code commit when written:** `<short sha>` (`git rev-parse --short HEAD`)
**Outcome data seen when written:** none. The brief version's outcome sort has not run: no
percentile, winner, loser or balance statistic of this brief version exists. (If anything was
seen, say exactly what; the analysis is then exploratory, README rule 3.)
**Timestamp:** the commit that adds this file, as pushed to `origin` (README rule 2).

## Rule for this file: no brief content (R8.2, README rule 5)

This file is public. It must not contain the brief's text: no description, no field or
problem statement, no target users, no keywords, no reference cases or distribution exemplars
(names or URLs), no competitor names. Where the analysis depends on a brief value, this file
commits its **SHA-256** (§1) and the private brief file holds the value. `pigtail brief
preregister` refuses a file that quotes a free-text value of the brief.

## 1. What this pre-registration is bound to

Paste the output of `pigtail brief preregister <brief-id> --print-hashes`:

| Item | Value |
|---|---|
| `brief_id` (private id, not content) | `<brief-id>` |
| `brief_version` | `<n>` |
| `brief_sha256` (the whole brief version's content hash) | `<64 hex>` |
| `success_definition_sha256` (primary dimension, percentile thresholds and minimums, metrics, business minimum, weights, `if_not_applicable`, the no-measurable-adoption rule) | `<64 hex>` |
| `selection_params_sha256` (N winners and losers, exact-match keys, SMD target, headline exclusion SMD, calipers, matching rule, widening steps allowed, fallback steps, exemplar matching, sensitivity alternatives, selection / outcome-model / analysis-params versions; since `selection-v5` also views A, B and C, the follow-through metric, the title-confirmation rule with the Haiku prompt's fingerprint and the hash of the guarded anchor-rule code, ADR-083; since `selection-v6` also view B's launch-event anchor rule and its limitations, the undeclared-launch sub-population, the language groups and the pattern rule, the numeric-only headline rule, the SD rule, the distribution-surface coding with its prompt fingerprint, and the resolved Haiku model id, ADR-084; since `selection-v7` also view B's launch sources: whether Product Hunt and Bluesky apply, the Product Hunt topics, slug rule, product slot and confirmation rule with its Haiku prompt fingerprint, the Bluesky declared-account sources, search calls and incomplete rule, ADR-085) | `<64 hex>` |
| `selection_version` | `selection-v8` |
| `outcome_model_version` | `2.1` |
| `anchor_rule_version` / `anchor_rule_source_sha256` | `anchor-v7` / `<64 hex>` |
| `follow_through_metric_version` / `title_confirmation_version` | `follow-through-v1` / `confirm-v2` |
| `distribution_surface` version / prompt fingerprint / Haiku model | `surface-v1` / `<12 hex>` / `<model id>` |
| View B's launch sources: Product Hunt / Bluesky (applies: yes or no), Product Hunt topics, `ph_match_check` prompt fingerprint | `yes` / `yes` / `open-source, developer-tools` / `<12 hex>` |
| Codebook version | `<x.y.z>` |

How the two partial hashes are computed (anyone holding the private brief can re-check them):
`success_definition_sha256 = sha256(canonical_json(Definition.from_brief(brief).to_dict()))` and
`selection_params_sha256 = sha256(canonical_json(Context.from_brief(brief).params()))`, where
`canonical_json` is JSON with sorted keys, no spaces and UTF-8 (`pigtail.briefs.model.sha256_json`;
`pigtail.briefs.preregistration.hashes`). Neither contains brief text.

## 1b. The two headline views and the context view (selection-v5, ADR-083)

The brief's success definition is unchanged (`success_definition_sha256`). Since
`selection-v5` the selection reads the brief's **attention** dimension in two ways and reports
both (selection-version semantics, not a brief change):

| View | Attention means | Population | Winners and losers are paired on |
|---|---|---|---|
| **A, follow-through** (headline 1) | stars on endpoint days 3..29 after the anchor relative to launch size: the residual of `ln(1 + stars d3..29)` on `ln(1 + stars d0..2)` (OLS on the brief's anchored cases); sensitivity: the log-ratio and plain `att.stars@30` | every anchored field and reference case | launch size (LSM caliper 0.5 SD), audience bucket and half-year (exact), quarter (±1), repo age and language (distance) |
| **B, launch** (headline 2) | launch size: stars on endpoint days 0..2 after view B's own anchor (§1c); `att.hn_points` (and Reddit reach, `unknown`: no connector) reported next to it | cases whose view-B anchor is a declared launch event (§1c); repos anchored on an undeclared launch form the separate sub-population `launch_undeclared` | only what existed before launch: audience bucket and half-year (exact), quarter (±1), repo age and stars before launch (calipers 0.5 SD), language and core/adjacent field (distance). Launch size is **not** a key |
| **C, context** | per view, winners against every shortlisted non-winner, no matching | as A or B | nothing: labelled "context, not a headline" |

The brief's threshold (for example top quartile), its fallback steps (for example top third),
minimums, N and the headline exclusion (ADR-054.1) apply to each headline view on that view's
covariates. A hypothesis is written for one view (§2).

## 1c. What `selection-v6` adds (ADR-084; owner decisions of 2026-09-27 after verifier round 6)

These rules are committed in `selection_params_sha256`; restate them here so a reader of this
file knows what was fixed before the outcome sort.

- **View B's anchor** (view A keeps its outcome-model §2.2 anchor). The earliest
  maintainer-initiated launch event in the brief's window, from launch events only, never from
  star data: a Show HN or Launch HN post (discovery or the launch lookup; URL-matched, or a
  confirmed title match) or a GitHub release whose name or first 300 body characters match
  `\b(?:launch|launching|introducing|announcing|first\s+public\s+release)\b`
  (case-insensitive). Ties: show_hn, launch_hn, release_launch, then item id or tag. Every
  later launch event is a **relaunch event**, reported descriptively. Without a launch event:
  the earlier of the **first external mention** (the earliest HN item of any type linking the
  repo's GitHub URL) and the **first public release** (earliest release, prereleases
  included), flagged `undeclared_launch`: these repos form their own view-B sub-population
  (`launch_undeclared`, own winners, losers and balance), never part of the declared-launch
  headline. Bursts never anchor view B. The report shows the count per anchor rule
  (`show_hn`, `launch_hn`, `release_launch`, `undeclared:first_mention`,
  `undeclared:first_release`, `none`).
- **Limitations of view B's anchor (state them in the report):** as amended by §1d.
  **Known bias:** a case whose real first launch was on an unobserved channel is dated by its
  first observable event.
- **Headline exclusion:** only numeric standardized differences > 0.5 exclude a pair (LSM and
  repo age in A; repo age and stars before launch in B). Language, language group, category
  and the distribution surface stay in the balance table (`balance_limited` when they miss the
  target) and never exclude.
- **Language groups** (fixed): `js_ts` (JavaScript, TypeScript), `python`, `go`, `rust`,
  `other` (any other GitHub primary language, or none). Sensitivity per headline view:
  "same-language-group pairs only". For every headline pattern the report states whether it
  **holds** (same sign, at least half the contrast), **weakens** (same sign, less than half,
  or zero) or **reverses** (opposite sign: labelled "language-dependent") on that subset.
- **Distribution surface**, coded by Haiku before any outcome from public project-level text
  (the selection's first step): `surface` (`mcp_server`, `cli`, `library`,
  `editor_or_agent_plugin`, `hosted_app`, `other`, `unknown`) and `install_paths` (`npx`,
  `pip`, `brew`, `binary`, `marketplace`, `other`, `unknown`); a balance row next to language
  (SMD per level); never a matching key or an exclusion.
- **Calipers:** 0.5 SD on the log10 scale of 1 + stars before launch, repo age in days and
  1 + launch size, each **SD computed over the brief's full shortlisted pool** (every field
  and reference repo with an observed value) at sort time. The rule is fixed here; the values
  need the star data (outcome data), so they are computed when the selection runs and stored
  with it, never written in this file.
- **View C** ("all losers"): every shortlisted non-winner of the brief, no-anchor and
  undetermined repos included; statistics over those with a value, with the counts with and
  without one.

## 1d. What `selection-v7` adds (ADR-085; owner decisions of 2026-09-27 on Product Hunt and Bluesky)

Committed in `selection_params_sha256` (`launch_sources`); restate what applies to this brief.

- **Product Hunt launches** (if it applies; on unless the instance set
  `PIGTAIL_SELECTION_PRODUCT_HUNT=false` before this pre-registration): official API with the
  operator's developer token, project-level post fields only. A post counts as the repo's
  launch when it is found by slug (at most 2 slug candidates: the GitHub name lowercased with
  `_` and `.` turned into `-`, then the same without hyphens) or by the scan of the topics
  listed above inside the window; its name normalizes (casefold, only a–z and 0–9 kept) to the
  repo's GitHub name; and it is confirmed by the first of: the repo's GitHub URL in its tagline
  or description, the repo's homepage domain there, two distinctive keywords shared with the
  repo description, the Haiku check (`ph_match_check`, fail closed). A repo name under 5
  characters or made of stop-list words is confirmed by the URL or domain rule only. Dated
  `featuredAt`, else `createdAt`. Votes and comments are reported as secondary launch-size
  measures (`att.ph_votes`, `att.ph_comments`), never ranked on.
- **Bluesky posts by declared maintainer accounts** (if it applies): only accounts the
  maintainer declared by a `bsky.app/profile/…` link or `@name.bsky.social` on the repo's
  homepage field, README, org page (`blog`, `description`) or GitHub profile social accounts
  (at most 3 per repo), matched in memory; the search asks for that account's posts linking the
  repo's GitHub URL or homepage, inside the window. Only kind, time, role `maintainer` and match
  are stored; never the handle.
- **Tie-break at the same instant:** `show_hn`, `launch_hn`, `product_hunt`, `release_launch`,
  `bluesky_maintainer_post`, then the event's ref. The earliest event anchors; later ones are
  relaunch events. The report's counts per anchor rule include the two new kinds.
- **Incomplete sources:** a repo whose Bluesky data (or Product Hunt data) could not be read
  completely has no view-B anchor (`launch_source_incomplete:<source>`) and is counted; more
  than 10 % of the shortlisted repos incomplete refuses the selection.
- **Limitations of view B's anchor (state them in the report):** X (paid API; USD 0 cap for
  other paid services), Reddit (no API approval), blogs (no source), Bluesky posts by accounts
  the maintainer did not declare (finding them would be cross-platform name matching, forbidden
  by ADR-075.3), Product Hunt launches under another name or that no rule confirms; a launch
  source turned off for this brief.

## 2. Hypotheses (codebook §7.2)

List the pattern hypotheses this brief checks, before the outcome sort, **per view** (A or B;
a hypothesis for both is listed twice). Use the seed ids (`MC-01` … `MC-13`,
`docs/methodology/mechanisms/candidates.md`) where a seed fits; write brief-specific ones in
the same form without brief content (describe the mechanism, not the project).

| id | view | seed | Pattern (present / absent, coded field) | Expected direction among winners vs matched losers | Coded fields it rests on |
|---|---|---|---|---|---|
| H1 | B | MC-xx | … | … | … |
| H2 | A | MC-xx | … | … | … |

**View B (what makes a launch large)** asks about the launch itself, so its hypotheses map to
seeds MC-01 to MC-11:

| Theme | Seeds |
|---|---|
| channel | MC-01 (first-party HN post: its preconditions and reception, since every view-B case has a declared launch), MC-02 (front page), MC-04 (social posts), MC-07 (multi-channel), MC-08 (cross-community breadth) |
| timing | MC-03 (posting window), MC-05 (launch week), MC-10 (release-driven) |
| title / message | MC-09 (novelty positioning) |
| assets | MC-11 ("try it now" asset) |
| maintainer posting | MC-06 (disclosed paid promotion alongside the launch), MC-07 (active first-party promotion) |

**View A (what follows a launch of a given size)** asks what differs after launches of similar
size: post-launch activity (releases, MC-10), community channels, sustained multi-channel
promotion (MC-07, MC-08). In view A, H1-type contrasts on the presence of a launch post
(MC-01) are restricted to launch-anchored pairs (ADR-081.6).

## 3. Tests and decision rules

- **Contrast:** per hypothesis, n present among winners vs among matched losers **of the
  hypothesis's view**, with the counterexamples (PRD §5.2, F20), per outcome dimension.
  Headline patterns use that view's headline pairs only (ADR-054.1, ADR-078); every other pair
  is shown in the case-level view. View C (winners against every shortlisted non-winner) is
  context, never a headline; so is view B's undeclared-launch sub-population.
- **Minimum evidence:** "insufficient evidence in this neighbourhood" below 10 known cases per
  side or 3 cases where the pattern is present (ADR-050.3).
- **Reliability:** per-field Krippendorff's α; findings resting on a field with α < 0.70 are
  labelled "low reliability" (ADR-065).
- **Balance:** SMD per covariate after matching, target |SMD| < 0.25; contrasts depending on a
  covariate that misses it are labelled `balance_limited` (outcome-model §5.6). The balance
  table includes language, language group, category, distribution surface and install path
  (SMD per level); only numeric differences exclude a pair from the headline (§1c, ADR-084).
  No re-matching.
- **Sensitivity:** the alternatives fixed in the brief (committed in `selection_params_sha256`)
  are reported; a pattern that holds only under the baseline definition is labelled
  definition-sensitive (outcome-model §8). Each headline pattern is also checked on the
  same-language-group pairs only: holds, weakens or reverses; a reversing pattern is labelled
  "language-dependent" (§1c).
- Anything else, or anything changed after the outcome sort, is **exploratory** (README rule 3).

## 4. Deviations

None at the time of writing. Later changes go into a dated amendment (README rule 3), never
into this file.
