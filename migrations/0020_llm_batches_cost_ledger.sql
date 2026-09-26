-- M21b (PRD R15.8-R15.11, R18.5; Directive §6.3-6.4, ADR-064.3-4): Message Batches API state,
-- resumable by batch id, and the actual-cost ledger per brief run (cost per case, M23).
-- Forward-only. No prompt text, model output or evidence content is stored here: batch rows
-- hold ids, hashes, counts and statuses; outputs go to the LLM result cache.

-- One row per submitted batch. A paused or restarted run finds its open batches here and
-- collects them instead of submitting (and paying for) the same work again.
CREATE TABLE llm_batches (
    batch_id           text PRIMARY KEY CHECK (length(batch_id) BETWEEN 1 AND 200),
    backend            text NOT NULL CHECK (backend = 'api'),
    job                text NOT NULL,
    stage              text NOT NULL CHECK (stage IN ('relevance', 'extraction', 'synthesis')),
    model              text NOT NULL,
    prompt_id          text NOT NULL,
    prompt_version     text NOT NULL,
    prompt_fingerprint text NOT NULL,
    schema_hash        text NOT NULL,
    brief_run_id       text REFERENCES brief_runs (id) ON DELETE SET NULL,
    status             text NOT NULL CHECK (status IN (
                           'submitted', 'ended', 'collected', 'failed', 'canceled')),
    requests           integer NOT NULL CHECK (requests >= 1),
    est_usd            numeric(12, 6),                 -- the estimate checked before submitting
    counts             jsonb NOT NULL DEFAULT '{}',    -- succeeded / errored / canceled / expired
    submitted_at       timestamptz NOT NULL DEFAULT now(),
    ended_at           timestamptz,
    collected_at       timestamptz
);
CREATE INDEX llm_batches_open_idx ON llm_batches (brief_run_id, status)
    WHERE status IN ('submitted', 'ended');

-- One row per request in a batch: which cache key it fills and how it ended.
CREATE TABLE llm_batch_requests (
    batch_id     text NOT NULL REFERENCES llm_batches (batch_id) ON DELETE CASCADE,
    custom_id    text NOT NULL CHECK (custom_id ~ '^[A-Za-z0-9_-]{1,64}$'),
    cache_key    text NOT NULL,
    input_hash   text NOT NULL CHECK (input_hash ~ '^[0-9a-f]{64}$'),
    evidence_id  text,
    case_ref     text,
    status       text NOT NULL DEFAULT 'pending' CHECK (status IN (
                     'pending', 'succeeded', 'invalid_output', 'errored', 'canceled', 'expired')),
    error_type   text,                                  -- API error type only, never content
    PRIMARY KEY (batch_id, custom_id)
);

-- Actual cost per model call of a brief run (R15.11; the pilot's cost per case, M23): tokens
-- in and out, prompt-cache writes and reads, batch id, list-price USD. Cache hits of pigtail's
-- own result cache cost nothing and are not recorded.
CREATE TABLE llm_cost_ledger (
    id                 bigserial PRIMARY KEY,
    created_at         timestamptz NOT NULL DEFAULT now(),
    brief_id           text,
    brief_run_id       text REFERENCES brief_runs (id) ON DELETE SET NULL,
    case_ref           text,
    job                text NOT NULL,
    stage              text,
    backend            text NOT NULL CHECK (backend IN ('subscription', 'api')),
    model              text NOT NULL,
    prompt_id          text NOT NULL,
    prompt_version     text NOT NULL,
    batch_id           text,
    status             text NOT NULL,
    input_tokens       integer NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
    output_tokens      integer NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
    cache_write_tokens integer NOT NULL DEFAULT 0 CHECK (cache_write_tokens >= 0),
    cache_read_tokens  integer NOT NULL DEFAULT 0 CHECK (cache_read_tokens >= 0),
    cost_usd           numeric(12, 6) NOT NULL DEFAULT 0 CHECK (cost_usd >= 0),
    prices_as_of       text
);
CREATE INDEX llm_cost_ledger_run_idx ON llm_cost_ledger (brief_run_id, case_ref);
CREATE INDEX llm_cost_ledger_brief_idx ON llm_cost_ledger (brief_id, created_at);
CREATE INDEX llm_cost_ledger_month_idx ON llm_cost_ledger (backend, created_at);
