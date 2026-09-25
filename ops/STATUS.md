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

### End-of-day summary (2026-09-25)
**Milestones:** M0 accepted · M2 accepted (except the fake-star reproduction M2-T3, which needs star data) · M3 accepted, and the ADR-032–038 updates verified · M1 capture built but **not running** (no host, no GitHub token) · M4 pilot designed and pre-registered, not started.

**What changed today that you should know**
- **GitHub Archive is no longer a usable source of star data** (≈ 1–2% of stars captured), and **GitHub closed stargazer lists** on 2026-06-30. Detection was rebuilt on GitHub's new daily star counts plus an hourly watch list (ADR-032). It is built and tested, but only against a fake GitHub API until a token exists.
- **Outcome classes use raw daily star counts** (thresholds v0.2.0, ADR-035); fake-star filtering becomes a sensitivity check where data allows.
- **Privacy controls built:** retention purge, opt-out list, access and erasure requests, LLM redaction and cache expiry, HN deletion sync, 16-day limit on per-repo event data (ADR-038).
- **D1 preview app** (password-protected case browser) and the **unattended scheduler** are ready to deploy.

**Blocked on you (see `ops/HUMAN_INPUTS.md`, defaults apply 2026-10-09)**
1. **Host VM + `GITHUB_TOKEN`:** nothing collects until these exist. HN front-page rank history, which can't be backfilled, is lost every day until then.
2. **Confirm model training is off** in your Claude account (or provide an API key): needed for the pilot.
3. **Name and contact for the privacy notice** (CB-12): needed before HN mentions, Bluesky or per-repo events can be switched on.
4. **Lawyer (H2):** 29 questions; LQ-1/2 (Consumer Terms: no commercial use of your subscription) matter most.

**Costs to date:** USD 0 in API or hosting. Research measurements used about 2,100 GitHub API requests on your logged-in `gh` account (within limits) and about 11 GB of downloads.

**Process incidents:** twice, a commit was pushed after the private-data scan failed (no real secret or personal data either time). Both fixed; the check now runs separately before any commit.
