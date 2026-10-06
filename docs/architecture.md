# Architecture (for contributors)

Pigtail is a local Python application. There is no server, database, queue or container. One
command runs a pipeline and writes files.

```text
                 ┌────────────────────────── research engine ──────────────────────────┐
target  ──►  resolve ─► repository history ─► discover ─► fetch ─► extract ─► reconstruct ─► gaps
                 └────────────────────────────────────┬──────────────────────────────────┘
                                                      ▼
                                          Research Bundle (JSON)  ──►  validator
                                                      ▼
                                             renderer ──► report.html
```

The renderer reads **only** the bundle. Nothing in the report comes from engine internals, which is
why `pigtail render` works on any valid bundle without credentials.

## Package layout

| Path | Responsibility |
|---|---|
| `src/pigtail/cli.py` | Typer CLI: `analyze` (and the `pigtail <target>` shorthand), `render`, `validate`, `doctor`, `version`. |
| `src/pigtail/config.py` | TOML config lookup and validation; secrets only by env-var name. |
| `src/pigtail/domain/` | UUIDv7, time ranges (precision never invented), confidence vocabularies, object refs. |
| `src/pigtail/bundle/` | Pydantic models of Research Bundle 0.1.0, schema generation, reader/writer, validator, version check. |
| `src/pigtail/policies/` | Source-policy file model and loader; URL → platform mapping. |
| `src/pigtail/providers/` | Everything that talks to the outside world: model (`models/anthropic.py`), web search (`search/`), GitHub (`github/`), Hacker News (`community/`), web pages (`fetchers/`). Each adapter meters requests and cost. |
| `src/pigtail/research/` | The pipeline: target resolution, discovery and triage, first-party feeds/sitemaps, claim extraction, normalization, reconstruction, synthesis, conflicts, gaps, repository analysis wiring, and the orchestrator. `builder.py` is the only place bundle objects are created. |
| `src/pigtail/repository/` | Pure functions for star series, growth-episode detection, release selection, launch clustering and post-launch windows. |
| `src/pigtail/renderer/` | View model + Jinja2 template + inline CSS/JS. The report has no external requests. |
| `schemas/research-bundle/0.1.0.schema.json` | The normative JSON Schema, generated from the models. CI fails if they drift. |
| `source-policies/*.yaml` | Per-platform access/retention/display rules. |
| `src/pigtail/publication/` | The publication gate for Pigtail-hosted reports (internal; see [publication-gate.md](publication-gate.md)). `pigtail analyze` does not use it. |
| `examples/reviewed/` | Published examples: gate output only (report, public projection, manifest, metadata) plus `NOTICE.md`. Used by tests and the website. |
| `site/` | The static Next.js website. Its build refuses any example without a current PASS manifest. |

## Provider interfaces

The research core depends on small protocols in `providers/base.py`:

- `ModelProvider.structured(request) -> PydanticModel` — schema-constrained output.
- `SearchProvider.search(query)` / `search_many(queries, context)`.
- GitHub, Hacker News and the web fetcher are concrete adapters with the same metering.

Adding a model or search provider means adding one adapter and one line in its registry. The
default discovery provider (Anthropic's web-search tool) was chosen so one API key covers both
model and search; the choice is open for measurement (spec §53).

## Guardrails in code (not only in prompts)

| Guardrail | Where |
|---|---|
| Quotes must be found in the page text | `research/extraction.py` (`quote_in_text`) |
| Model may only cite known claim refs | `research/reconstruction.py` (`apply_reconstruction`) |
| Metric numbers must appear in cited claims | `reconstruction.number_supported` |
| Dates must agree with cited claims | `reconstruction.consistent_with_claims` |
| "Company-attributed" needs a first-party attribution claim | `apply_reconstruction` |
| Growth-episode association is timing-only | `research/repository_analysis.py` |
| Search URLs come from tool results, not model text | `providers/search/anthropic_web_search.py` |
| Triage selects by index, cannot add URLs | `research/discovery.py` |
| Policies decide fetch and excerpt length | `providers/fetchers/web.py`, `policies/models.py` |
| Invalid bundles are never written as `research-bundle.json` | `bundle/writer.py` |

## Tests

```bash
uv sync --all-groups
uv run pytest            # unit, contract, integration, golden
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy
uv run python -m pigtail.bundle.schema --check
```

Tests never call paid APIs. Golden tests check structure and known facts of the reviewed
examples, not generated prose.

## What is deliberately not here

The specification also freezes contracts for a future private platform (canonical Postgres,
review workflow, Neo4j projection, API, MCP). None of that is built in this repository; the
Research Bundle is designed so it can be imported there later without scraping HTML.
