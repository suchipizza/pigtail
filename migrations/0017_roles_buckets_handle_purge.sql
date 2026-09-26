-- M21a (Owner Directive 001 §8.1, ADR-066.1, ADR-071.1-2; PRD R5.3, §7 `actor`,
-- `optout_fingerprint`): roles and buckets instead of pseudonymized handles. Forward-only.
--
-- Coded and stored records never hold a handle, a personal name or a pseudonym of an individual.
-- This migration moves the existing data to the new model, then purges every stored pseudonym:
--
-- 1. hn_mention: the author pseudonym is replaced by a coded actor (role, follower bucket,
--    automated-account flag, rule versions). Rows written before this migration cannot be coded
--    from a pseudonym (the handle is gone), so they get role `account`, bucket `r0` (unknown) and
--    `automated_account` NULL (unknown), with rule versions `migrated-0017` so analyses can tell
--    them apart. Then the author column is dropped.
-- 2. upstream_items: the author pseudonym (used to reach every item of an account) is dropped;
--    deletion sync keeps tracking items by id.
-- 3. repo_event_actor (per-repo star/fork events with actor pseudonyms, TM-33): its rows are
--    turned into hourly counts (`repo_event_hourly_agg`, distinct non-automated actors per hour
--    and type, as the table itself counted them), the per-repo event-id watermark is carried to
--    `repo_event_poll.newest_event_id` so no event is counted twice, then the table is dropped.
--    The daily aggregates already stored are kept as they are.
-- 4. The refusal list: kind `pseudonym` becomes `person` (the opt-out fingerprint, ADR-071.1).
--    Values are unchanged (same HMAC under the same key), so every opt-out keeps matching.
-- 5. Every purge writes a count-only tombstone to `deletion_log` with the new reason
--    `directive_001_handle_purge` (no value, no content).
--
-- Outside Postgres, the LLM cache (SQLite, CB-05) may still hold `@p_…` tokens in cached outputs
-- until its retention ends or `pigtail llm cache clear` runs (see docs/guides/operator.md).

ALTER TABLE deletion_log DROP CONSTRAINT deletion_log_reason_check;
ALTER TABLE deletion_log ADD CONSTRAINT deletion_log_reason_check
    CHECK (reason IN ('retention', 'erasure', 'objection', 'deleted_upstream', 'key_rotation',
                      'purpose_limitation', 'directive_001_handle_purge'));

-- 1. hn_mention: coded actor instead of the author pseudonym --------------------------------------
ALTER TABLE hn_mention
    ADD COLUMN author_role text NOT NULL DEFAULT 'account' CHECK (author_role IN (
        'maintainer', 'account', 'newsletter', 'community', 'organization', 'automated_account')),
    ADD COLUMN author_bucket text NOT NULL DEFAULT 'r0'
        CHECK (author_bucket IN ('r0', 'r1', 'r2', 'r3', 'r4')),
    ADD COLUMN automated_account boolean,           -- NULL: unknown (rows before 0017)
    ADD COLUMN bot_rule_version text,
    ADD COLUMN role_rule_version text;
UPDATE hn_mention SET bot_rule_version = 'migrated-0017', role_rule_version = 'migrated-0017';
ALTER TABLE hn_mention ALTER COLUMN bot_rule_version SET NOT NULL;
ALTER TABLE hn_mention ALTER COLUMN role_rule_version SET NOT NULL;
ALTER TABLE hn_mention ALTER COLUMN author_role DROP DEFAULT;
ALTER TABLE hn_mention ALTER COLUMN author_bucket DROP DEFAULT;
ALTER TABLE hn_mention ADD CONSTRAINT hn_mention_automated_role_check
    CHECK (automated_account IS NULL OR automated_account = (author_role = 'automated_account'));

INSERT INTO deletion_log (reason, action, target, rows_affected)
SELECT 'directive_001_handle_purge', 'fields_cleared', 'hn_mention.author', n
FROM (SELECT count(*)::integer AS n FROM hn_mention WHERE author IS NOT NULL) t WHERE n > 0;
DROP INDEX IF EXISTS hn_mention_author_idx;
ALTER TABLE hn_mention DROP COLUMN author;

-- 2. upstream_items: no author ----------------------------------------------------------------------
INSERT INTO deletion_log (reason, action, target, rows_affected)
SELECT 'directive_001_handle_purge', 'fields_cleared', 'upstream_items.author_pseudonym', n
FROM (SELECT count(*)::integer AS n FROM upstream_items WHERE author_pseudonym IS NOT NULL) t
WHERE n > 0;
DROP INDEX IF EXISTS upstream_items_author_idx;
ALTER TABLE upstream_items DROP COLUMN author_pseudonym;

