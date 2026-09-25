-- M1-T12 (D1 preview, R13.3, DPIA CB-19): operator sessions and an audit log for the private UI,
-- plus indexes for the read-only API behind it (R14.2; D1 p95 <= 2 s at 5,000 evidence items).
-- Forward-only: never edit an applied migration; add a new file instead.
--
-- Neither table holds an IP address, a handle or content. `client` is a keyed hash of the
-- *truncated* client address (IPv4 /24, IPv6 /48), 16 hex chars: enough to rate-limit login
-- attempts per network and to spot a brute-force pattern (security of processing, GDPR Art. 32),
-- not enough to single out a person without the deployment key. Rows expire after
-- LOG_RETENTION_DAYS (12 months max, CB-18).

CREATE TABLE ui_sessions (
    token_hash   text PRIMARY KEY CHECK (token_hash ~ '^[0-9a-f]{64}$'),  -- sha256 of the cookie
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL
);
CREATE INDEX ui_sessions_expires_idx ON ui_sessions (expires_at);

CREATE TABLE ui_audit_log (
    id           bigserial PRIMARY KEY,
    at           timestamptz NOT NULL DEFAULT now(),
    event        text NOT NULL CHECK (event IN (
                     'login_success', 'login_failure', 'login_rate_limited', 'logout',
                     'snapshot_view', 'snapshot_gone', 'snapshot_missing',
                     'snapshot_integrity_failure')),
    route        text NOT NULL,
    status       integer,
    evidence_id  text,           -- no FK: evidence may be deleted later, the audit row stays
    content_hash text CHECK (content_hash IS NULL OR content_hash ~ '^[0-9a-f]{64}$'),
    session      text CHECK (session IS NULL OR session ~ '^[0-9a-f]{16}$'),  -- token-hash prefix
    client       text CHECK (client IS NULL OR client ~ '^[0-9a-f]{16}$')     -- keyed, truncated
);
CREATE INDEX ui_audit_log_at_idx ON ui_audit_log (at);
CREATE INDEX ui_audit_log_login_idx ON ui_audit_log (event, client, at);

-- Audit rows are never rewritten; only the retention purge deletes old rows.
CREATE FUNCTION ui_audit_log_no_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'ui_audit_log rows are immutable (% not allowed)', TG_OP;
END;
$$;
CREATE TRIGGER ui_audit_log_no_update
    BEFORE UPDATE ON ui_audit_log
    FOR EACH ROW EXECUTE FUNCTION ui_audit_log_no_update();

-- Read paths of the D1 API.
CREATE INDEX evidence_case_fetched_idx ON evidence (case_id, fetched_at);
CREATE INDEX evidence_repo_fetched_idx ON evidence (repo_id, fetched_at);
CREATE INDEX cases_status_opened_idx ON cases (status, opened_at DESC);
CREATE INDEX cases_velocity_idx ON cases (((detection ->> 'stars_48h')::integer) DESC NULLS LAST);
CREATE INDEX hn_story_repo_id_idx ON hn_story (repo_id);
CREATE INDEX hn_mention_repo_id_idx ON hn_mention (repo_id);
CREATE INDEX gharchive_hours_evidence_idx ON gharchive_hours (evidence_id);
