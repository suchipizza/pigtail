status: BLOCKED
# ACTIVE | BLOCKED (every remaining task waits on a human gate) | DONE

milestone_focus: M1 (capture), M2 (verification)
updated: 2026-09-25
paused_by_owner: 2026-09-25, until the host VM and GITHUB_TOKEN are ready (H1)

| Track | Milestone | Status | Blocker |
|---|---|---|---|
| A capture | M1 | built: GH Archive scan (control), HN rank poller, HN connectors (held), detection v1 on GitHub API (ADR-032/037), privacy controls, scheduler, D1 preview. Not live: no host, no GITHUB_TOKEN | H1 (host, GITHUB_TOKEN); ADR-022 holds; CB-12 notice details |
| B research | M2 | literature review + source matrix **verified (PASS)**; M2-T3 fake-star reproduction pending | M1 GH Archive pipeline |
| C methodology | M3 | **accepted** (verifier PASS round 4, 2026-09-25) | — |
| C methodology | M4 | method set verified (codebook v0.2.0, pilot + amendments 1–2, forecasting + amendments 1–4, threshold calibration; PASS 2026-09-25); pilot not started | ADR-022 controls (CB-12) + HN person-level sources; case pool needs live detection (GITHUB_TOKEN); H1 training-off or API key; DST check 2026-11-01 |
| D engine | M0 | **accepted** (verifier PASS 2026-09-25) | — |
| D engine | M5, M8 | not started | G1 / G2 |
| E analysis | M6, M7 | not started | M5 |
| D engine (v2) | M10 | not started | M9 |

Gates: G1 pending · G2 pending
Human gates: H1 OPEN, H2 OPEN (both raised 2026-09-25, default action on 2026-10-09)
Interim holds: ADR-022 (no production capture until the CB controls exist)
