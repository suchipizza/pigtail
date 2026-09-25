# Amendment 2 to the M4 pilot pre-registration

**Amends:** `docs/preregistration/2026-09-25-pilot.md`, frozen at commit `3f07692` (`3f0769289e1842d24c1d861e4b05db9fe57ca855`, 2026-09-25T16:10:40+02:00), as amended by `docs/preregistration/2026-09-25-pilot-amendment-1.md`, frozen at commit `2521652` (`25216526e7ee1cbe857f7d540edbd5f9f4f9c81e`, 2026-09-25T17:18:53+02:00). Neither file is edited (README rule 1).
**Task:** M4-T1d, M4-T2c (codebook 0.2.0), M4-T2b (link to the threshold-calibration pre-registration) · **Written:** 2026-09-25 · **Author role:** `analyst`
**Decided by:** ADR-032, ADR-035 (star series and class series); codebook §13 (semver, including the new "before 1.0.0" rule); ADR-019 (held-out split). The codebook version decision still needs an ADR; see "Open issues".
**Code commit when written:** `535ddd0` (HEAD on 2026-09-25).
**Outcome data seen when written:** **none.** No outcome observation, percentile, class, provisional label or base rate has been computed or looked at, under any thresholds version, for any case in either split. No case has been selected, unitized or coded. No burst or quiet event has been derived under any codebook version. No pilot attempt has started, and no prompt has been run on the development set. Nothing in this amendment was informed by data.
**Timestamp:** the commit that adds this file (README rule 2).

## Why

Amendment 1 left one item open ("Not changed", last bullet). Codebook 0.1.0 derived the `burst | quiet` layer, which defines the C9/C10 units, from "`starscout_filtered` from the stargazers API". That series no longer exists (ADR-032), and the filtered series is `unknown` for almost every pool case (ADR-035). The pilot could not have cut its burst units. Codebook **0.2.0** (`docs/methodology/codebook.md`, `schemas/codebook/v0.2.0.json`) fixes this. It also adds the case-level `novelty_claim` field that MC-09 needs (candidates.md open issue 1). This amendment pins 0.2.0 for the pilot. It also records how the pilot's selection classes relate to the new threshold-calibration pre-registration (`docs/preregistration/2026-09-25-threshold-calibration.md`).

**Why the version is 0.2.0 and not 0.1.1** (full reasoning in the codebook CHANGELOG):
- adding a field is a minor change (CB §13);
- moving a derived field to another input series is not wording-only, and before 1.0.0 it bumps the minor version (CB §13, "Before 1.0.0");
- both changes ship together so that the pilot doesn't pin two versions in a row with no coding between them.

**No coding had used 0.1.0.**

No gate, α threshold, minimum unit count, sample, sample size, seed, stratum rule, caliper, matching weight, blinding rule or revision rule changes.

## Changes

Section numbers refer to the original file. "Old" quotes the original (or amendment 1, where it replaced the original); "New" replaces it for all purposes.

### B1. Pinned codebook version (header, §1, §5.1, §5.2, §6.7, §7.2)
- Old (header): "codebook `0.1.0`: `docs/methodology/codebook.md` and `schemas/codebook/v0.1.0.json`;"
- New: "codebook `0.2.0`: `docs/methodology/codebook.md` and `schemas/codebook/v0.2.0.json`;". Selection addendum 1 (§4.6) records the SHA-256 of the 0.2.0 files. `schemas/codebook/v0.1.0.json` stays in the tree unchanged, as a record; it is not a pilot input.
- Old (§1 objective 2): "Revise the codebook from v0.1.0 and freeze the version that passes G1 as v1.0.0". New: "Revise the codebook from v0.2.0 and freeze …".
- Old (§5.1, passes A and B, "Sees"): "codebook 0.1.0 text + JSON". New: "codebook 0.2.0 text + JSON".
- Old (§5.2): "`codebook_version = 0.1.0`". New: "`codebook_version = 0.2.0`".
- Old (§6.7): "the 79 enum values listed under `g1_category_change_denominator` in `schemas/codebook/v0.1.0.json`". New: "… in `schemas/codebook/v0.2.0.json`". The count is still **79**, with the same nine enums and the same per-enum counts as the §6.7 table. The nine enums are identical between 0.1.0 and 0.2.0 (a JSON diff is empty).
- Old (§7.2): "**Attempt 1:** code P ∪ SE ∪ SC with v0.1.0" and "**no changes:** v0.1.0 is re-labelled v1.0.0." New: "with v0.2.0" and "v0.2.0 is re-labelled v1.0.0."

