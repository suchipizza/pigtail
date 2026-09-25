# Weekly status for the owner
(Written by the orchestrator weekly: progress per milestone, gates, what's blocked on you, costs, notable findings.)

## Week of 2026-09-21 (first report, 2026-09-25)
**Progress**
- M0 bootstrap: done locally. Repo scaffold, CI, private-data scan, docker compose (Postgres + S3-compatible storage), and the LLM client with the subscription/api switch. The subscription smoke test passed on your plan. GitHub CI confirmation is pending the first push.
- M2 research: literature review and source/terms matrix in progress.
- M1 capture: starts next, developed locally until a host exists.

**Gates:** G1 pending · G2 pending.

**Blocked on you (see `ops/HUMAN_INPUTS.md` → H1, deadline 2026-10-09):** a host VM (EU/CH) and a read-only GitHub token first; then GCP/BigQuery, a private bucket and backups, and the Reddit API. Also: please make sure model training is turned off in your Claude account's privacy settings.

**Costs to date:** USD 0 (LLM calls ran on your subscription).

**Notable:** The MinIO Docker image is no longer pullable, so local storage uses SeaweedFS (ADR-005). Anthropic's Claude Code terms confirm the subscription backend is for your own use only; shared deployments must use the API (ADR-008).

### Update (same day, later)
- M0 **accepted** by the verifier. M2 research **accepted** after one correction round (no fabricated citations).
- M3: outcome-model spec and compliance pack written. **H2 (legal review) raised**: see `ops/HUMAN_INPUTS.md`.
- **Please read this one:** the compliance review found that Anthropic's Consumer Terms for Pro/Max in the EEA/CH include a no-commercial-or-business-use clause, and those plans have no zero-retention option. pigtail keeps the subscription backend only for your own research use, and bulk coding of person-level text waits for your lawyer's answer (ADR-023). Switching to the API later is a one-line change.
- Production capture won't start on a host until the basic privacy controls (retention purge, encryption, raw-dump minimization, a published notice) are built (ADR-022). They're next in the backlog.
