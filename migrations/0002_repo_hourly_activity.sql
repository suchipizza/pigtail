-- M1-T3: GH Archive velocity scan (R1.1). Per-repo, per-hour star/fork counts (no actor data)
-- and a coverage table of which GH Archive hours have been scanned.

CREATE TABLE gharchive_hours (
    hour              timestamptz PRIMARY KEY,     -- start of the UTC hour
    status            text NOT NULL CHECK (status IN ('ok', 'missing')),
    content_hash      text,                        -- snapshot of the hourly dump (NULL if missing)
    evidence_id       text REFERENCES evidence (id),
    events            integer NOT NULL DEFAULT 0,
    filter_window     tstzrange,                   -- window used for per-actor bot features
    bot_filter_version text NOT NULL,
    run_id            text REFERENCES runs (id),
    scanned_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE repo_hourly_activity (
    repo_host_id    bigint NOT NULL,               -- GitHub numeric repo id
    hour            timestamptz NOT NULL,
    repo_name       text NOT NULL,                 -- last name seen in that hour
    stars_raw       integer NOT NULL DEFAULT 0,    -- every WatchEvent
    stars_bot       integer NOT NULL DEFAULT 0,    -- from bot logins (dropped)
    stars_lockstep  integer NOT NULL DEFAULT 0,    -- star-only actors in a flagged burst
    stars_filtered  integer NOT NULL DEFAULT 0,    -- raw - bot - lockstep
    forks_raw       integer NOT NULL DEFAULT 0,
    forks_filtered  integer NOT NULL DEFAULT 0,    -- raw - bot
    lockstep_flag   boolean NOT NULL DEFAULT false,
    PRIMARY KEY (repo_host_id, hour)
);
CREATE INDEX repo_hourly_activity_hour_idx ON repo_hourly_activity (hour);
