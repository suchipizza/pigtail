# Amendment 3 to the M4 pilot pre-registration: withdrawal

**Amends (withdraws):**
- `docs/preregistration/2026-09-25-pilot.md`, frozen at commit `3f07692` (`3f0769289e1842d24c1d861e4b05db9fe57ca855`, 2026-09-25T16:10:40+02:00);
- `docs/preregistration/2026-09-25-pilot-amendment-1.md`, frozen at commit `2521652` (`25216526e7ee1cbe857f7d540edbd5f9f4f9c81e`, 2026-09-25T17:18:53+02:00);
- `docs/preregistration/2026-09-25-pilot-amendment-2.md`, frozen at commit `a6a9e3d` (`a6a9e3db29f60e273fdeb94225118c27c60fa9da`, 2026-09-25T18:18:46+02:00).

Freezing commits found with `git log --diff-filter=A --format=%h -- <file>` (README rule 2). All three commits are on `origin/main`. None of the three files is edited (README rule 1).

**Task:** M11 (withdrawal amendments, WORK_ORDER §4.4) · **Written:** 2026-09-26 · **Author role:** `analyst`
**Decided by:** ADR-047.7 (G1 replaced by per-brief, per-field α with "low reliability" labels; earlier pre-registrations withdrawn by dated amendment, not deleted) and ADR-049.5 (the 3 + 3 pilot pre-registration and its amendments 1–2 are withdrawn), with ADR-049.6 (ADR-024.7 and ADR-029.1, the G1 remnants, superseded) and ADR-049.7 (confidence levels retired).
**Code commit when written:** `0330bd4` (HEAD on 2026-09-26).
**Outcome data seen when written:** **none.** No outcome observation, percentile, class, provisional label or class base rate has been computed or looked at, for any case, under any thresholds version. The pilot's selection run never ran, so no case was selected, unitized or coded, and no pilot attempt started. Selection addendum 1 (original §4.6) was never written. No prompt was run on development set D. No α, agreement statistic or disagreement count exists. Outcome scoring is not implemented (`src/pigtail/` has no outcome-observation, percentile or class code), and the last verifier check of the database recorded 0 cases and no outcome tables (`ops/RUNLOG.md`, verifier @`c73bf8c`). Nothing in this amendment was informed by data.
**Timestamp:** the commit that adds this file (README rule 2).

## What is withdrawn

The whole pilot chain: the original and amendments 1 and 2, every section, including the objectives, case selection, samples, double-coding protocol, G1 computation, revision and stopping rules, seeds, report outline and open issues. Quoted below are the parts that define what the pilot was for and how it was judged; the withdrawal is not limited to them.

- **Old** (original §1, objectives):
  > 1. **Check gate G1** (WORK_ORDER M4):
  >    - α ≥ 0.70 on every core field;
  >    - fewer than 10% of codebook categories changed in the last revision round;
  >    - 100% of claims have snapshots.
  > 2. **Revise the codebook** from v0.1.0 and freeze the version that passes G1 as v1.0.0 (CB §13).
  > 3. **Check that the process works end to end:** capture, unitizing, two coding passes through `LLMClient`, citation validation, adjudication, review queue, and the α computation. Measure cost and time per case.
- **Old** (original §3 heading): "## 3. Case selection: 3 winners + 3 matched losers across ≥ 3 strata"
- **Old** (original §3.1, first condition): "It is in the case store with an anchor `T` (OM §2.2), and `S − 730 d ≤ T ≤ S − 93 d`. That is inside R4.1's trailing 24 months, and old enough that `T+90 + 3 d` settle lag has passed, so the T+90 class is final (OM §2.4)."
- **Old** (original §4.1): "The pilot therefore codes a **reliability supplement**: extra cases coded by exactly the same procedure. They serve only to measure α and the unknown rates."
- **Old** (original §6.4): "The α part of G1 passes if and only if **every** one of the 20 statistics is assessed and has a **point estimate α ≥ 0.70** (PRD §9.2; ADR-025 keeps 0.70)."
- **Old** (original §6.7): "**Pass:** numerator / denominator < 0.10. With 79 values, that means **at most 7 changes**."
- **Old** (amendment 1, A1): "`outcome-thresholds 0.2.0`: `schemas/outcome-thresholds/v0.2.0.json`, spec `docs/specs/outcome-model.md` (OM)"
- **Old** (amendment 2, B1): "codebook `0.2.0`: `docs/methodology/codebook.md` and `schemas/codebook/v0.2.0.json`;"
- **Old** (amendment 2, B6): "**Cell populations for these percentiles contain calibration-split cases only.** The query drops held-out cases, using the split rule in the threshold-calibration pre-registration §1.1, before it reads any outcome observation. **The selection script runs only after `docs/preregistration/2026-09-25-threshold-calibration.md` has been committed and pushed**, because selection is the first computation of any class."

