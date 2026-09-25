# Decision log (ADRs)
Format per entry: `## ADR-NNN — title (date)` · Context · Options · Decision · How to reverse.

## ADR-000 — Defaults adopted at handoff (2026-09-25)
Context: The owner handed off autonomous execution.
Decision: Adopt the PRD §12 defaults. No composite outcome score. The capture layer starts before the pilot gate; the analysis layer is gated on G1.
How to reverse: A new ADR, with rationale.

## ADR-001 — Switchable LLM backend, subscription by default (2026-09-25)
Context: The owner wants to use her Claude subscription for now, with the option to switch to the API.
Decision: Agents run through Claude Code on the subscription (`AGENT_BACKEND`). Product LLM calls go through `LLMClient` with `LLM_BACKEND=subscription|api` (PRD F15). The subscription path uses only the official `claude` CLI and Anthropic's login flow, and pigtail never touches credentials. In subscription mode, content is pseudonymized before it is sent to the model.
Risks:
- Usage limits throttle the scale runs. Mitigation: pause/resume, and per-job API overrides.
- Anthropic's terms restrict subscription use by third-party products. Mitigation: the subscription backend is scoped to operator self-use through the official CLI, and shared deployments must use `api`. Re-check the Claude Code legal and compliance page at M0 and before release.
How to reverse: Set both variables to `api`.

## ADR-002 — Public repository from day one (2026-09-25)
Context: The repo github.com/suchipizza/pigtail already exists, is public and is MIT-licensed.
Decision: Build in public. Private data lives only in private storage, and a CI private-data scan blocks leaks. H4 now covers publishing findings and tagging the release, not repo visibility.
How to reverse: Make the repo private on GitHub; nothing else changes.

## ADR-003 — Repository layout (2026-09-25)
Context: The work order asks for a monorepo with uv. The Python engine comes first; the TypeScript UI starts with the D1 preview (M1).
Options: (a) uv workspace with several Python packages; (b) one Python package `src/pigtail` with subpackages, plus `ui/` for TypeScript later.
Decision: (b). One package (`pigtail.llm`, later `pigtail.capture`, `pigtail.connectors`, …), `schemas/` for JSON Schemas, `infra/` for deploy config, `ui/` for the React app when it starts. Python pinned to 3.12 via `.python-version` and `uv.lock`.
How to reverse: Split subpackages into uv workspace members; imports stay the same.

## ADR-004 — LLM cache and usage ledger in local SQLite for M0 (2026-09-25)
Context: R15.4 needs a cache and ledger now; the Postgres capture schema arrives in M1.
Decision: `pigtail.llm.store.LLMStore` uses SQLite under `PIGTAIL_DATA_DIR` (gitignored). The interface (cache_get/put, record, pause, summary) stays the same when it moves to Postgres.
How to reverse: Implement the same interface on Postgres and swap it in `build_client`.

## ADR-005 — SeaweedFS instead of MinIO for local object storage (2026-09-25)
Context: `docker compose up` must start object storage. The `minio/minio` image (Docker Hub and quay.io) can no longer be pulled anonymously.
Options: SeaweedFS (Apache-2.0, S3 API), RustFS (young project), Garage.
Decision: SeaweedFS `chrislusf/seaweedfs:4.47`, pinned, with S3 credentials from `S3_ACCESS_KEY`/`S3_SECRET_KEY`, and a bucket created by an `aws-cli` init container. The code only uses the S3 API, so any private S3-compatible bucket works in production.
How to reverse: Replace the `objectstore` service; nothing else depends on the implementation.

## ADR-006 — LLM cache key and redaction scope (2026-09-25)
Context: R15.4 keys the cache on (prompt version, input hash). The parity check (R15.7) must compare backends, and a model change must not serve stale outputs.
Decision: The key is (prompt id, prompt version, redacted-input hash, output-schema hash, model, backend). Identifier stripping (e-mails, phone numbers, @handles → keyed pseudonyms) runs before **every** model call on **both** backends, not only in subscription mode. It's the conservative reading of PRD §10 and costs nothing.
How to reverse: Drop fields from `LLMClient.cache_key`; make redaction conditional on the backend.

