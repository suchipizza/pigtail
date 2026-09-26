# Human inputs (owner)
Agents: add requests here in batches, each with a deadline, what it blocks, and the default action if unanswered.
Owner: answer inline and commit. **This repo is public:** never write secrets, balances or personal details here. Just tick ✅ when something has been provided through `.env` or the secrets manager.
Fill in H1 **before kickoff** if you can; everything else can wait.

## H1 — Credentials, infrastructure, budget  [OPEN — raised 2026-09-25, deadline 2026-10-09]
**Blocks:** deploying the capture layer to a host (M1 acceptance needs 7 days of unattended scans), BigQuery-based GH Archive and PyPI queries, Reddit, and the M5–M6 scale runs.
**Default if unanswered by 2026-10-09:** keep building and testing locally; use GH Archive hourly dumps (no BigQuery) and keyless public APIs (HN, Bluesky, Wayback, registries); record the other sources as gaps; spending ceiling USD 300/month.
**Update 2026-09-26:** GitHub token and FileVault done. The host VM is optional (ADR-048: batch runs on the Mac).
Agent notes (2026-09-25): on the agent machine, Claude Code is logged in with a Claude plan and `ANTHROPIC_API_KEY` is not set (checked with `claude auth status`). The `gh` CLI has push access to the repo. Everything else below is still needed from you.

Claude (subscription is the default backend for both the agents and the product):
- [x] Logged in to Claude Code with your Claude plan on the machine that runs the agents (verified by agent 2026-09-25)
- [ ] For the headless host: run `claude setup-token` yourself and put the token in the host's environment as `CLAUDE_CODE_OAUTH_TOKEN`. Never commit it.
- [x] `ANTHROPIC_API_KEY` is NOT set on the agent machine (verified 2026-09-25); still to check on the host
- [x] Model-training turned off in your Claude account's privacy settings — confirmed by the owner 2026-09-26
- [ ] Optional: Anthropic API key for `LLM_BACKEND=api` / `AGENT_BACKEND=api`, and for the backend parity check

Infrastructure and data sources:
- [x] FileVault on (required by ADR-048) — verified 2026-09-26 with `fdesetup status`
- [x] Push access to github.com/suchipizza/pigtail for the agent machine (`gh` logged in, verified 2026-09-25)
- [ ] Host VM (EU/CH region), SSH access for deploys
- [ ] Private S3-compatible bucket for snapshots, and a private backup location for the database
- [x] GitHub token (fine-grained, read-only public data) for collectors — set in the local .env, verified 2026-09-26 (full rate limit)
- [ ] GCP project with BigQuery billing (GH Archive, PyPI downloads)
- [ ] Reddit API app, plus data-access approval if it is required for your use
- [ ] Optional: X API, YouTube Data API, Product Hunt API
- [ ] Optional: SMTP / email for alerts and the weekly status
- [ ] `PSEUDONYM_KEY` generated on the host and backed up **separately** from the data (e.g. `openssl rand -hex 32`)
- [ ] Monthly budget ceiling set in the host `.env` as `BUDGET_USD_MONTH` (default 300)
- [ ] Codebook/methodology licence: MIT (default, matches the repo) or CC BY 4.0

## H2 — Legal review  [OPEN — raised 2026-09-25; formal default date 2026-10-09 (WO §5); requested answer date 2026-11-06]
**What:** An external lawyer reviews `docs/compliance/`. Start with `legal-review-questions.md`, then `lia.md`, `dpia.md`, `retention-policy.md`, `privacy-notice.md` and `terms-memos.md`, and answer LQ-1…LQ-26. Priority-A questions first: LQ-1, LQ-2, LQ-4, LQ-8, LQ-10, LQ-12. You (the owner) fill in the `[operator]` and contact placeholders in `privacy-notice.md`.
**Most important for you to know now:** under the EEA/Swiss Consumer Terms that cover Pro/Max plans, you agree not to use the services "for any commercial or business purposes", and zero data retention isn't offered on those plans. If the lawyer confirms this applies to pigtail, the default product backend must become `api` (LQ-1, LQ-2; ADR-023).
**Blocks:** publishing anything (M9: findings, D2 public mode, v1 tag). Also: subscription-mode LLM coding of person-level text at scale (LQ-1/2), account-level spread graphs (LQ-8), and the Wayback, V2EX, Discord and HN connectors for commercial operators (LQ-15/18/21/6). **Does not block** collection under the documented safeguards.
**Default if unanswered by 2026-10-09:** release stays blocked and the interim defaults in ADR-022 stay in force. Everything else continues.
## H1/H2 follow-ups — added 2026-09-25 (small, but they unblock the pilot)
- [x] **Model training off:** confirmed by the owner 2026-09-26 ("Help improve our AI models" turned off). Pilot coding may run on the subscription (ADR-029.4 / ADR-053).
- [ ] **Privacy notice details (CB-12):** the controller name and a contact address for `docs/compliance/privacy-notice.md`. Please don't write a personal e-mail here (public repo). A role address or "via GitHub issues on the repo" works. Other person-level sources (Hacker News, Bluesky) stay off until the notice is published (ADR-022).
- **Default if unanswered by 2026-10-09:** the pilot waits; the person-level sources stay off; everything else continues.

## H3 — Human calibration coding (optional, ~3–4 h)  [not yet raised]
## H4 — Publish aggregate findings / D2 public mode / tag v1.0  [not yet raised]
## H5 — External actions / owner launch profiles  [not yet raised]
## H6 — Over-budget spend  [none]
