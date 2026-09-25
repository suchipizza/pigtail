-- M1-T19: retry/backfill of missing GH Archive hours; M1-T23: HN follow-ups (repo opt-outs by
-- name, deletion sync for rank-poller stories); CB-23b/CB-24 need no schema change.
-- Forward-only: never edit an applied migration; add a new file instead.

-- M1-T19: why an hour is missing, how often it was tried, and when to try again.
ALTER TABLE gharchive_hours
    ADD COLUMN missing_reason  text
        CHECK (missing_reason IS NULL
               OR missing_reason IN ('not_found', 'server_error', 'transport_error', 'unparseable')),
    ADD COLUMN http_status     integer,
    ADD COLUMN attempts        integer NOT NULL DEFAULT 1 CHECK (attempts >= 0),
    ADD COLUMN last_attempt_at timestamptz,
    ADD COLUMN next_retry_at   timestamptz;
-- Hours marked missing before this migration were 404s (the only case recorded until now).
UPDATE gharchive_hours SET missing_reason = 'not_found', http_status = 404,
    last_attempt_at = scanned_at, next_retry_at = scanned_at
WHERE status = 'missing';
CREATE INDEX gharchive_hours_retry_idx ON gharchive_hours (next_retry_at) WHERE status = 'missing';

-- M1-T23: a repo opt-out also covers repos that are not (yet) in `repos`, matched by normalized
-- `owner/name`. The name itself is not stored: `rn_` + 32 hex of SHA-256("<host>:<owner/name>")
-- (pigtail.privacy.suppression.repo_name_key).
ALTER TABLE privacy_suppression DROP CONSTRAINT privacy_suppression_kind_check;
ALTER TABLE privacy_suppression ADD CONSTRAINT privacy_suppression_kind_check
    CHECK (kind IN ('pseudonym', 'repo', 'repo_name'));
ALTER TABLE privacy_suppression ADD CONSTRAINT privacy_suppression_repo_name_check
    CHECK (kind <> 'repo_name' OR value ~ '^rn_[0-9a-f]{32}$');

-- M1-T23: clearing story titles/urls after an upstream deletion is logged as `fields_cleared`.
ALTER TABLE deletion_log DROP CONSTRAINT deletion_log_action_check;
ALTER TABLE deletion_log ADD CONSTRAINT deletion_log_action_check
    CHECK (action IN ('raw_dropped', 'rows_deleted', 'cache_purged', 'evidence_deleted',
                      'error_text_cleared', 'fields_cleared'));

-- M1-T23: when deletion sync found the story gone upstream (title and url cleared; the rank
-- history and the project-level repo link stay).
ALTER TABLE hn_story ADD COLUMN content_cleared_at timestamptz;

-- M1-T23: stories seen by the rank poller are re-checked by deletion sync from now on. Register
-- the ones seen before this migration, linked to every item snapshot the poller took of them.
INSERT INTO upstream_items (platform, item_id, author_pseudonym, first_seen_at, last_seen_at,
                            next_check_at)
SELECT 'hn', item_id::text, NULL, first_seen_at, last_seen_at, last_seen_at + interval '30 days'
FROM hn_story
ON CONFLICT (platform, item_id) DO NOTHING;
INSERT INTO evidence_upstream_items (evidence_id, platform, item_id)
SELECT e.id, 'hn', s.item_id::text
FROM hn_story s
JOIN evidence e ON e.source = 'hn_ranks'
    AND e.url = 'https://hacker-news.firebaseio.com/v0/item/' || s.item_id::text || '.json'
ON CONFLICT DO NOTHING;
