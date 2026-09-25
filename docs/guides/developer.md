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

## Capture layer (M1)
```bash
uv run pigtail db migrate                       # forward-only SQL in migrations/, tracked in schema_migrations
PSEUDONYM_KEY=… uv run pigtail capture scan --start 2026-09-20T00 --end 2026-09-21T00
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
- Tests marked `db` / `s3` use the compose services and skip if they are unreachable
  (`PIGTAIL_REQUIRE_DB=1` makes them fail instead; CI sets it).

## Public-repo rules
Fixtures are synthetic or pseudonymized and must be listed in `tests/fixtures/MANIFEST.md`. No raw records, snapshots, handles or secrets in git, including commit messages.
