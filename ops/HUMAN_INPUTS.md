# Human inputs (owner)
Agents: add requests here in batches, each with a deadline, what it blocks, and the default action if unanswered.
Owner: answer inline and commit. **This repo is public:** never write secrets, balances, personal details or brief content here. Just tick ✅ when something has been provided through `.env` or the secrets manager.

**Gates as of 2026-09-26:** Owner Directive 001 §10 (`ops/OWNER_DIRECTIVE_001.md`), logged as ADR-068. The default for every gate applies if there is no answer within 14 days of the request. The v2.0 gate texts are replaced; superseded items are kept, struck through, at the end of each section so the history stays readable.

## H1 — Credentials and machine setup  [OPEN — raised 2026-09-25, updated 2026-09-26 per Directive §10; deadline 2026-10-09]
**What (Directive §10, ADR-068):** an Anthropic API key (Commercial Terms) with spend limits set in the Console; a GitHub token; FileVault confirmed on; an encrypted local backup location; the Claude subscription login for the agents. Optional: BigQuery billing (free tier only), SMTP.
**Blocks:** product LLM calls (API key); backups (backup location); live runs of sources whose credentials are missing.
**Default if unanswered by 2026-10-09:** continue with what is available; record missing sources as gaps.

Required:
- [x] Anthropic API key for product calls (`LLM_BACKEND=api`, Commercial Terms) — verified 2026-09-26; the three models of ADR-064.3 were visible on the key
- [x] **Spend limits set on that key in the Anthropic Console** — confirmed by the owner 2026-09-26 (matches the monthly API cap).
- [x] GitHub token (fine-grained, read-only public data) — set in the local `.env`, verified 2026-09-26 (full rate limit)
- [x] FileVault on — verified 2026-09-26 with `fdesetup status`
- [~] **Encrypted local backup location** — owner chose an external drive (2026-09-26). Still to do: set `BACKUP_DIR` to the drive's path and `BACKUP_RECIPIENT` to an age public key, then run a first `pigtail backup create` (the briefs directory is included from M21).
- [x] Claude subscription login for the agents (`AGENT_BACKEND=subscription`) — verified by agent 2026-09-25 with `claude auth status`

Optional:
- [ ] GCP project with BigQuery billing, **free tier only** (GH Archive discovery signals, PyPI downloads)
- [ ] SMTP / e-mail for alerts and the weekly status

Other open items carried over (not gates under Directive §10; defaults apply):
- [ ] Key for the opt-out HMAC fingerprint (today `PSEUDONYM_KEY`; ADR-071.1), generated on the machine and backed up **separately** from the data (e.g. `openssl rand -hex 32`)
- [ ] Optional: Reddit API app and data-access approval. Until approval, Reddit is metadata only (Directive §8.4, ADR-066.4).
- [ ] Codebook/methodology licence: MIT (default, matches the repo) or CC BY 4.0

Superseded (kept for history):
- ~~Host VM (EU/CH region) and a private S3 bucket~~ — optional since ADR-048 (batch runs on the Mac).
- ~~For the headless host: `claude setup-token`~~ — only needed on the optional server path.
- ~~Model training off, so pilot coding may run on the subscription (ADR-029.4 / ADR-053)~~ — confirmed by the owner 2026-09-26, but product calls now run on the API (ADR-064.1). The setting still matters for the agents' subscription.
- ~~Monthly budget ceiling `BUDGET_USD_MONTH` (default 300)~~ — replaced by the caps in H6.

## H2 — Narrowed legal consult  [OPEN — raised 2026-09-25, narrowed 2026-09-26 per Directive §9]
**What (Directive §9, ADR-067):** the three questions in `docs/compliance/LEGAL_REVIEW_H2.md`:
1. legitimate interest (FADP; GDPR Art. 6(1)(f)) for collecting and coding public posts about shortlisted projects under the mitigations, and whether GDPR applies through Art. 3(2)(b);
2. whether a public privacy notice is enough (GDPR Art. 14(5)(b) and the FADP equivalent);
3. optional, only if Reddit is enabled beyond metadata: Reddit's terms for this use.

The Anthropic questions (LQ-1, LQ-2) are **closed**, because product calls run on the API (ADR-064, ADR-067). `docs/compliance/legal-review-questions.md` (LQ-1…LQ-31) is kept as history.
**Blocks:** publishing only, and publishing is disabled (H4). **It does not block collection** under the mitigations.
**Default if unanswered:** collection continues under the mitigations. Nothing is published.

## H3 — Optional human calibration coding (~2 h)  [OPTIONAL — per Directive §7 and §10]
**What:** if you want, code a small sample of brief #1's cases so the report can show LLM–human agreement.
**Blocks:** nothing.
**Default:** LLM–LLM agreement only; findings carry the label "LLM-coded, not human-validated".

## H4 — Publishing anything  [DISABLED — Directive §8.6 and §10, ADR-066.6]
No findings, data or reports leave your instance, and there is no public mode, until you revoke Owner Directive 001. The repo contains only code, docs, templates and synthetic fixtures. Nothing to answer.

## H5 — External actions on your behalf  [OPEN — one item]
**Rule (Directive §10):** agents take no external action on your behalf without your approval. Default: don't act.
- [~] **Privacy notice publication (ADR-071.4).** The notice for your instance goes in a separate repo on GitHub Pages (it describes your instance, not the tool). It will name you as controller, a **dedicated contact alias** (please don't use or write your personal e-mail here), purpose, sources, what is and isn't stored (roles and buckets, no handles), retention (report final + 12 months), legal basis, how to opt out or object (the opt-out fingerprint), and US processing. **The draft will be shown to you before anything is published.** Needed from you: approval of the draft, and the contact alias. Until the notice is published (and CB-06b is done), the person-level sources held by ADR-022 (HN mentions and comments, Bluesky, per-repo events) stay off (CB-12, ADR-049.2; ADR-022 as amended by ADR-073.2). **Status 2026-09-26:** owner's v1.1 draft adopted (private); contact address chosen; still to fill: email provider name; to verify: Anthropic contracting entity and default API retention; publication waits for the H2 answers (postal address, EU representative) and the owner's approval (H5).

## H6 — Spend above the caps  [standing rule — Directive §6.4 and §10, ADR-064.4]
**Caps:** each brief's own API cap (your first full brief: USD 150); USD 200 per month on the API; USD 0 for any other paid service (BigQuery free tier only; Trendshift and X off).
**Rule:** agents show an estimate and wait for your approval before any spend above these caps. After the first 5 pilot cases, the cost per case and a projection go in `ops/COSTS.md` and `ops/STATUS.md`; if the projection exceeds the cap, the run stops and asks here.
**Default:** don't spend; degrade gracefully (runs hard-stop at the cap with a checkpoint). No request open.

## Brief fields to confirm in the shortlist review  [generic item — Directive §4 generic rule, ADR-062]
When brief #1 runs, any brief field that is missing gets a default, and any reference case whose repo can't be resolved is left open. **Neither blocks the run.** They are listed in the shortlist review screen (`/briefs/:id/shortlist`) for you to confirm or correct. This file only records that such a list exists; the fields and their values stay in your private brief (`~/.pigtail/briefs`) and never appear here.
- [ ] Confirm or correct the defaulted brief fields and unresolved reference cases in the shortlist review of brief #1.