### B2. Burst units for C9 and C10 (§4.5; §2 item 4)
- Old (§4.5 table): "C9 `trigger_type`, C10 `trigger_confidence` | burst (derived, CB §3.2)".
- New: "… | burst (derived, CB 0.2.0 §3.2: `raw` star-history endpoint days for every case; onset hour from hourly watch-list snapshots only where they cover the 48-hour detection window, per OM §2.1; the burst holding the case's `T_burst` takes the recorded onset)". Trigger candidates follow CB 0.2.0 §6.1, including the proximal window for day-precision onsets.
- **A consequence known before any data.** Pool cases have `T` at least 93 days before `S`. The watch list's hourly snapshots started with detection v1 (ADR-032.1, M1-T24, 2026-09). So most or all pilot bursts will have **day-precision** onsets, and under CB 0.2.0 §6.3 their `trigger_confidence` can be at most `medium`. C10 then varies mainly over {`low`, `medium`}. This doesn't change the C10 statistics or their minimums (§6.2, §6.3), and it is recorded now so that nobody reads it as a finding later. The unit counts in §2 item 4 and addendum 1 are computed on units cut under CB 0.2.0.
- **Day-zone dependency.** Burst days use `star_history_day_tz` (OM §1.2), which is inferred as US Pacific and due to be confirmed across the 2026-11-01 DST change (detection-replan M2). If M2 changes the zone after units are cut but **before** coding starts, units are re-cut, and addendum 1 records the zone status used. If it changes after coding starts, the attempt continues on the units it was given, and the report records this as a deviation. Re-cutting mid-attempt would change units under the coders.

### B3. `novelty_claim` (new; CB 0.2.0 §5.1)
- The case-level fields `novelty_claim` and `novelty_kind` are coded by passes A and B on **P ∪ SE**, from inputs frozen at T (CB §5.1). They are not coded on SC, which codes C4 and C5 only (§4.2).
- They are **not core** (CB §10.3). Agreement is reported but doesn't gate G1:
  - nominal α on `novelty_claim` (`present | absent | unknown`);
  - one binary nominal α per `novelty_kind` value on the units both passes coded `present`;
  - n pairable and the unknown rate per coder.

  They follow the §6.6 format under the heading "non-core fields". **The 20 gating statistics of §6.2 are unchanged.**
- They are not in the §6.7 denominator (non-core enums, as before for other non-core fields).

### B4. MC-09 presence test for the pilot's C11a units (§4.5, C11a/C11b rows)
- Old (candidates.md MC-09, pinned by the header): "*Test:* the `ai_hype` extra field `novelty_claim` has a quoted span", "**Presence test:** `present` if precondition 1 holds. `absent` if the `ai_hype` module is active, the README and tagline at T were captured, and they contain no novelty claim. `unknown` otherwise.", "**Modules:** `ai_hype` (in v0; the codebook gap limits it to this module)."
- New, for all pilot coding: "*Test:* the case-level `novelty_claim` (CB 0.2.0 §5.1) is `present`, with its quoted span." "**Presence test:** `present` if `novelty_claim = present`; `absent` if `novelty_claim = absent`; `unknown` otherwise." "**Modules:** none (not limited to `ai_hype`)." Required assets, sequence, outcome dimensions, loser contrast and the `direction` rule are unchanged.
- `docs/methodology/mechanisms/candidates.md` and `schemas/mechanisms/candidates-v0.json` carry this text as MC-09 version 0.2.0 (file version 0.2.0, written together with this amendment). Addendum 1 hashes those files. No C11a unit has been cut, so this changes no unit.
- MC-09 still can't be promoted on this field, because `novelty_claim` is non-core (CB §10.3). This doesn't matter for the pilot, which promotes nothing (§1).

### B5. What counts as a G1 revision round (§6.7)
- Old (§6.7): "For a later round, the denominator is the count in the codebook version going **into** that round." and "**'The last revision round'** is the revision made after the attempt that is being judged (§7.1)."
- New: unchanged. Added: "The change from 0.1.0 to 0.2.0 was made before attempt 1, with no codes, so it isn't a revision round and counts toward no numerator. The version going into round 1 is 0.2.0, with a denominator of 79."
- **Why:** G1's "< 10% of codebook categories changed" is measured between revision rounds of the pilot, and the pilot hasn't started.