## ADR-007 — Default product model `claude-opus-5` (2026-09-25)
Context: PRD F15 doesn't name a model. The same model ID has to work on both backends so the parity check compares like with like.
Decision: `LLM_MODEL=claude-opus-5` by default for both backends. Cheaper models can be set per deployment. Bulk jobs may get a per-job model once the pilot shows the agreement holds (R15.7-style check, logged as an ADR).
How to reverse: Set `LLM_MODEL`.

## ADR-008 — Subscription-backend scope confirmed against Anthropic's Claude Code legal page (2026-09-25)
Context: ADR-001 asked for a re-check at M0.
Finding (https://code.claude.com/docs/en/legal-and-compliance, accessed 2026-09-25): Free/Pro/Max use of Claude Code falls under the Consumer Terms. Running Claude Code inside products or services requires the Commercial Terms, an unmodified binary, and each end user authenticating with their own credentials; the provider may not pay for, resell or intermediate Claude usage for end users.
Decision: Keep R15.6 as written. The `subscription` backend is for an operator running pigtail for themselves on their own plan, through the unmodified official CLI. Any deployment that serves other users must use `api`. The operator guide says so. Re-check before release (M9).
How to reverse: n/a (compliance constraint). Revisit if Anthropic's terms change.

## ADR-009 — Don't rely on GH Archive alone for star velocity (2026-09-25)
Context: The source audit (docs/research/source-matrix.md) found open, unanswered community issues on the gharchive repo (#310, #320). They report event volume dropping after May 2025 and star (WatchEvent) capture at about 10–20% since Feb 2026. These are community reports, not confirmed. Verifier check (M2-T4): #320's comparison covers only 2 repos. #310's comments point to a likely systematic cause (the crawler polls only page 1 of the Events API). #312 reports a further ~100× drop from 2025-10-09. If the cause is systematic, the loss is probably biased toward high-activity periods, which are exactly the bursts pigtail detects. So the coverage ratio has to be measured per case, not assumed constant. GH Archive also records only star `started` events, so R1.1 "net stars" can't be computed from it.
Options: (a) trust GH Archive; (b) switch star series to the GitHub API only (rate-limited; not feasible for universe-wide scanning); (c) use GH Archive for screening, confirm candidates against the GitHub stargazers API, and record per-case coverage.
Decision: (c). Detection stays pluggable by source. Every case records a coverage ratio (observed vs reference). The absolute thresholds in R1.1 are read as "observed in GH Archive" until they are recalibrated against measured coverage (M1-T16). The fake-star reproduction (R3.3) must report coverage for its window.
How to reverse: If coverage is confirmed to be ≥ 95%, drop the confirmation step and keep the coverage field.

## ADR-010 — Source clearances adopted from the M2 source matrix (2026-09-25)
Context: The source matrix classified 25 sources: 1 cleared, 11 cleared with conditions, 13 gaps.
Decision: Connectors are built only for CLEARED or CLEARED-WITH-CONDITIONS sources, in the matrix's priority order, and must meet the conditions listed in docs/compliance/terms-memos.md. Wayback and V2EX stay off by default until H2 answers Q5 and Q8. Reddit, YouTube, X, Product Hunt, Lobste.rs, dev.to, Juejin, Zhihu, Bilibili, TrustMRR, Crunchbase (unless the operator brings a licence) and the YC directory are documented gaps (R2.3). Outcome metrics that depend on gap sources are tagged `self_reported` or `unknown` in the M3 outcome model. This is conservative until the legal review; it does not relax any PRD requirement.
How to reverse: Change a source's clearance after H2 answers; a new ADR per source.

## ADR-011 — Role e-mail addresses allowed by the private-data scan (2026-09-25)
Context: The source matrix cites a platform's published contact address (hello@…). The scan blocked it as personal data.
Decision: Generic role addresses (hello@, info@, support@, legal@, …) are allowed; personal addresses are still blocked. Commits are gated on the scan passing (it failed once without gating: see RUNLOG 2026-09-25; no personal data was involved).
How to reverse: Remove the role-address pattern from `EMAIL_ALLOW`.
