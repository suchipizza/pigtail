status: ACTIVE
# ACTIVE | BLOCKED (every remaining task waits on a human gate) | DONE

milestone_focus: M1 (capture), M2 (verification)
updated: 2026-09-25

| Track | Milestone | Status | Blocker |
|---|---|---|---|
| A capture | M1 | in progress (schemas, snapshot store, connector base, velocity scan) | host VM and GCP (H1) for deploy / BigQuery; local dev not blocked |
| B research | M2 | literature review + source matrix written; fake-star reproduction and verifier spot-check pending | — |
| C methodology | M3, M4 | not started | M2 |
| D engine | M0 | **accepted** (verifier PASS 2026-09-25) | — |
| D engine | M5, M8 | not started | G1 / G2 |
| E analysis | M6, M7 | not started | M5 |
| D engine (v2) | M10 | not started | M9 |

Gates: G1 pending · G2 pending
Human gates: H1 OPEN (raised 2026-09-25, default action on 2026-10-09)
