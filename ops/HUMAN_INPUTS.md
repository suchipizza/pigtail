# Human inputs (owner)
Agents: add requests here in batches, each with a deadline, what it blocks, and the default action if unanswered.
Owner: answer inline and commit. **This repo is public:** never write secrets, balances or personal details here. Just tick ✅ when something has been provided through `.env` or the secrets manager.
Fill in H1 **before kickoff** if you can; everything else can wait.

## H1 — Credentials, infrastructure, budget  [OPEN]
Claude (subscription is the default backend for both the agents and the product):
- [ ] Logged in to Claude Code with your Claude plan on the machine that runs the agents (`claude`, then `/login`; confirm with `/status`)
- [ ] For the headless host: run `claude setup-token` yourself and put the token in the host's environment as `CLAUDE_CODE_OAUTH_TOKEN`. Never commit it.
- [ ] `ANTHROPIC_API_KEY` is NOT set on machines that should use the subscription (if it is set, it overrides the subscription)
- [ ] Model-training turned off in your Claude account's privacy settings (pigtail sends pseudonymized public-web content to the model)
- [ ] Optional: Anthropic API key for `LLM_BACKEND=api` / `AGENT_BACKEND=api`, and for the backend parity check

Infrastructure and data sources:
- [ ] Push access to github.com/suchipizza/pigtail for the agent machine (deploy key or token)
- [ ] Host VM (EU/CH region), SSH access for deploys
- [ ] Private S3-compatible bucket for snapshots, and a private backup location for the database
- [ ] GitHub token (fine-grained, read-only public data) for collectors
- [ ] GCP project with BigQuery billing (GH Archive, PyPI downloads)
- [ ] Reddit API app, plus data-access approval if it is required for your use
- [ ] Optional: X API, YouTube Data API, Product Hunt API
- [ ] Optional: SMTP / email for alerts and the weekly status
- [ ] Monthly budget ceiling set in the host `.env` as `BUDGET_USD_MONTH` (default 300)
- [ ] Codebook/methodology licence: MIT (default, matches the repo) or CC BY 4.0

## H2 — Legal review  [not yet raised]
## H3 — Human calibration coding (optional, ~3–4 h)  [not yet raised]
## H4 — Publish aggregate findings / D2 public mode / tag v1.0  [not yet raised]
## H5 — External actions / owner launch profiles  [not yet raised]
## H6 — Over-budget spend  [none]
