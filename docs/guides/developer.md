# Developer guide

## Setup
```bash
uv sync                      # Python 3.12 + dev tools, from uv.lock
uv run pre-commit install    # ruff, gitleaks, private-data scan, mypy
cp .env.example .env         # never commit .env
docker compose up -d --wait db objectstore && docker compose run --rm objectstore-init
```

## Checks (the same ones CI runs)
```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy                  # strict on src/
uv run pytest -q             # unit tests; smoke tests skip by default
python3 scripts/private_data_scan.py
```
Real-backend smoke tests: `PIGTAIL_RUN_SMOKE=1 uv run pytest tests/smoke` (subscription, uses your own Claude plan). The api smoke test runs automatically when `ANTHROPIC_API_KEY` is set.

## LLM calls (PRD F15)
Every product LLM call goes through `pigtail.llm.LLMClient`. Never call the SDK or the CLI directly.
```python
from pydantic import BaseModel
from pigtail.llm import PromptSpec, build_client


class Out(BaseModel):
    label: str


PROMPT = PromptSpec(id="classify-event", version="1", system="…", template="Classify: {input}")
res = build_client().complete(PROMPT, text, Out, job="pilot_extraction")
res.output, res.provenance()  # store the provenance with every coded record (R7.4)
```
- Bump `PromptSpec.version` whenever the prompt text changes. The cache is keyed on it (ADR-006).
- `job` names route to a backend through `LLM_BACKEND_OVERRIDES` (e.g. `tier2_extraction:api`).
- `UsageLimitReached` pauses the backend; later calls raise `QueuePaused(until)` until the reset. Job runners must catch `QueuePaused`, sleep until `until`, and resume (R15.5).
- Inputs are stripped of e-mails, phone numbers, @handles, profile URLs (`github.com/<login>`,
  `bsky.app/profile/…`, `news.ycombinator.com/user?id=…`) and `did:plc:`/`did:web:` ids before
  they leave the process (DPIA CB-06). Pass `namespace="github"` (the source's pseudonym
  namespace) so @mentions get the same pseudonyms the connector stores, and `evidence_id=` so the
  cache row is purged with its source (CB-05).

## Direction since M11 (ADR-047, ADR-048, ADR-049)
pigtail is brief-driven: a user's research brief defines a neighbourhood, and runs are batch runs
on the operator's own machine plus launch mode. There is no global collection any more. M11
removed the 50k-repo watch list (`capture/github_watch.py`), the all-GitHub search sweeps and
screens (`capture/github_screens.py`), the GH Archive velocity scan and its backfill
(`capture/velocity.py`, `capture/gharchive_backfill.py`), detection v1
(`capture/detection_v1.py`), the held-out split and H-sealed guard (`pigtail.analysis.split`)
and the settle-lag collection (`capture/settle_lag.py`), with their CLI commands, scheduler jobs
and tables (migration 0014). They are in the git tag `archive/global-collection`; don't bring
them back without an ADR. New code is scoped to a brief or a tracked project: take the repos or
queries as input, never enumerate GitHub. The full rewrite of this guide comes with M12/M14.

## Briefs (M12; PRD F18, D7)
`pigtail.briefs`: `model` (pydantic model behind `schemas/brief/v1.1.json`; regenerate the
file with `uv run pigtail brief schema > schemas/brief/v1.1.json`, a test fails on drift;
`schemas/brief/v1.json` is frozen, and `migrate_v1` maps v1 briefs onto v1.1 when they load),
`store` (private, immutable versions under `PIGTAIL_DATA_DIR/briefs`; stale edits are refused),
`diff`, `estimate` (`estimate-v1` planning model), `expansion` (R18.7: the versioned
`brief_expansion` prompt, `propose_expansion` and `apply_expansion`; tests use the fake backend
from `tests/conftest.py`), `budget` and `cache`. Stages built from M13 on must:
- call `BudgetGuard.check_llm` before each LLM call or chunk, `check_paid`/`charge_paid` around
  any paid request and `check_backend` before routing a job; on `BudgetStop`, write a checkpoint
  with `BriefRun.pause_for_budget` and exit cleanly (R18.5, ADR-053.1);