### B6. Selection classes: calibration-split cells, and ordering against threshold calibration (§2 item 3, §3.1)
- Old (§2 item 3, as amended): "Outcome classes can be computed for the selection pool under `outcome-thresholds 0.2.0`, with cells of n ≥ 30 after fallback (OM §3.4)."
- New: "Outcome classes can be computed for the selection pool under `outcome-thresholds 0.2.0`, with cells of n ≥ 30 after fallback (OM §3.4). **Cell populations for these percentiles contain calibration-split cases only.** The query drops held-out cases, using the split rule in the threshold-calibration pre-registration §1.1, before it reads any outcome observation. **The selection script runs only after `docs/preregistration/2026-09-25-threshold-calibration.md` has been committed and pushed**, because selection is the first computation of any class."
- **Why:**
  - The threshold-calibration pre-registration requires that no percentile or class is computed on held-out cases before `outcome-thresholds 1.0.0` is frozen.
  - Original §1 and open issue 1 require that pre-registration to exist before any class base rate is computed.
  - Calibration-only cells are about 30 % smaller, so more pool cases may fall back to a coarser cell or be `small_cohort`. That is accepted.
- The pilot's selection classes stay under 0.2.0. If calibration later changes a parameter (threshold-calibration §2), the pilot sample is **not** re-drawn: the pilot measures codebook reliability, and its classes only picked the cases.

### B7a. Held-out set: H-eval and H-sealed (§3.1, §8)
- Old (§3.1): "It is in the **calibration split**: `sha256("pigtail-outcome-holdout-v1" + case_id) mod 100 ≥ 30` (ADR-019). Held-out cases never enter the pilot." Old (§8, first row): "Held-out exclusion | `sha256("pigtail-outcome-holdout-v1" + case_id) mod 100 < 30` (ADR-019)".
- New: the rules are unchanged. Added: the digest is read as defined in the threshold-calibration pre-registration §1.1 (UTF-8, full hex digest as an integer, mod 100). The held-out set (`m < 30`) is now divided into **H-eval** (`m < 20`) and **H-sealed** (`20 ≤ m < 30`). **Neither enters the pilot** (P, SE, SC or D), the selection pool, or any cell population used for selection (B6). H-sealed is never classed or inspected until a post-freeze threshold change needs its single re-run (threshold-calibration §5.3).
- **Why:** so that a held-out set stays blind after M6 and the forecasting test use H-eval (orchestrator decision, 2026-09-25, before any data). The pilot's sample and pool don't change, because the calibration split is identical.

### B7. Open issue 1 (§11 item 1)
- Old: "**Threshold calibration** (ADR-019, OM §9) needs its own pre-registration before any class base rate is computed. This file doesn't cover it."
- New: "Covered by `docs/preregistration/2026-09-25-threshold-calibration.md`, which is committed before any class base rate is computed (B6)." This file still doesn't cover calibration.

## Not changed

- Objectives (other than the version in B1), G1 and its three parts, the α statistics and their number (20), minimum units, prevalence floor, the ADR-029 companion bound, bootstrap, seeds, samples P/SE/SC/D and their sizes, strata, calipers, the distance formula, overrides, blinding, the double-coding protocol, the review queue, H3 handling, revision, retry and stopping rules.
- Amendment 1 in full (thresholds `0.2.0`, E2/E3/E5, `LSM`, the founder-audience proxy, limitations 6–7, open issue 8).
- Candidate cards other than MC-09.

## Analyses affected

- Unitizing for C9 and C10 (§4.5), and so the §2 item 4 unit counts and addendum 1.
- Provenance on every coded value (§5.2).
- The G1 denominator source file (§6.7; value unchanged).
- MC-09's C11a and C11b units (B4).
- A new non-gating agreement table for `novelty_claim` and `novelty_kind` (B3).
- The cell populations of the selection classes, and the order of steps before selection (B6).
- The held-out definition: its reading is made exact, and it is divided into H-eval and H-sealed (B7a). Pilot samples are unchanged.

No gating rule changes.

## Open issues (for the orchestrator)

1. **ADR needed for the codebook version decision.** It should cover codebook 0.2.0 as a minor bump, the new CB §13 rule ("before 1.0.0, would-be-major changes bump minor", mirroring ADR-035 for thresholds), the day-precision trigger window (CB §6.1), and `high` trigger confidence requiring an hour-precision onset (CB §6.3). ADR-024 items 1 and 3 name the filtered series and hour-based windows and need the matching note. The analyst can't write `ops/DECISIONS.md` under this brief.
2. **MC-09 card files** (B4): done together with this amendment. Addendum 1 hashes them.
3. **Forecasting test and H-sealed.** The forecasting evaluation set must exclude H-sealed live cases. This needs a forecasting-test amendment 2 (threshold-calibration open issue 1); the README has a pointer.
4. **Day-zone confirmation (M2)** should come before unitizing if the pilot's other blockers allow it (B2).
