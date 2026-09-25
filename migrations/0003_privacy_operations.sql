-- DPIA CB-01 / CB-08 / CB-13: privacy operations (retention purge, data-subject requests,
-- refusal list). Forward-only: never edit an applied migration; add a new file instead.
-- None of these tables may hold a raw handle, e-mail or content.

-- CB-13: refusal list. Pseudonyms only (handles are pseudonymized with PSEUDONYM_KEY before
-- they reach the database), plus repo ids of projects whose owner opted out.
CREATE TABLE privacy_suppression (
    kind       text NOT NULL CHECK (kind IN ('pseudonym', 'repo')),
    value      text NOT NULL,
    platform   text NOT NULL,
    reason     text NOT NULL CHECK (reason IN ('objection', 'erasure')),
    request_id text,
    added_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (kind, value),
    CHECK (kind <> 'pseudonym' OR value ~ '^p_[0-9a-f]{16}$'),
    CHECK (kind <> 'repo' OR value ~ '^[a-z]+:[0-9]+$')
);

-- CB-08: data-subject request log. No handle and no pseudonym: the request id links to the
-- suppression entry (if any); the export file lives outside the database.
CREATE TABLE privacy_requests (
    id           text PRIMARY KEY CHECK (id ~ '^dsr_[0-9a-f]{32}$'),
    type         text NOT NULL CHECK (type IN ('access', 'erasure', 'objection')),
    platform     text NOT NULL,
    received_at  timestamptz NOT NULL,
    completed_at timestamptz,
    outcome      text NOT NULL CHECK (outcome IN ('pending', 'completed', 'no_data', 'failed')),
    counts       jsonb NOT NULL DEFAULT '{}',
    run_id       text REFERENCES runs (id)
);
CREATE INDEX privacy_requests_received_idx ON privacy_requests (received_at);

-- CB-01 / CB-08 / CB-17: append-only deletion log (tombstones). Holds hashes and ids, never
-- content, so deletions can be re-applied after a backup restore.
CREATE TABLE deletion_log (
    id            bigserial PRIMARY KEY,
    logged_at     timestamptz NOT NULL DEFAULT now(),
    reason        text NOT NULL
        CHECK (reason IN ('retention', 'erasure', 'objection', 'deleted_upstream')),
    action        text NOT NULL
        CHECK (action IN ('raw_dropped', 'rows_deleted', 'cache_purged', 'evidence_deleted',
                          'error_text_cleared')),
    target        text NOT NULL,   -- 'snapshot', a table name, 'llm_cache', 'runs.error'
    content_hash  text CHECK (content_hash IS NULL OR content_hash ~ '^[0-9a-f]{64}$'),
    evidence_id   text,            -- no FK: the evidence row itself may be deleted
    rows_affected integer NOT NULL DEFAULT 0 CHECK (rows_affected >= 0),
    run_id        text REFERENCES runs (id),
    request_id    text REFERENCES privacy_requests (id)
);
CREATE INDEX deletion_log_hash_idx ON deletion_log (content_hash);

CREATE FUNCTION deletion_log_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'deletion_log is append-only (% not allowed)', TG_OP;
END;
$$;

CREATE TRIGGER deletion_log_no_update_delete
    BEFORE UPDATE OR DELETE ON deletion_log
    FOR EACH ROW EXECUTE FUNCTION deletion_log_append_only();

CREATE TRIGGER deletion_log_no_truncate
    BEFORE TRUNCATE ON deletion_log
    FOR EACH STATEMENT EXECUTE FUNCTION deletion_log_append_only();
