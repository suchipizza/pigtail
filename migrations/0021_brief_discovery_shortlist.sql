-- M22 (PRD R4.5-R4.7, R4.10, R4.11, R19.1; D7): candidate discovery, the LLM relevance filter
-- and the shortlist review of a brief version, and the stage runner's statuses. Forward-only.
--
-- Project-level only: repo ids and names, public repo metadata (descriptions pass through the
-- keyless alias redaction first), discovery signals, verdicts with their provenance, and
-- review decisions. No handles, no person-level content. Named reference cases and
-- distribution exemplars are referenced by their position in the brief (`named:<panel>:<i>`),
-- never by name, so no brief content is stored here (R18.9); the brief file holds the names.

-- Stage runner (R19.1): a run that waits for a Message Batch, and a run whose shortlist awaits
-- the user's review, are neither running nor finished.
ALTER TABLE brief_runs DROP CONSTRAINT brief_runs_status_check;
ALTER TABLE brief_runs ADD CONSTRAINT brief_runs_status_check CHECK (status IN (
    'planned', 'running', 'waiting_batch', 'paused_budget', 'awaiting_review', 'succeeded',
    'failed'));
ALTER TABLE brief_runs ADD COLUMN stages jsonb NOT NULL DEFAULT '{}';  -- stage -> status, counts
ALTER TABLE brief_runs ADD COLUMN incremental boolean NOT NULL DEFAULT false;
ALTER TABLE brief_runs ADD COLUMN resumes integer NOT NULL DEFAULT 0 CHECK (resumes >= 0);

-- One row per candidate of a brief version (R4.5 discovery, R4.6 relevance verdict).
CREATE TABLE brief_candidate (
    brief_id            text NOT NULL,
    brief_version       integer NOT NULL CHECK (brief_version >= 1),
    candidate_ref       text NOT NULL CHECK (
                            candidate_ref ~ '^gh:[a-z0-9][a-z0-9-]*/[a-z0-9._-]+$'
                            OR candidate_ref ~ '^named:(reference|exemplar):[0-9]{1,2}$'),
    repo_full_name      text CHECK (repo_full_name = lower(repo_full_name)),  -- owner/name
    repo_host_id        bigint,
    repo_id             text,                       -- repos.id when known
    panel               text NOT NULL DEFAULT 'field'
                            CHECK (panel IN ('field', 'exemplar', 'reference')),
    named_index         integer CHECK (named_index >= 0),  -- position in the brief's list
    resolution          text NOT NULL DEFAULT 'resolved'
                            CHECK (resolution IN ('resolved', 'unresolved', 'confirmed')),
    resolution_rule     text,                       -- brief_repo | brief_url | launch_link | ...
    matches             jsonb NOT NULL DEFAULT '[]',  -- unresolved: candidate repos to confirm
    sources             jsonb NOT NULL DEFAULT '[]',  -- [{source, ...signal}], de-duplicated
    metadata            jsonb NOT NULL DEFAULT '{}',  -- project-level public repo metadata
    first_seen_at       timestamptz NOT NULL DEFAULT now(),
    last_seen_at        timestamptz NOT NULL DEFAULT now(),
    first_brief_run_id  text REFERENCES brief_runs (id) ON DELETE SET NULL,
    -- relevance filter (R4.6): verdict, reason (<= 30 words), distance from the core field
    -- (0 core, 1-2 widening steps, R4.10), panel the model placed it in, and provenance
    verdict             text CHECK (verdict IN ('relevant', 'not_relevant', 'uncertain')),
    reason              text CHECK (reason IS NULL OR length(reason) <= 400),
    distance            smallint CHECK (distance IN (0, 1, 2)),
    model_panel         text CHECK (model_panel IN ('field', 'exemplar', 'reference')),
    rubric_version      text,
    relevance           jsonb,                      -- prompt, model, batch id, input hash, ...
    judged_at           timestamptz,
    judged_brief_run_id text REFERENCES brief_runs (id) ON DELETE SET NULL,
    CHECK ((candidate_ref LIKE 'gh:%') = (repo_full_name IS NOT NULL)),
    CHECK (candidate_ref NOT LIKE 'gh:%' OR candidate_ref = 'gh:' || repo_full_name),
    PRIMARY KEY (brief_id, brief_version, candidate_ref)
);
CREATE INDEX brief_candidate_repo_idx ON brief_candidate (repo_id) WHERE repo_id IS NOT NULL;
CREATE INDEX brief_candidate_name_idx ON brief_candidate (repo_full_name)
    WHERE repo_full_name IS NOT NULL;

-- The shortlist of a brief version (R4.7): in review until the user finalizes it.
CREATE TABLE brief_shortlist (
    brief_id      text NOT NULL,
    brief_version integer NOT NULL CHECK (brief_version >= 1),
    status        text NOT NULL CHECK (status IN ('in_review', 'final')),
    created_at    timestamptz NOT NULL DEFAULT now(),
    brief_run_id  text REFERENCES brief_runs (id) ON DELETE SET NULL,
    finalized_at  timestamptz,
    finalized_role text CHECK (finalized_role IN ('user', 'owner', 'verifier')),
    finalized_via text CHECK (finalized_via IN ('cli', 'ui')),
    precision     jsonb,                            -- R4.7 precision at finalization
    PRIMARY KEY (brief_id, brief_version),
    CHECK ((status = 'final') = (finalized_at IS NOT NULL))
);

-- Decisions record where they were made (the operator's CLI or the web app).
ALTER TABLE shortlist_decision ADD COLUMN via text CHECK (via IN ('cli', 'ui'));
CREATE INDEX shortlist_decision_candidate_idx
    ON shortlist_decision (brief_id, brief_version, candidate_ref, decided_at DESC, id DESC);

-- Shortlist review writes from the web app are audited (event only: no names, no reasons).
ALTER TABLE ui_audit_log DROP CONSTRAINT ui_audit_log_event_check;
ALTER TABLE ui_audit_log ADD CONSTRAINT ui_audit_log_event_check CHECK (event IN (
    'login_success', 'login_failure', 'login_rate_limited', 'logout',
    'snapshot_view', 'snapshot_gone', 'snapshot_missing', 'snapshot_integrity_failure',
    'brief_create', 'brief_version', 'brief_expansion', 'shortlist_decision',
    'shortlist_finalize'));