- look results up through `StageCache.get_or_compute` with `item_key(...)` for per-item work
  (relevance per candidate, evidence per repo, extraction per case) or the run's `stage_keys`
  for whole-stage outputs, and declare in `cache.STAGE_INPUTS` every brief field the stage reads
  (a stage that reads an undeclared field would be wrongly reused; bump `STAGE_VERSIONS` when a
  stage's logic changes) (R18.4);
- record provenance through `BriefRuns.create` (R18.6).
Tests use synthetic briefs only (the example); never a real brief.

## Capture layer (M1)
```bash
uv run pigtail db migrate                       # forward-only SQL in migrations/, tracked in schema_migrations
uv run pigtail scheduler run --once             # one batch run of the jobs in infra/schedule.toml
```
- Records follow `schemas/v0/*.schema.json`; `pigtail.capture.models` mirrors them (a test checks both).
- Connectors subclass `pigtail.connectors.base.Connector`: declare `terms`, rate limit, `handle_fields`;
  implement `_parse()`. `fetch()` snapshots raw bytes (content-addressed, `SNAPSHOT_BACKEND=local|s3`)
  and writes `evidence` before anything is parsed; `records()` always pseudonymizes the handle fields.
  `pigtail.capture.replay.replay()` re-parses a stored snapshot through the same path.
- Wrap jobs in `RunRecorder` so each run writes a `run` record. Its error text is scrubbed of
  identifiers and truncated (CB-18); use `pigtail.logsafe.configure_logging()` for log output.
- Connectors take `suppression=pigtail.privacy.suppression.load(db)` and drop opted-out people
  and repos in `records()` (CB-13). Declare `repo_fields` for numeric repo ids.
- Person-level connectors set `person_level_hold = True` (ADR-022): enabling them raises
  `PersonSourceHold` unless `PIGTAIL_ADR022_PERSON_SOURCES_OK=1`. A group flag (`enable_env`, e.g.
  `PIGTAIL_ENABLE_HN`) can enable a family of connectors; the per-connector flag wins.
- Person-level captures register the upstream items in each snapshot with
  `pigtail.privacy.deletion_sync.track_items()`, so deletion sync (CB-02) can drop the snapshot when
  an item is deleted upstream. New platforms implement a `DeletionSource` (poll or push).
- Tables holding pseudonymous person-level rows must be registered in
  `pigtail.privacy.deletion.PERSON_TABLES` so retention (CB-01) and erasure (CB-08) reach them.
  Operator commands: docs/guides/operator.md "Privacy operations".
- A table with a shorter cap than 24 months sets `PersonTable.retention_days` and
  `retention_class` (e.g. `repo_event_actor`: 30 days, `person_level_30d`, CB-22).
- **GitHub (M1-T24, ADR-032; per-repo since M11).** `pigtail.connectors.github` has two
  connectors on one HTTP layer (`GitHubAPI`): `github` (project-level: GraphQL, Search, star
  history) and `github_events` (person-level per-repo events; off, ADR-022 hold). The layer adds
  the token (`GITHUB_TOKEN`, `MissingToken` without it), per-resource rate-limit buckets,
  primary/secondary rate-limit handling, conditional requests (`fetch_conditional`, ETag cache
  `github_http_cache`) and budget hard stops (`pigtail.connectors.github_budget`: hourly caps
  from a shared ledger, job caps, server reserve). `Connector.requires_env` makes scheduler jobs
  skip with `missing_env:<VAR>`. Capture code: `capture/star_history.py` (endpoint day labels,
  never converted to UTC; `due_case_repos` for `star-history --cases`),
  `capture/github_search.py` (`search_repos`: pages one caller-supplied query under the
  1,000-result cap and drops each page's raw bytes after parsing, for brief-scoped discovery in
  M13), `capture/repo_events.py` (events polling for live cases, aggregate-only bot filter), CLI
  in `capture/github_cli.py`. Tests run against `tests/github_fake.py` (httpx `MockTransport`):
  no network and no real token in tests. `repo_event_actor` may only be read in aggregate
  (`tests/unit/test_github_privacy_m1t24.py` enforces this); never add a function, command or
  API path that lists a repo's stargazers.
- **GH Archive connector.** `connectors/gharchive.py` is kept, unused by any job, for
  brief-restricted discovery signals in M13 (ADR-047.1; since mid-2025 the dumps are nearly
  push-events only, so stars and forks come from the GitHub API, ADR-047.8). Raw dumps stay
  under `GHARCHIVE_RAW_RETENTION_DAYS`.
- **Drop at parse (CB-23b, CB-24).** Person-level pages that are only parsed for project-level
  fields (HN rank items, GitHub search pages, per-repo events) are dropped with
  `pigtail.privacy.deletion.drop_after_parse` right after parsing. A page that fails to parse is
  dropped at once with `drop_unparseable` (catch `PARSE_ERRORS`); connectors that parse inside a
  fetch method call `Connector.parse_failed(f, e)`, which hands the page to the
  `parse_failure_sink` the capture job set (`unparseable_sink(...)`) and returns `ParseFailed`.
  Record failures as counts only (`<source>.parse_failed.<Type>`), never the exception message.
- **Repo opt-outs by name (M1-T23, CB-13b).** Besides `<host>:<id>`, the refusal list holds
  keyed `repo_name_key(owner/name, pz)` hashes (`rk_…`: HMAC-SHA256 with `PSEUDONYM_KEY`,
  namespace `repo_name`). Check `Suppressions.name_suppressed(full_name)` wherever a repo is known
  only by name (HN stories, mentions, search results). `suppression.load(db, pz)` binds
  the key (default: `PSEUDONYM_KEY` from the environment) and raises `MissingNameKey` when keyed
  entries exist but no key does. Never write an unkeyed hash: `legacy_repo_name_key` exists only
  to match and convert rows from before migration 0009 (`repo_name_unkeyed`), and the database
  refuses new ones.
- Tests marked `db` / `s3` use the compose services and skip if they are unreachable
  (`PIGTAIL_REQUIRE_DB=1` / `PIGTAIL_REQUIRE_S3=1` make them fail instead). CI sets both and
  starts the compose `objectstore` (SeaweedFS) and `objectstore-init` services for the python
  job (M1-T20).

## Scheduler and launch mode (M1-T21; ADR-048, ADR-049.1)
`pigtail scheduler run --once` is a batch run: every due job plus the jobs with
`run_at_start = true`, then exit. A job with `launch_mode_only = true` is otherwise due only while
`pigtail.scheduler.launch_mode` says launch mode is on (an active row in `launch_mode_window`, a
stub M14 fills, or `PIGTAIL_LAUNCH_MODE=1`). The HN rank poller uses both flags; there is no
always-on job. Job kinds are `command` and `hn_mentions`.

## Burst segmentation and analysis parameters (M11)
`pigtail.analysis.bursts` derives bursts and quiet intervals from one repo's star-history days
(codebook v0.3.0 §3.2-3.3, outcome model v2 §2.1): pure functions (`segment`, `evaluate_day`,
`onset`, `burst_end`, `daily_baseline`), no database and no global scan. Its parameters, and the
StarScout fake-star parameters, are versioned in `pigtail.analysis.params`
(`PARAMS_VERSION`), which mirrors `schemas/analysis-params/v1.0.0.json`
(`tests/unit/test_analysis_bursts_m11.py` keeps the two equal). Changing a value means a new
version of both, logged as an ADR. There is no held-out split any more (ADR-049.5).

## JSONL export (M1-T20)
`pigtail.export.jsonl`: one file per table. It fails closed on tables missing from
`TABLE_LEVELS` (`project` / `person` / `never`). **Every new migration that creates a table must
classify it** (`tests/integration/test_export_jsonl_m1t20.py` checks this). Person-level tables
are `person` or `never`; anything that would list a repo's stargazers is `never`.

## External liveness (M1-T26)
`pigtail.scheduler.liveness`: the scheduler calls `write_heartbeat` each tick through the
`Scheduler(heartbeat=...)` hook, and `pigtail health --liveness-file PATH|-` checks it.
Operator setup: docs/guides/operator.md, "External liveness check".

## Web app (M1-T12, D1 preview)
- API: `src/pigtail/api/` (FastAPI). Queries in `queries.py` run on a pool whose sessions are
  read-only at the server (`default_transaction_read_only=on`); only `ui_sessions` and
  `ui_audit_log` are written, through a second pool. Every number returned carries the evidence
  id(s) it came from (R13.2); source text goes through `guard_text()`; never return handles or
  person-level fields (HN `author`).
- UI: `ui/` (TypeScript strict, React, Vite, pnpm; no component library, hand-rolled SVG charts).
  ```bash
  pnpm --dir ui install
  pnpm --dir ui dev          # Vite on 127.0.0.1:5173, proxies /api to `pigtail ui serve` on :8080
  pnpm --dir ui lint && pnpm --dir ui typecheck && pnpm --dir ui test && pnpm --dir ui build
  ```
- Case detail returns an older case's `detection` block as recorded; the hourly data behind it
  (`detection_hours`) was dropped in M11. The timeline's `github` lane is the repo's star-history
  days (`repo_star_daily`: `stars_net`, `is_partial`, the page's evidence id), daily whatever
  the bucket.
- Tests: `tests/integration/test_api_d1.py` (seeded synthetic DB: auth, audit, 410s, hash
  verification, no handles in JSON, 5,000-item budget), `tests/unit/test_ui_auth.py`,
  `ui/src/ui.test.tsx`.

## Public-repo rules
Fixtures are synthetic or pseudonymized and must be listed in `tests/fixtures/MANIFEST.md`. No raw records, snapshots, handles or secrets in git, including commit messages.
