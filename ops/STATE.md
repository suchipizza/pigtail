status: ACTIVE
# ACTIVE | BLOCKED (every remaining task waits on a human gate) | DONE

milestone_focus: Owner Directive 001 (ADR-059..072) → M20 docs check, then M21
updated: 2026-09-26
resumed: 2026-09-26 after CR-001/CR-002 (ADR-047/048); work locally, no server

| Milestone (ADR-069/072) | Status | Blocker |
|---|---|---|
| M0, M2, M3, M11, M12 | accepted | — |
| M20 Documents per Owner Directive 001 | done, verifier check running | — |
| M21 Cleanup and privacy (handle model, snapshot purge, API wiring, per-stage models, cost estimator, briefs dir) | next | M20 |
| M22 Discovery, relevance, shortlist review, `pigtail run --brief` | not started | M21 |
| M23 Owner brief #1 pilot (5 cases, cost projection, evidence decay) | not started | M22 |
| M24 Owner brief #1 full run and report | not started | M23, cost within cap |
| M25 D3 plan | not started | M24 |
| M26 Launch mode + D5 | not started | M22 |
| M27 Operator guide, first brief in under an hour | not started | M24 |
| M28 D4 (v2) | not started | M27, H5 |

Gates: G1/G2 retired (ADR-047); per-report quality rules instead
Human gates: H1 OPEN, H2 OPEN (both raised 2026-09-25, default action on 2026-10-09)
Interim holds: ADR-022 (no production capture until the CB controls exist)
