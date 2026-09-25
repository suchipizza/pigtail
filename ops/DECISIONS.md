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
