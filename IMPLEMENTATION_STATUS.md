# Implementation status — Pigtail Light V1

Last updated: 2026-10-06 (overnight autonomous session)

## Light V1 boundary (summary of the PRD)

**Build:** a public Python package and CLI that takes a GitHub repository URL or a product domain,
researches public evidence, and writes a portable **Research Bundle v0.1** plus a standalone
**HTML growth forensic** rendered only from that bundle. Repository targets add GitHub star
history with launches/posts/releases on one time axis, growth episodes, launch episodes,
post-launch windows, repeated-launch comparison and durability. Plus: source-policy mechanism,
validation, golden examples, tests, a static website/example library, and launch-ready README/docs.

**Do not build for V1:** Postgres/Supabase, Neo4j, pgvector, Pigtail Review app, CandidateChangeSet
workflow, canonical ingestion, graph projector, outbox, hosted updates, cohort pipeline, Forge,
API, MCP, CMO, auth, billing, schedulers, production monitoring. Their contracts in the spec are
frozen for compatibility only.

**Non-negotiables:** Source ≠ Claim ≠ Event ≠ Tactic ≠ GrowthEngine; MetricSnapshot ≠ Outcome;
CompanyStage ≠ StrategyPhase; timing ≠ causation; every displayed fact traces
object → Claim → EvidenceLink → Source → SourceFetch/locator; unknown is a valid answer; nothing
fabricated; Pigtail is not a content archive.

## Milestones

| Milestone | State |
|---|---|
| M0 Contract skeleton | **Done** — package, CLI (`analyze`, shorthand, `render`, `validate`, `doctor`, `version`), config, UUIDv7, Pydantic models, checked-in schema, source-policy loader, validator, renderer, contract tests |
| M1 First OSS vertical slice | **Done (first pass)** — `pigtail https://github.com/plausible/analytics` produces a valid bundle and forensic (≈3–4 min, ≈$1–3) |
| M2 Tally/general product slice | Not started |
| M3 Non-curated validation (~10 targets) | Not started |
| M4 Golden examples | Not started |
| M5 Product polish | Partly (report UX in progress) |
| M6 Website + GitHub launch | Not started |

## What exists

- `src/pigtail/bundle/` — models (Research Bundle 0.1.0), schema generator/check, reader, writer,
  validator implementing all 15 rejection rules of spec §21 plus §31 checks and the SHOULD-warn list.
- `schemas/research-bundle/0.1.0.schema.json` — generated from the models; a test fails if they drift.
- `source-policies/*.yaml` — github, hacker-news, web (enabled); reddit, product-hunt, x (link-only,
  disabled by default).
- `src/pigtail/providers/` — model interface + Anthropic adapter (structured outputs, metering,
  retries), discovery interface + Anthropic server-side web search adapter, GitHub adapter,
  Hacker News (Algolia) adapter, policy-obeying web fetcher (robots.txt, transient text only).
- `src/pigtail/research/` — target resolution, discovery + model triage (by index, so no invented
  URLs), claim extraction with **verbatim quote verification**, duplicate merging, two-step
  reconstruction (timeline, then interpretation) with deterministic checks (claim refs must exist,
  metric numbers must appear in cited claims, dates must agree with cited claims, causal attribution
  capped unless a first-party claim states the cause), narrative synthesis, conflicts, gaps,
  orchestrator.
- `src/pigtail/repository/` — star series utilities, growth-episode detection, release selection,
  launch clustering and post-launch windows (+24h/+48h/+7d/+30d/+90d).
- `src/pigtail/renderer/` — Jinja2 template, inline CSS/JS, star/event chart with event lanes,
  growth-episode bands, evidence drawer (claim → evidence → source → fetch/locator), dark mode.

## Tests

`uv run pytest` — 58 passing (contract, unit, integration). `uv run ruff check`, `uv run mypy`: clean.

## Deviations from the specification (with reasons)

1. **Star history source.** GitHub closed stargazer *lists* to non-collaborators on 2026-06-30
   (`/stargazers` now answers 403 "Resource not accessible by personal access token",
   `x-accepted-github-permissions: contents=write`; GraphQL `stargazers` is also FORBIDDEN).
   Pigtail uses `GET /repos/{o}/{r}/stargazers/history` (weekly buckets with daily counts, no
   identities). This works without a token, needs 1–5 requests per repository and covers repos of
   any size, so star history is `exact` (daily net counts of current stargazers). The spec's
   "40,000-star sampling" concern no longer applies. No contract change needed.
2. **Narrative blocks may be `null`.** Spec §20 shows every block as an object. Pigtail sets a block
   to `null` when the evidence cannot support it (PRD P5 "missing evidence is preferable"). The
   schema allows `null`; `key_takeaways` stays a (possibly empty) array. Proposed as a 0.1.x
   clarification.
3. **Config defaults.** `discovery.max_queries` defaults to 12 (spec example 25) and the default
   discovery provider is `anthropic_web_search`, so one `ANTHROPIC_API_KEY` covers both model and
   search. Provider choice remains OPEN / MEASURE FIRST per spec §53.
4. **Extra source policy files** `web.yaml` (generic pages) and `x.yaml` in addition to the four
   examples in spec §24. A `web` policy is required as the fallback for unknown hosts.
5. **Developer cache.** `PIGTAIL_DEV_CACHE=1` caches model responses under `.pigtail-cache/`
   (gitignored) for local iteration only. Off by default.

## Assumptions recorded

- License: MIT (not specified; owner to confirm).
- Default model `anthropic/claude-opus-5-5` (spec leaves the model open).
- The legal values in `source-policies/` are conservative engineering defaults, not legal
  conclusions; each file says so. Reddit, Product Hunt and X are link-only until reviewed.
- Hacker News stories that only mention the name in the title are confirmed by the model triage
  before use; stories older than the repository (−30 days) are dropped unless they link to the
  project's own domain.

## Known limitations (current)

- Product Hunt and Reddit are link-only: launches there appear only when a permitted source (e.g.
  the maker's blog) states them.
- Lone popular Hacker News posts (≥100 points) form "attention" launch episodes; for content-led
  projects (e.g. Plausible) that is many rows.
- Model cost per OSS run measured once: $2.90 uncached (Plausible, 54 sources, 350 claims).

## Next task

M1 polish → M2: run `pigtail tally.so`, compare with the Tally reference, fix generalizable gaps.

## Owner decisions needed

1. Confirm MIT license.
2. Confirm using the Anthropic web-search tool as the default discovery provider.
3. Legal review of `source-policies/web.yaml` defaults (transient fetch, ≤280-char excerpts).
4. Website domain (`pigtail.dev` is used as the schema `$id`; is it registered?).
