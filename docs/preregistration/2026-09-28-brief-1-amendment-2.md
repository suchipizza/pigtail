# Pre-registration amendment 2: brief 1, version 6

**Amends:** `docs/preregistration/2026-09-26-brief-1.md` (frozen at `e4b5f00`) and its amendment 1
(`2026-09-26-brief-1-amendment-1.md`, frozen at `7d8855f`). Both stay unchanged; this
amendment supersedes amendment 1's binding (§1) and its hypotheses (§2).
**Requirements:** PRD R8.2, R4.3, R4.8, R4.9; README rules 2–5; outcome-model v2.1 §2, §5, §5.9;
ADR-083, ADR-084, ADR-085 (addenda 1–6).
**Written:** 2026-09-28 · **Author role:** `analyst` (orchestrator agent, for the owner)
**Code commit when written:** `1020bb3` (M22 accepted by the verifier, round 9)
**Outcome data seen when written:** none for brief 1. Its outcome sort has not run: no star
history, anchor, launch size, percentile, winner, loser or balance statistic of brief 1 (any
version) exists. The instance holds the shared Product Hunt topic listing (post ids, dates,
hashed names; not specific to any brief) and brief 1's final shortlist (686 repos, carried from
v5 to v6). The M22 acceptance runs of the synthetic example brief (a different brief, on a
throwaway instance) were seen; the owner's decisions below were taken on method grounds and
on those runs, never on brief 1's data.
**Timestamp:** the commit that adds this file, as pushed to `origin` (README rule 2).

This file holds no brief content (README rule 5). The brief id is committed as a SHA-256.

## 1. Binding

| Item | Value |
|---|---|
| `brief_id_sha256` | `1d7308991ef2e88eed3b659358c21cf1db64234809ba98f4d13aa3cea16626bb` |
| `brief_version` | `6` |
| `brief_sha256` | `2b8b601bb0847d5b0a851a885c34a6f8b66839b2832b4a20b7f10b589cae07e1` (unchanged) |
| `success_definition_sha256` | `167559d8b565b017054a9c83a6a117ee45e21ed54a8fae09dd35fdbf1c771110` (unchanged) |
| `selection_params_sha256` | `4235c9858852541d712309721be917803ea68046451d8d51520f0442437f8830` |
| `selection_version` | `selection-v12` |
| `outcome_model_version` | `2.1` |
| `anchor_rule_version` / `anchor_rule_source_sha256` | `anchor-v11` / `9897a5624677943c64e81705d9c8a02967ac1b0e6617aeab8e650457db0cae28` |
| `follow_through_metric_version` / `title_confirmation_version` | `follow-through-v1` / `confirm-v2` |
| `distribution_surface` version / prompt fingerprint / Haiku model | `surface-v1` / `e71bbf19e31d` / `claude-haiku-4-5-20251001` |
| Title-match check prompt fingerprint | `5f632bba2768` |
| View B's launch sources: Product Hunt / Bluesky / PH topics / `ph_match_check` fingerprint | `yes` / `yes` / `open-source, developer-tools` / `3cef7b51cd29` |
| Codebook version | `0.4.0` |

The hashes are computed as in `docs/preregistration/TEMPLATE-brief.md` §1. Every rule below is
inside `selection_params_sha256`; this section restates them for the reader.

## 1b. What is fixed before the sort (owner decisions of 2026-09-27 and 2026-09-28)

**Two headline views and a context view** (ADR-083; the brief's attention dimension read two
ways, the brief itself unchanged):

- **View A, follow-through (headline 1).** Outcome: the residual of `ln(1 + stars on endpoint
  days 3..29)` on `ln(1 + stars on days 0..2)`, OLS on the view's anchored cases (primary);
  the log-ratio and plain `att.stars@30` are sensitivity checks. Anchor: outcome-model §2.2.
  Pairs: launch size (LSM, days 0..2, caliper 0.5 SD), audience bucket and half-year (exact),
  quarter (±1), repo age and language (distance). The brief's threshold and its top-third
  fallback apply.
- **View B, launch (headline 2).** Outcome: launch size (stars on days 0..2 from view B's own
  anchor); Hacker News points and Product Hunt votes/comments (US Pacific endpoint days 0–2
  only) are secondary, never ranked on; Reddit reach is `unknown` (no connector). Population:
  repos whose view-B anchor is a declared launch. Pairs only on what existed before launch:
  audience bucket and half-year (exact), quarter (±1), repo age and stars before launch
  (calipers 0.5 SD), language and core/adjacent field (distance). Launch size is not a key.
- **View B, undeclared launches** (anchored on the first external mention or the first public
  release, whichever is first): reported separately, never a headline.
- **View C, context:** per view, winners against every shortlisted non-winner of the brief,
  unmatched; context, never a headline.

**View B's anchor** (ADR-084, ADR-085): the earliest maintainer-initiated launch event in the
window, from launch events only, never from star data — a Show HN or Launch HN post; a
confirmed Product Hunt launch (featured time, else creation time); a GitHub release whose name
or first 300 characters use launch wording (launch, launching, introducing, announcing, first
public release); a post with launch wording by a Bluesky account the maintainer declared.
Ties: Show HN, Launch HN, Product Hunt, release, Bluesky. Later launch events are relaunches,
reported descriptively. A repo whose first declared launch is before the window has no view-B
anchor. Counts per anchor rule are reported.

