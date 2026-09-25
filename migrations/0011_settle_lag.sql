-- K2 settle_lag collection (M4-T4; threshold-calibration pre-registration §2.1 K2, §4 step 2):
-- scheduled re-fetches of the same star-history source-day at lags 1, 3, 7, 14 and 21 days after
-- the day ended (endpoint day labels, `star_history_day_tz`). Project-level: net star counts per
-- endpoint day, no identities. Forward-only.

-- What is due. One row per (repo, endpoint day, lag); `due_at` = end of the day in the endpoint's
-- inferred time zone + lag. Mutable status only (the observations below are append-only).
CREATE TABLE settle_lag_schedule (
    repo_host_id bigint NOT NULL,
    day          date NOT NULL,                        -- star-history endpoint day label
    lag_days     smallint NOT NULL CHECK (lag_days IN (1, 3, 7, 14, 21)),
    due_at       timestamptz NOT NULL,
    status       text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'fetched', 'missed', 'dropped')),
    enrolled_at  timestamptz NOT NULL,
    done_at      timestamptz,
    PRIMARY KEY (repo_host_id, day, lag_days)
);
CREATE INDEX settle_lag_schedule_due_idx ON settle_lag_schedule (status, due_at);

-- Every fetch version of a source-day, never overwritten (repo_star_daily keeps only the latest).
-- Rows are deleted only by a repo opt-out purge (CB-13); UPDATE is refused.
CREATE TABLE star_history_settle_obs (
    id               bigserial PRIMARY KEY,
    repo_host_id     bigint NOT NULL,
    day              date NOT NULL,
    lag_days         smallint NOT NULL CHECK (lag_days IN (1, 3, 7, 14, 21)),
    due_at           timestamptz NOT NULL,
    fetched_at       timestamptz NOT NULL,
    lag_hours_actual real NOT NULL,                    -- fetched_at - end of day, in hours
    stars_net        integer,                          -- NULL: day not in the response (unknown)
    week_label       text,
    is_partial       boolean,
    not_modified     boolean NOT NULL,                 -- 304: same content as the previous fetch
    day_boundary_tz  text NOT NULL,
    evidence_id      text,                             -- no FK: evidence may be purged later
    run_id           text REFERENCES runs (id),
    UNIQUE (repo_host_id, day, lag_days)
);
CREATE INDEX star_history_settle_obs_repo_idx ON star_history_settle_obs (repo_host_id, day);

CREATE FUNCTION star_history_settle_obs_no_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'star_history_settle_obs is append-only (UPDATE not allowed)';
END;
$$;

CREATE TRIGGER star_history_settle_obs_no_update
    BEFORE UPDATE ON star_history_settle_obs
    FOR EACH ROW EXECUTE FUNCTION star_history_settle_obs_no_update();
