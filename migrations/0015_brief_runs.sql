-- M12 (PRD R18.4, R18.5, R18.6; D7): brief-run provenance, the stage cache that lets a re-run
-- reuse unchanged work, and the shortlist-decision table M13 fills (R4.7). Forward-only.
--
-- Brief *content* is never stored here: briefs live as files in PIGTAIL_DATA_DIR/briefs (R18.9).
-- These tables hold the brief id, version and the SHA-256 of its content, so a report can name
-- the exact version it came from.

-- One row per run of one brief version (R18.6 provenance; R18.5 estimate, spend and hard stop).
CREATE TABLE brief_runs (
    id                    text PRIMARY KEY CHECK (id ~ '^brun_[0-9a-f]{20}$'),
    brief_id              text NOT NULL,
    brief_version         integer NOT NULL CHECK (brief_version >= 1),
    brief_hash            text NOT NULL CHECK (brief_hash ~ '^[0-9a-f]{64}$'),
    run_id                text REFERENCES runs (id),  -- the generic run record, when one exists
    status                text NOT NULL CHECK (status IN (
                              'planned', 'running', 'paused_budget', 'succeeded', 'failed')),
    created_at            timestamptz NOT NULL DEFAULT now(),
    started_at            timestamptz,
    finished_at           timestamptz,
    data_version          text,
    code_commit           text,
    codebook_version      text,
    outcome_model_version text,
    prompt_versions       jsonb NOT NULL DEFAULT '{}',
    model_versions        jsonb NOT NULL DEFAULT '{}',
    estimate              jsonb,                      -- shown before the run (label: estimate)
    approved_paid         boolean NOT NULL DEFAULT false,
    spend                 jsonb NOT NULL DEFAULT '{}',
    stop                  jsonb,                      -- the BudgetStop that paused the run
    checkpoint            jsonb,                      -- where a resumed run continues
    resumed_from          text REFERENCES brief_runs (id),
    stage_keys            jsonb NOT NULL DEFAULT '{}',  -- stage -> fingerprint (cache.py)
    rerun_plan            jsonb,                      -- what the edit since the last run affects
    reuse                 jsonb NOT NULL DEFAULT '{}'   -- stage -> {reused, recomputed}
);
CREATE INDEX brief_runs_brief_idx ON brief_runs (brief_id, brief_version, created_at DESC);

-- Cached stage results, keyed by what they were computed from (cache.py `item_key`). A re-run
-- that computes the same key reuses the row instead of recomputing (R18.4). `repo_id` links a
-- per-repo item to its repo so a repo opt-out deletes it (CB-13c).
CREATE TABLE brief_stage_cache (
    key                 text PRIMARY KEY CHECK (key ~ '^[0-9a-f]{64}$'),
    stage               text NOT NULL,
    stage_version       text NOT NULL,
    item_ref            text NOT NULL,
    repo_id             text,
    input_hash          text NOT NULL,
    result              jsonb NOT NULL,
    result_hash         text NOT NULL CHECK (result_hash ~ '^[0-9a-f]{64}$'),
    created_at          timestamptz NOT NULL DEFAULT now(),
    first_brief_id      text NOT NULL,
    first_brief_version integer NOT NULL,
    first_brief_run_id  text REFERENCES brief_runs (id) ON DELETE SET NULL,
    hits                integer NOT NULL DEFAULT 0,
    last_hit_at         timestamptz
);
CREATE INDEX brief_stage_cache_stage_idx ON brief_stage_cache (stage, item_ref);
CREATE INDEX brief_stage_cache_repo_idx ON brief_stage_cache (repo_id) WHERE repo_id IS NOT NULL;

-- Shortlist review decisions (R4.7). Stub: M13 writes it. Rows are never edited (a changed mind
-- is a new decision); they can be deleted only by privacy operations (repo opt-out, CB-13c).
CREATE TABLE shortlist_decision (
    id                bigserial PRIMARY KEY,
    brief_id          text NOT NULL,
    brief_version     integer NOT NULL CHECK (brief_version >= 1),
    brief_run_id      text REFERENCES brief_runs (id) ON DELETE SET NULL,
    candidate_ref     text NOT NULL,          -- discovery's candidate key
    candidate_repo_id text,                   -- repos.id when the candidate is a known repo
    decision          text NOT NULL CHECK (decision IN ('accept', 'reject', 'add')),
    reason            text NOT NULL CHECK (length(btrim(reason)) > 0),
    reviewer_role     text NOT NULL CHECK (reviewer_role IN ('user', 'owner', 'verifier')),
    decided_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX shortlist_decision_brief_idx ON shortlist_decision (brief_id, brief_version);

CREATE FUNCTION shortlist_decision_no_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'shortlist_decision rows are immutable (% not allowed)', TG_OP;
END;
$$;
CREATE TRIGGER shortlist_decision_no_update
    BEFORE UPDATE ON shortlist_decision
    FOR EACH ROW EXECUTE FUNCTION shortlist_decision_no_update();

-- D7 brief writes from the web app are audited (event only: no brief content, no id).
ALTER TABLE ui_audit_log DROP CONSTRAINT ui_audit_log_event_check;
ALTER TABLE ui_audit_log ADD CONSTRAINT ui_audit_log_event_check CHECK (event IN (
    'login_success', 'login_failure', 'login_rate_limited', 'logout',
    'snapshot_view', 'snapshot_gone', 'snapshot_missing', 'snapshot_integrity_failure',
    'brief_create', 'brief_version'));