**Bluesky** (owner rule, 2026-09-27): an account counts as the maintainer's only if declared
by the GitHub profile's social accounts, the org page or the repo homepage field, or by the
README when it names exactly one account (a handle and its own DID count as one, the handle
resolved in memory). Only posts that link the repo (or its homepage) are searched; the handle,
DID and text stay in memory; only the role, time and match are stored. The owner accepted that
the person-level hold of ADR-022/073.2 does not apply to this search (nothing person-level is
stored; raw answers are dropped).

**Name-only launch matches** (Hacker News titles, Product Hunt names) count only when
confirmed: the post uses the repo's homepage domain, names the owner (not when the owner's
login is part of the repo name), shares at least two description keywords, or a Haiku check
says it is the same project; otherwise excluded and counted.

**Headline rule** (owner option 1): only numeric standardized differences above 0.5 exclude a
pair; language, language group, category, distribution surface and install path are balance
rows, labelled `balance_limited` when they miss the target.

**Language groups**, fixed now: `js_ts` (JavaScript, TypeScript), `python`, `go`, `rust`,
`other` (anything else or none). Each headline pattern is re-checked on same-group pairs only:
holds, weakens or reverses; a reversing pattern is labelled "language-dependent".

**Distribution surface**, coded by Haiku from public project text before any outcome data:
MCP server / CLI / library / editor or agent plugin / hosted app / other / unknown, plus install
paths (npx, pip, brew, binary, marketplace, other, unknown). A balance row only.

**Calipers:** 0.5 SD on log10 scale. The rule is fixed here; the SD values are computed at sort
time over the shortlisted repos that have a value relative to the view's own anchor (they need
star data, which is outcome data), and are stored with the selection.

## 2. Hypotheses (codebook §7.2; supersede amendment 1 §3 and the original §2)

Seeds from `docs/methodology/mechanisms/candidates.md` (0.4.0), taken as written. "Presence" is
coded per case by the extraction stage; each hypothesis is tested only in its view.

| id | view | seed | Pattern | Expected among winners vs matched losers | Rests on |
|---|---|---|---|---|---|
| B1 | B | MC-01 | first-party Show HN / Launch HN as the launch event (vs another declared launch kind) | more common | anchor kind; codebook launch fields |
| B2 | B | MC-02 | HN front-page exposure at launch | more common (labelled "close to the outcome") | MC-02 fields |
| B3 | B | MC-03 | HN post in the 12–17 UTC window | more common | MC-03 fields |
| B4 | B | MC-04 | social posts linking the repo around launch | more common | MC-04 fields |
| B5 | B | MC-05 | launch week (multi-day campaign) | more common | MC-05 fields |
| B6 | B | MC-06 | disclosed paid promotion alongside the launch | more common (expected mostly `unknown`) | MC-06 fields |
| B7 | B | MC-07 | active multi-channel first-party promotion | more common | MC-07 fields |
| B8 | B | MC-08 | early cross-community breadth | more common | MC-08 fields |
| B9 | B | MC-09 | novelty positioning in the launch title/message | more common | MC-09 fields |
| B10 | B | MC-10 | launch tied to a release | more common | MC-10 fields |
| B11 | B | MC-11 | "try it now" asset at launch | more common | MC-11 fields |
| B12 | B | MC-12 | star inflation suspected (anomaly checks) | detection and contrast only | MC-12 fields |
| B13 | B | MC-13 | vote solicitation on HN | detection and contrast only | MC-13 fields |
| A1 | A | MC-10 | releases in the follow-through window | more common | MC-10 fields |
| A2 | A | MC-07 | sustained multi-channel first-party promotion after launch | more common | MC-07 fields |
| A3 | A | MC-08 | cross-community breadth after launch | more common | MC-08 fields |
| A4 | A | MC-04 | social posts linking the repo after launch | more common | MC-04 fields |
| A5 | A | MC-11 | "try it now" asset | more common | MC-11 fields |
| A6 | A | MC-09 | novelty positioning | more common | MC-09 fields |
| A7 | A | MC-01 | launch post presence | reported within launch-anchored pairs only; presence "not testable under this anchor rule" (amendment 1) | anchor kind |
| A8 | A | MC-12 | star inflation suspected | detection and contrast only | MC-12 fields |
| A9 | A | MC-13 | vote solicitation on HN | detection and contrast only | MC-13 fields |

Tests and decision rules: as in `TEMPLATE-brief.md` §3 (contrast per view on headline pairs,
minimum evidence, Krippendorff's α, balance, sensitivity including the same-language-group
check). Anything else, or anything changed after the sort, is exploratory.

## 3. Known limitations (stated before the sort)

- Launch events on X (paid API; the owner's cap for other paid services is USD 0), Reddit (no
  API approval), blogs (no source), and Bluesky accounts the maintainer did not declare are not
  observable; such a case is dated by its first observable event.
- The Product Hunt topic scan covers the window only; a pre-window Product Hunt launch whose
  name the slug candidates don't produce is missed (owner accepted, 2026-09-28).
- Product Hunt's site terms: the working reading (ids, dates and hashed names kept 30 days are
  not a "significant portion of the Content"; personal, non-commercial use) is the owner's,
  pending her email to Product Hunt (H5); if they object, the cache is deleted and Product
  Hunt turned off.
- Adoption and community have no connector yet (milestone M23b): they are reported where data
  exists and never select.
- The shortlist's relevance-filter precision is unmeasured (bulk decisions without item
  review); four named reference cases are unresolved.

## 4. Deviations

None beyond the above. Later changes go into a dated amendment, never into this file.
