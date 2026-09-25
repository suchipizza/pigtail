-- M1-T1: capture schema v0 (PRD §7, R1.4). Mirrors schemas/v0/*.schema.json.
-- Forward-only: never edit an applied migration; add a new file instead.

CREATE TABLE repos (
    id             text PRIMARY KEY,               -- '<host>:<host_id>'
    schema_version text NOT NULL DEFAULT 'v0',
    host           text NOT NULL,
    host_id        bigint NOT NULL,
    full_name      text NOT NULL,
    first_seen_at  timestamptz NOT NULL,
    created_at     timestamptz,
    updated_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (host, host_id)
);

CREATE TABLE runs (
    id              text PRIMARY KEY,
    schema_version  text NOT NULL DEFAULT 'v0',
    job             text NOT NULL,
    started_at      timestamptz NOT NULL,
    finished_at     timestamptz,
    status          text NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    code_commit     text,
    config          jsonb NOT NULL DEFAULT '{}',
    counts          jsonb NOT NULL DEFAULT '{}',
    prompt_versions jsonb,
    model_versions  jsonb,
    error           text
);
CREATE INDEX runs_job_started_idx ON runs (job, started_at);

CREATE TABLE cases (
    id             text PRIMARY KEY,
    schema_version text NOT NULL DEFAULT 'v0',
    repo_id        text NOT NULL REFERENCES repos (id),
    opened_at      timestamptz NOT NULL,
    closed_at      timestamptz,
    trigger        text NOT NULL CHECK (trigger IN ('velocity', 'announced', 'manual', 'analyze')),
    status         text NOT NULL CHECK (status IN ('live', 'pre_launch', 'closed')),
    run_id         text REFERENCES runs (id),
    detection      jsonb,
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (repo_id, trigger, opened_at)
);
CREATE INDEX cases_repo_idx ON cases (repo_id, opened_at);

CREATE TABLE evidence (
    id                text PRIMARY KEY,
    schema_version    text NOT NULL DEFAULT 'v0',
    source            text NOT NULL,
    url               text NOT NULL,
    fetched_at        timestamptz NOT NULL,
    content_hash      text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    snapshot_ref      text NOT NULL,
    content_type      text,
    http_status       integer,
    reliability       text NOT NULL CHECK (reliability IN ('high', 'medium', 'low', 'unknown')),
    terms_basis       text NOT NULL,
    retention_class   text NOT NULL
        CHECK (retention_class IN ('person_level_24m', 'project_level', 'derived_aggregate')),
    deletion_state    text NOT NULL DEFAULT 'present'
        CHECK (deletion_state IN ('present', 'deleted_upstream', 'raw_dropped')),
    collector_version text NOT NULL,
    case_id           text REFERENCES cases (id),
    repo_id           text REFERENCES repos (id),
    run_id            text REFERENCES runs (id)
);
CREATE INDEX evidence_hash_idx ON evidence (content_hash);
CREATE INDEX evidence_case_idx ON evidence (case_id);
CREATE INDEX evidence_source_fetched_idx ON evidence (source, fetched_at);
