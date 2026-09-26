# Pre-registration amendment 1: brief 1, version 6

**Amends:** `docs/preregistration/2026-09-26-brief-1.md` (frozen at `e4b5f00`; unchanged).
**Requirements:** PRD R8.2; README rules 2–5; outcome-model v2.1 §2.1–§2.2, §5.8; ADR-081.
**Written:** 2026-09-26 · **Author role:** `analyst` (orchestrator agent, for the owner)
**Code commit when written:** `2e19b0a`
**Outcome data seen when written:** none for this brief. Its outcome sort has not run: no
percentile, winner, loser, anchor or balance statistic of brief 1 (any version) exists. The
M22 acceptance run of the synthetic example brief (a different brief, on a throwaway instance)
was seen; it motivated no change below beyond what ADR-081 requires.
**Timestamp:** the commit that adds this file, as pushed to `origin` (README rule 2).

This file holds no brief content (README rule 5).

## 1. Why

The verifier's round 3 on M22 found that launch anchors used only the Show HN posts discovery
happened to record, which departs from outcome-model §2.1 and would push launched repos with
little traction out of the population and the loser pool. ADR-081 fixes this with a per-repo
launch lookup and a day-precision comparison against bursts, versions the anchor rule
(`anchor-v2`) and bumps the selection to `selection-v3`. The selection parameters, and their
hash, therefore changed. The brief itself did not.

## 2. New binding

| Item | Value |
|---|---|
| `brief_id_sha256` | `1d7308991ef2e88eed3b659358c21cf1db64234809ba98f4d13aa3cea16626bb` (unchanged) |
| `brief_version` | `6` (unchanged) |
| `brief_sha256` | `2b8b601bb0847d5b0a851a885c34a6f8b66839b2832b4a20b7f10b589cae07e1` (unchanged) |
| `success_definition_sha256` | `167559d8b565b017054a9c83a6a117ee45e21ed54a8fae09dd35fdbf1c771110` (unchanged) |
| `selection_params_sha256` | `c0713107b958c061a31fed723dcc7078498d857a22a2a787fe78fa95ec3b415b` (was `352a1c76…`) |
| `selection_version` | `selection-v3` (was `selection-v2`) |
| Anchor rule | `anchor-v2` (ADR-081) |
| `outcome_model_version` | `2.1` (unchanged) |
| Codebook version | `0.4.0` (unchanged) |

## 3. Change to the hypotheses: H1 (MC-01)

Under the anchor rule, every loser has a declared launch, while a winner can be anchored on a
burst, and `launch_type` is not an exact-match key. A contrast "a first-party Show HN / Launch
HN post is more common among winners" is therefore decided by the design, not by the data.

- **H1 is restricted to launch-anchored pairs** (ADR-081.6). Within those pairs, presence of a
  launch post is equal on both sides by construction, so **H1 presence is reported as "not
  testable under this anchor rule"**.
- Within launch-anchored pairs, the report contrasts only what differs between launches:
  timing (H3), reception (points, front page; H2, labelled "close to the outcome" as before) and
  the preparation around the launch (H4, H5, H7, H8, H11).
- The count of winners anchored on a burst rather than a launch is reported with the headline.

H2–H13 and the tests and decision rules of the original file (§3) are unchanged.

## 4. Deviations

None beyond the above. The original file's §4 notes (unmeasured precision; four unresolved
reference cases) still apply.
