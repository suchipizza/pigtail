# Pre-registrations

This folder holds pigtail's pre-registered analyses (WORK_ORDER §6: "Pre-register analyses (hypotheses, tests, thresholds) in `docs/preregistration/` **before** looking at the outcome data they test"). The analyst rule applies to every analysis: anything that isn't pre-registered here, or that deviates from a pre-registration, is labelled **exploratory** wherever it's reported.

## Index

| File | Covers | Requirements | Status | Outcome data seen when written |
|---|---|---|---|---|
| [2026-09-25-pilot.md](2026-09-25-pilot.md) | M4 pilot: case selection, double coding, G1 computation, codebook revision, failure handling | WO §4 M4, PRD §9.2, R4.3, R7.2, R7.3, R6.3 | frozen at the commit that adds it | none |
| [2026-09-25-forecasting-test.md](2026-09-25-forecasting-test.md) | PRD §9.1 test 1 (forecasting), per ADR-026 | PRD §9.1 (1), R3.5, ADR-026 | frozen at the commit that adds it | none |
| [2026-09-25-pilot-amendment-1.md](2026-09-25-pilot-amendment-1.md) | Amends the pilot (frozen at `3f07692`): `outcome-thresholds 0.2.0` (raw star-history classes); E2/E5, `LSM` and the founder-audience proxy moved off the stargazers list API; campaign flags per stratum | ADR-032, ADR-035, ADR-036 | frozen at the commit that adds it | none |
| [2026-09-25-forecasting-test-amendment-1.md](2026-09-25-forecasting-test-amendment-1.md) | Amends the forecasting test (frozen at `3f07692`): `raw` star-history target and star features, ADR-032 screens and `t_det`, X3, filtered-series sensitivity | ADR-032, ADR-035, ADR-036 | frozen at the commit that adds it | none |
| [2026-09-25-pilot-amendment-2.md](2026-09-25-pilot-amendment-2.md) | Amends the pilot (frozen at `3f07692`; amendment 1 at `2521652`): pins codebook `0.2.0` (burst units from `raw` star-history days; case-level `novelty_claim`, non-core); MC-09 presence test; 0.1.0 → 0.2.0 is not a G1 revision round; selection classes on calibration-split cells, run only after the threshold-calibration pre-registration | M4-T1d, M4-T2c, ADR-032, ADR-035, ADR-019 | frozen at the commit that adds it | none |
| [2026-09-25-threshold-calibration.md](2026-09-25-threshold-calibration.md) | Outcome-threshold calibration in the M4 pilot: exact split rule with test vectors; held-out set divided into H-eval (20 %) and H-sealed (10 %); calibration split only; `min_cell_n` {30, 50} and per-source `settle_lag` {1, 3, 7, 14} calibrated by class-free rules; all other parameters fixed; OM §9 diagnostics report-only after the freeze; v1.0.0 freeze procedure; post-freeze changes per ADR-019 | M4-T2b, ADR-019, OM §5.4, §9, PRD §8.2, R3.5 | frozen at the commit that adds it; must be pushed before any class or base rate is computed | none |
| [2026-09-25-forecasting-test-amendment-2.md](2026-09-25-forecasting-test-amendment-2.md) | Amends the forecasting test: evaluation set excludes H-sealed (ADR-039.5); superseded in wording by amendment 3 | PRD §9.1 (1), ADR-039 | frozen at the commit that adds it | none |
| [2026-09-25-forecasting-test-amendment-3.md](2026-09-25-forecasting-test-amendment-3.md) | Amends the forecasting test: old/new text for the evaluation set; target cells (A30, T+90 inputs, sensitivity (a), final status) exclude H-sealed | PRD §9.1 (1), ADR-039, calibration §1.4 | frozen at the commit that adds it | none |

Add a row for every new file, including amendments and addenda.

**Held-out pools (pointer; changes no frozen file).** Under `2026-09-25-threshold-calibration.md` §1.1, the ADR-019 held-out set (`m < 30`) is divided into **H-eval** (`m < 20`) and **H-sealed** (`20 ≤ m < 30`). H-eval is the held-out pool that the forecasting test (`2026-09-25-forecasting-test.md`, amendment 1) draws from. H-sealed cases are never classed or inspected until a post-freeze threshold change needs its single re-run (calibration §5.3). The forecasting pre-registration is frozen and predates this division, so H-sealed is excluded from its evaluation set and from its target cells by forecasting-test amendments 2 and 3, committed before any evaluation target was computed.

## Rules

1. **Frozen once committed.** A pre-registration is frozen as soon as it is committed. After that nobody edits it, not even to fix a typo. Every change goes into a new file (rule 3).
2. **The commit is the timestamp.** A pre-registration's timestamp is the hash and date of the commit that first added it:
   ```
   git log --diff-filter=A --format='%H %cI' -- docs/preregistration/<file>
   ```
   A local commit date can be set to anything. The timestamp that outsiders can verify is the time the commit reached `origin` (https://github.com/suchipizza/pigtail), which is public. Push a pre-registration before collecting or looking at the data it covers.
3. **Amendments are new dated files.** Name them `YYYY-MM-DD-<slug>-amendment-<n>.md`. Each amendment must state:
   - the original file's path and the commit hash that froze it;
   - exactly what changes (quote the old text and give the new text);
   - why it changes;
   - **whether any outcome data had been seen when it was written**, and if so, which data (for example: "pilot pass A/B codes seen, no α computed"; "classes for the calibration split seen; held-out split not seen");
   - which analyses the change affects.

   An analysis that changes after its outcome data was seen is reported as **exploratory**. The only exception is when it is re-run on held-out data under an ADR (ADR-019 policy).
4. **Addenda record, they don't change.** Some pre-registrations require a record to be committed later, for example the SHA-256 of a private case-selection manifest, or of a frozen model artifact. These records go in `YYYY-MM-DD-<slug>-addendum-<n>.md`. An addendum may only add the records the original asks for. If it changes an analysis, it's an amendment.
5. **No private data here.** This folder is public (CLAUDE.md, PRD §10). It holds designs, hashes and aggregate results only: no repo names of matched losers or personal-account repos, no case-level coded values, no quoted spans, no handles or pseudonyms. Private manifests stay in private storage and are referenced here only by their SHA-256.
6. **Thresholds don't move after data.** A threshold set here (α, SMD, Brier margin, minimum n) is never lowered after the data it gates has been seen. Where the PRD sets the threshold (§9.2, §9.3), changing it needs owner approval as well as an ADR (WORK_ORDER §1).
7. **Reports cite their pre-registration.** Every report that runs a pre-registered analysis (in `docs/reports/`) cites the file and its freezing commit hash. It also has a "Deviations" section, which may say "none".

## Errata (naming only; no analysis change)
- 2026-09-25 — `2026-09-25-forecasting-test-amendment-1.md` line 33 uses `bot_filter_confirmed`; the case schema field is `bot_filter.confirmed` (same meaning). Recorded here because the amendment is frozen.