-- 3. per-repo events: counts only ------------------------------------------------------------------
-- Hourly star/fork counts per repo (project-level aggregate). `stars` / `forks`: events from
-- non-automated accounts, de-duplicated within a poll (in memory); `*_automated`: events the bot
-- rule flagged. `bot_rule_version` records the rule.
CREATE TABLE repo_event_hourly_agg (
    repo_host_id      bigint NOT NULL,
    hour              timestamptz NOT NULL CHECK (date_trunc('hour', hour) = hour),
    stars             integer NOT NULL DEFAULT 0 CHECK (stars >= 0),
    stars_automated   integer NOT NULL DEFAULT 0 CHECK (stars_automated >= 0),
    forks             integer NOT NULL DEFAULT 0 CHECK (forks >= 0),
    forks_automated   integer NOT NULL DEFAULT 0 CHECK (forks_automated >= 0),
    bot_rule_version  text NOT NULL,
    updated_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (repo_host_id, hour)
);

INSERT INTO repo_event_hourly_agg (repo_host_id, hour, stars, stars_automated, forks,
                                   forks_automated, bot_rule_version)
SELECT repo_host_id, date_trunc('hour', created_at),
       count(DISTINCT actor_pseudonym) FILTER (WHERE event_type = 'WatchEvent' AND NOT is_bot),
       count(*) FILTER (WHERE event_type = 'WatchEvent' AND is_bot),
       count(DISTINCT actor_pseudonym) FILTER (WHERE event_type = 'ForkEvent' AND NOT is_bot),
       count(*) FILTER (WHERE event_type = 'ForkEvent' AND is_bot),
       'bot-filter-v0'
FROM repo_event_actor
GROUP BY 1, 2;

-- Watermark of counted events: an event is new when (created_at, id) is above the largest pair
-- counted so far for the repo (pigtail.capture.repo_events).
ALTER TABLE repo_event_poll
    ADD COLUMN watermark_at timestamptz,     -- created_at of the newest counted event
    ADD COLUMN newest_event_id bigint,       -- its id (ties at the same second: larger id is new)
    ADD COLUMN oldest_event_at timestamptz;  -- oldest event seen by this poll (any type)
-- carry each repo's watermark to its latest poll so the next poll does not re-count events
UPDATE repo_event_poll p SET watermark_at = w.at, newest_event_id = w.max_id
FROM (
    SELECT DISTINCT ON (repo_host_id) repo_host_id, created_at AS at,
           CASE WHEN event_id ~ '^[0-9]{1,18}$' THEN event_id::bigint END AS max_id
    FROM repo_event_actor
    ORDER BY repo_host_id, created_at DESC,
             CASE WHEN event_id ~ '^[0-9]{1,18}$' THEN event_id::bigint END DESC NULLS LAST
) w
WHERE p.repo_host_id = w.repo_host_id
  AND p.polled_at = (SELECT max(polled_at) FROM repo_event_poll q
                     WHERE q.repo_host_id = p.repo_host_id);
UPDATE repo_event_poll p SET oldest_event_at = w.oldest
FROM (SELECT repo_host_id, min(created_at) AS oldest FROM repo_event_actor GROUP BY 1) w
WHERE p.repo_host_id = w.repo_host_id
  AND p.polled_at = (SELECT min(polled_at) FROM repo_event_poll q
                     WHERE q.repo_host_id = p.repo_host_id);

ALTER TABLE repo_event_daily_agg
    ADD COLUMN bot_rule_version text NOT NULL DEFAULT 'bot-filter-v0';
ALTER TABLE repo_event_daily_agg ALTER COLUMN bot_rule_version DROP DEFAULT;

INSERT INTO deletion_log (reason, action, target, rows_affected)
SELECT 'directive_001_handle_purge', 'rows_deleted', 'repo_event_actor', n
FROM (SELECT count(*)::integer AS n FROM repo_event_actor) t WHERE n > 0;
DROP TABLE repo_event_actor;

-- 4. refusal list: `pseudonym` -> `person` (opt-out fingerprint; values unchanged) ------------------
ALTER TABLE privacy_suppression DROP CONSTRAINT privacy_suppression_kind_check;
ALTER TABLE privacy_suppression DROP CONSTRAINT privacy_suppression_check;
UPDATE privacy_suppression SET kind = 'person' WHERE kind = 'pseudonym';
ALTER TABLE privacy_suppression ADD CONSTRAINT privacy_suppression_kind_check
    CHECK (kind IN ('person', 'repo', 'repo_name', 'repo_name_unkeyed'));
ALTER TABLE privacy_suppression ADD CONSTRAINT privacy_suppression_person_check
    CHECK (kind <> 'person' OR value ~ '^p_[0-9a-f]{16}$');
