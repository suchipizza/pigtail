-- M11 (ADR-047.6, ADR-049.1, ADR-049.5): re-scope cleanup. Forward-only.
--
-- 1. Drops the tables of the removed global-collection features. Their rows are discarded
--    (purpose limitation, ADR-047.6); the code that wrote them is in the git tag
--    `archive/global-collection`. One `rows_deleted` tombstone per table records how many rows
--    went (counts only, no content), with the new reason `purpose_limitation`.
--      - 50k-repo watch list: watchlist, repo_count_snapshot, github_graphql_batch
--      - all-GitHub search / HN / GH Archive screens: hn_show_screen
--      - global breakout detection: gharchive_hours, repo_hourly_activity (GH Archive velocity
--        scan), detection_agreement (detection v1 vs velocity v0)
--      - H-sealed holdout guard (ADR-039.5): holdout_unseal_log
--      - settle-lag collection (ADR-042.2-3): settle_lag_schedule, star_history_settle_obs
--    Kept as a cache briefs can reuse: repos, cases, evidence, per-repo star history
--    (repo_star_daily, star_history_fetch), per-repo events, HN tables, the GitHub budget ledger
--    and ETag cache. Raw GH Archive dumps already in the snapshot store stay under their
--    retention (GHARCHIVE_RAW_RETENTION_DAYS) and are purged by `pigtail retention purge`.
--    The generic append-only function `pigtail_append_only()` (0010) is kept: 0012 uses it.
-- 2. Adds `launch_mode_window`, the launch-mode stub (ADR-049.1). M14 fills it; until then the
--    scheduler only reads it: while a window is active the HN rank poller runs continuously.

ALTER TABLE deletion_log DROP CONSTRAINT deletion_log_reason_check;
ALTER TABLE deletion_log ADD CONSTRAINT deletion_log_reason_check
    CHECK (reason IN ('retention', 'erasure', 'objection', 'deleted_upstream', 'key_rotation',
                      'purpose_limitation'));

INSERT INTO deletion_log (reason, action, target, rows_affected)
SELECT 'purpose_limitation', 'rows_deleted', t.name, t.n
FROM (
    SELECT 'watchlist' AS name, (SELECT count(*) FROM watchlist)::integer AS n
    UNION ALL SELECT 'repo_count_snapshot', (SELECT count(*) FROM repo_count_snapshot)::integer
    UNION ALL SELECT 'github_graphql_batch', (SELECT count(*) FROM github_graphql_batch)::integer
    UNION ALL SELECT 'hn_show_screen', (SELECT count(*) FROM hn_show_screen)::integer
    UNION ALL SELECT 'detection_agreement', (SELECT count(*) FROM detection_agreement)::integer
    UNION ALL SELECT 'repo_hourly_activity', (SELECT count(*) FROM repo_hourly_activity)::integer
    UNION ALL SELECT 'gharchive_hours', (SELECT count(*) FROM gharchive_hours)::integer
    UNION ALL SELECT 'holdout_unseal_log', (SELECT count(*) FROM holdout_unseal_log)::integer
    UNION ALL SELECT 'settle_lag_schedule', (SELECT count(*) FROM settle_lag_schedule)::integer
    UNION ALL SELECT 'star_history_settle_obs',
        (SELECT count(*) FROM star_history_settle_obs)::integer
) t
WHERE t.n > 0;

DROP TABLE watchlist;
DROP TABLE repo_count_snapshot;
DROP TABLE github_graphql_batch;
DROP TABLE hn_show_screen;
DROP TABLE detection_agreement;
DROP TABLE repo_hourly_activity;
DROP TABLE gharchive_hours;
DROP TABLE holdout_unseal_log;
DROP TABLE settle_lag_schedule;
DROP TABLE star_history_settle_obs;
DROP FUNCTION star_history_settle_obs_no_update();

-- Launch mode (ADR-048.2, ADR-049.1): a window during which a brief's or a tracked project's
-- launch is followed closely. Project-level: a brief id or a repo id, no person-level data.
CREATE TABLE launch_mode_window (
    id         bigserial PRIMARY KEY,
    scope      text NOT NULL CHECK (scope IN ('brief', 'tracked_project')),
    brief_id   text,                                 -- scope 'brief'
    repo_id    text,                                 -- scope 'tracked_project': repos.id
    starts_at  timestamptz NOT NULL,
    ends_at    timestamptz NOT NULL,
    source     text NOT NULL DEFAULT 'declared'
        CHECK (source IN ('declared', 'detected', 'manual')),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (ends_at > starts_at),
    CHECK ((scope = 'brief') = (brief_id IS NOT NULL)),
    CHECK ((scope = 'tracked_project') = (repo_id IS NOT NULL))
);
CREATE INDEX launch_mode_window_time_idx ON launch_mode_window (starts_at, ends_at);
