status: ACTIVE
# ACTIVE | BLOCKED (every remaining task waits on a human gate) | DONE

milestone_focus: M1 (capture), M2 (verification)
updated: 2026-09-25

| Track | Milestone | Status | Blocker |
|---|---|---|---|
| A capture | M1 | in progress (schemas, snapshot store, connector base, velocity scan) | host VM and GCP (H1) for deploy / BigQuery; local dev not blocked |
| B research | M2 | literature review + source matrix **verified (PASS)**; M2-T3 fake-star reproduction pending | M1 GH Archive pipeline |
| C methodology | M3 | outcome spec + compliance pack written; H2 raised; verifier review pending | — |
| C methodology | M4 | ready (codebook v0 next) | — |
| D engine | M0 | **accepted** (verifier PASS 2026-09-25) | — |
| D engine | M5, M8 | not started | G1 / G2 |
| E analysis | M6, M7 | not started | M5 |
| D engine (v2) | M10 | not started | M9 |

Gates: G1 pending · G2 pending
Human gates: H1 OPEN, H2 OPEN (both raised 2026-09-25, default action on 2026-10-09)
Interim holds: ADR-022 (no production capture until the CB controls exist)
