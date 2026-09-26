status: ACTIVE
# ACTIVE | BLOCKED (every remaining task waits on a human gate) | DONE

milestone_focus: Owner Directive 001 (ADR-059..073) → M20 docs check, then M21
updated: 2026-09-26
resumed: 2026-09-26 after CR-001/CR-002 (ADR-047/048); work locally, no server

| Milestone (ADR-069/072) | Status | Blocker |
|---|---|---|
| M0, M2, M3, M11, M12 | accepted | — |
| M20 Documents per Owner Directive 001 | **accepted** (2026-09-26) | — |
| M21 Cleanup and privacy | **accepted** (2026-09-26) | — |
| M22 Discovery, relevance, shortlist review, selection (outcome sort, matched losers, balance, sensitivity), `pigtail run --brief` | in progress (round-2 fixes done, ADR-078; follow-ups: shortlist carry-forward ADR-079, example ranks on attention ADR-080; awaiting verifier round 3) | the live acceptance run waits for the owner |
| M23 Owner brief #1 pilot (5 cases, cost projection, evidence decay) | not started | M22; CB-12 (H5) and CB-06b for person-level sources |
| M23b Outcome connectors (npm, crates.io, returning contributors; PyPI/deps.dev optional) | not started (ADR-080) | M23; before the owner's second report |
| M24 Owner brief #1 full run and report | not started | M23, cost within cap |
| M25 D3 plan | not started | M24 |
| M26 Launch mode + D5 | not started | M22; M24 (R17.5 validation cases) |
| M27 Operator guide, first brief in under an hour | not started | M24, M25, M26 |
| M28 D4 (v2) | not started | M27, H5 |

Gates: G1/G2 retired (ADR-047); per-report quality rules instead
Human gates: H1 OPEN, H2 OPEN (both raised 2026-09-25, default action on 2026-10-09)
Interim holds: ADR-022 as amended by ADR-073.2 (person-level sources stay off until CB-12, the privacy notice published after the owner's approval via H5, and CB-06b are done). Reports stay in the instance's private data directory (ADR-073.1).