**New:** "Withdrawn on 2026-09-26 (ADR-047.7, ADR-049.5). No analysis in `2026-09-25-pilot.md`, `2026-09-25-pilot-amendment-1.md` or `2026-09-25-pilot-amendment-2.md` is run. No selection addendum is written for them. Nothing they define (gate G1, the 3 + 3 selection, the reliability supplement, samples P/SE/SC/D, the 20 gating α statistics, the category-change count, the two-attempt rule, the v1.0.0 codebook freeze) binds any later analysis."

## Why

1. **The scope the pilot served is gone.** ADR-047 (CR-002) re-scopes pigtail from a global mechanism library to per-brief neighbourhood analysis. The pilot selected 3 winners and 3 matched losers from a global case store by global outcome classes (`outcome-thresholds 0.2.0`, category-and-quarter cells), and existed to pass gate G1 before global scale runs. ADR-047.3 cuts the global library, Tier 1 at scale and the global backfill; ADR-049.12 retires the global classes and thresholds as a selection method; ADR-049.8 retires the global cells; WORK_ORDER v2.0 §4.3 retires gates G1 and G2.
2. **The reliability rule changed.** ADR-047.7 replaces G1 (a blocking gate: every core field α ≥ 0.70, fewer than 10 % of categories changed, two attempts) with per-brief, per-field α shown in every report, where findings resting on a field with α < 0.70 are labelled "low reliability", not dropped (PRD R7.5, §9.2). ADR-049.6 supersedes ADR-024.7 (the "< 10 % categories changed" count) and ADR-029.1 (the reliability supplement, which existed only to make G1's fields assessable).
3. **Inputs it pinned are retired or changed.** The held-out split and H-sealed (ADR-019, ADR-039.5) are superseded by ADR-049.5; the mechanism-card confidence levels and promotion rule it referred to are retired (ADR-049.7, PRD F9); the pinned codebook 0.2.0 is replaced for new work by codebook 0.3.0 (`docs/methodology/codebook.md` changelog).
4. **Withdrawn, not deleted.** ADR-047.7 requires a dated amendment so that the record of what was pre-registered, and when, stays in the public history.

## Status of the 3 + 3 pilot

**Withdrawn before it started.** No case was ever selected or coded under it, so there is no partial result to report and nothing to label exploratory. The WORK_ORDER v2.0 pilot is a different study: the first end-to-end neighbourhood report on the owner's brief (M15, PRD §9.4). It gets its **own** pre-registration (success definition, selection, contrasts, sensitivity alternatives, agreement reporting, evidence-decay measurement), committed and pushed **before its outcome sort** (WORK_ORDER §6, ADR-049.5). That new file may reuse design elements from the withdrawn chain (for example the α variants, the minimum-units rule, the bootstrap CI, blinding), but only by restating them; nothing is inherited by reference.

## Analyses affected

Every analysis in the three withdrawn files: case selection and balance reporting, reliability samples and unitizing, double coding, the G1 α statistics and bootstrap CIs, the category-change count, the snapshot check as a G1 component, codebook revision and the v1.0.0 freeze, and the pilot report `docs/reports/pilot.md` as specified there. The file name `docs/reports/pilot.md` is reused by M15 for the new pilot's method-level report, which will cite the new pre-registration, not these files.

No other pre-registration depends on this chain except `2026-09-25-threshold-calibration.md` (step 1 of its §4 is the pilot's selection run), which is withdrawn by `2026-09-26-threshold-calibration-amendment-1.md`.
