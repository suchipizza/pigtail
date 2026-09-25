-- M1-T24 (ADR-032): GitHub watch list, hourly count snapshots, search/HN screens, star-history
-- daily series, detection v1 agreement, budget ledger, ETag cache, and per-repo events for
-- tracked cases (TM-33; CB-22 retention class person_level_30d, CB-23 minimisation).
-- Project-level tables hold no handles. `repo_event_actor` is the only person-level table here
-- (pseudonyms only; registered in pigtail.privacy.deletion.PERSON_TABLES with a 30-day cap).
-- Forward-only.

-- CB-22: a 30-day person-level retention class for GitHub per-repo event snapshots.
ALTER TABLE evidence DROP CONSTRAINT evidence_retention_class_check;
ALTER TABLE evidence ADD CONSTRAINT evidence_retention_class_check CHECK (
    retention_class IN ('person_level_24m', 'person_level_30d', 'project_level', 'derived_aggregate')
);

-- Watch universe U (replan §6.1 layer 2). One row per repo; deactivated rather than deleted.
CREATE TABLE watchlist (
    id                 bigserial PRIMARY KEY,
    repo_host_id       bigint UNIQUE,                 -- GitHub databaseId; NULL until resolved
    node_id            text UNIQUE,                   -- GraphQL global id (rename-proof lookups)
    full_name          text NOT NULL,                 -- owner/name as last seen
    source             text NOT NULL
        CHECK (source IN ('search', 'hn', 'gharchive', 'manual', 'case')),  -- first inclusion
    sources            text[] NOT NULL DEFAULT '{}',  -- every source that nominated it
    source_ref         text,                          -- e.g. 'hn:<item>', 'show_hn:<item>'
    owner_type         text,                          -- 'User' | 'Organization' (never a login)
    added_at           timestamptz NOT NULL,
    last_nominated_at  timestamptz NOT NULL,
    active             boolean NOT NULL DEFAULT true,
    pinned             boolean NOT NULL DEFAULT false,  -- manual / case: never evicted
    deactivated_at     timestamptz,
    deactivated_reason text,
    created_at_gh      timestamptz,                   -- repo creation time on GitHub
    last_stars         integer,
    last_counted_at    timestamptz,
    baseline_fetched_at timestamptz                   -- last star-history fetch
);
CREATE UNIQUE INDEX watchlist_full_name_lc_idx ON watchlist (lower(full_name));
CREATE INDEX watchlist_active_idx ON watchlist (active, pinned, last_nominated_at);

-- Hourly GraphQL counts (project-level; public counters, net of un-stars).
CREATE TABLE repo_count_snapshot (
    repo_host_id bigint NOT NULL,
    observed_at  timestamptz NOT NULL,                -- fetch time of the batch snapshot
    stars        integer NOT NULL CHECK (stars >= 0),
    forks        integer NOT NULL CHECK (forks >= 0),
    pushed_at    timestamptz,
    evidence_id  text REFERENCES evidence (id) ON DELETE SET NULL,
    PRIMARY KEY (repo_host_id, observed_at)
);
CREATE INDEX repo_count_snapshot_time_idx ON repo_count_snapshot (observed_at);

-- One row per GraphQL batch: point cost from `rateLimit` (replan §8 M3).
CREATE TABLE github_graphql_batch (
    evidence_id  text PRIMARY KEY REFERENCES evidence (id) ON DELETE CASCADE,
    observed_at  timestamptz NOT NULL,
    run_id       text REFERENCES runs (id),
    n_aliases    integer NOT NULL,
    n_found      integer NOT NULL,
    n_missing    integer NOT NULL,
    n_errors     integer NOT NULL,
    cost         integer,
    remaining    integer,
    rate_limit   integer,
    reset_at     timestamptz,
    seconds      real
);
CREATE INDEX github_graphql_batch_time_idx ON github_graphql_batch (observed_at);

-- Budget ledger per UTC hour and resource bucket (ADR-032.4; replan §8 M7).
CREATE TABLE github_budget_ledger (
    hour           timestamptz NOT NULL,
    resource       text NOT NULL CHECK (resource IN ('core', 'graphql', 'search')),
    units          integer NOT NULL DEFAULT 0,        -- requests (core/search) or points (graphql)
    requests       integer NOT NULL DEFAULT 0,
    not_modified   integer NOT NULL DEFAULT 0,        -- 304s (free)
    rate_limited   integer NOT NULL DEFAULT 0,        -- primary or secondary limit answers
    last_limit     integer,
    last_remaining integer,
    last_used      integer,
    last_reset     timestamptz,
    updated_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (hour, resource)
);

-- Conditional-request state per URL (ETag) and the last X-Poll-Interval GitHub sent.
CREATE TABLE github_http_cache (
    url             text PRIMARY KEY,
    etag            text,
    content_hash    text,
    evidence_id     text,
    fetched_at      timestamptz,
    last_status     integer,
    poll_interval_s integer
);

-- Star-history daily net counts (ADR-032.3 scoring series, `raw`). `day` is the endpoint's own
-- day label (week label + index), NOT a UTC day: see `day_boundary_tz`.
CREATE TABLE repo_star_daily (
    repo_host_id    bigint NOT NULL,
    day             date NOT NULL,
    stars_net       integer NOT NULL,                 -- can be negative (net of un-stars)
    week_label      text NOT NULL,                    -- verbatim label of the containing week
    day_boundary_tz text NOT NULL,
    is_partial      boolean NOT NULL DEFAULT false,   -- the day was still filling when fetched
    fetched_at      timestamptz NOT NULL,
    evidence_id     text REFERENCES evidence (id) ON DELETE SET NULL,
    PRIMARY KEY (repo_host_id, day)
);

CREATE TABLE star_history_fetch (
    repo_host_id  bigint NOT NULL,
    fetched_at    timestamptz NOT NULL,
    per_page      integer NOT NULL,
    pages         integer NOT NULL,
    weeks         integer NOT NULL,
    not_modified  integer NOT NULL DEFAULT 0,
    total_net     integer,                            -- sum of weekly totals fetched
    complete      boolean NOT NULL,                   -- reached the repo's creation week
    run_id        text REFERENCES runs (id),
    PRIMARY KEY (repo_host_id, fetched_at)
);

-- Detection v1 vs the GH Archive velocity-v0 control (ADR-032.1).
CREATE TABLE detection_agreement (
    repo_host_id          bigint NOT NULL,
    detected_hour         timestamptz NOT NULL,
    v1_case_id            text REFERENCES cases (id) ON DELETE SET NULL,
    v0_case_id            text REFERENCES cases (id) ON DELETE SET NULL,
    v0_opened_at          timestamptz,
    gharchive_stars_48h   integer,                    -- filtered GH Archive stars, same window
    gharchive_hours_ok    integer,                    -- scanned GH Archive hours in the window
    v1_stars_48h          integer NOT NULL,
    recorded_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (repo_host_id, detected_hour)
);

-- HN Show HN screen (project-level): which showstories items were looked at.
CREATE TABLE hn_show_screen (
    item_id        integer PRIMARY KEY,
    repo_full_name text,
    seen_at        timestamptz NOT NULL,
    evidence_id    text REFERENCES evidence (id) ON DELETE SET NULL
);

-- Per-repo events for tracked cases (TM-33). PERSON-LEVEL: pseudonyms only, WatchEvent and
-- ForkEvent only (CB-23), kept at most 30 days (CB-22). Read only in aggregate by the bot filter.
CREATE TABLE repo_event_actor (
    repo_host_id    bigint NOT NULL,
    event_id        text NOT NULL,
    event_type      text NOT NULL CHECK (event_type IN ('WatchEvent', 'ForkEvent')),
    actor_pseudonym text CHECK (actor_pseudonym IS NULL OR actor_pseudonym ~ '^p_[0-9a-f]{16}$'),
    is_bot          boolean NOT NULL,
    created_at      timestamptz NOT NULL,
    observed_at     timestamptz NOT NULL,
    PRIMARY KEY (repo_host_id, event_id)
);
CREATE INDEX repo_event_actor_time_idx ON repo_event_actor (repo_host_id, created_at);
CREATE INDEX repo_event_actor_pseudonym_idx ON repo_event_actor (actor_pseudonym);

-- One row per events poll (project-level): 304s, new events, window overflow (replan §8 M5).
CREATE TABLE repo_event_poll (
    repo_host_id    bigint NOT NULL,
    polled_at       timestamptz NOT NULL,
    status          integer NOT NULL,
    pages           integer NOT NULL,
    events_kept     integer NOT NULL DEFAULT 0,
    events_new      integer NOT NULL DEFAULT 0,
    overflow        boolean NOT NULL DEFAULT false,   -- older events may have rolled out unseen
    overflow_after  timestamptz,                      -- the unseen gap starts after this time
    poll_interval_s integer,
    newest_event_at timestamptz,                      -- newest event seen (any type)
    run_id          text REFERENCES runs (id),
    PRIMARY KEY (repo_host_id, polled_at)
);

-- Aggregates that outlive the 30-day person-level rows (TM-33 "then aggregates only").
CREATE TABLE repo_event_daily_agg (
    repo_host_id    bigint NOT NULL,
    day             date NOT NULL,                    -- UTC day of event created_at
    stars_seen      integer NOT NULL DEFAULT 0,       -- WatchEvents, distinct non-bot actors
    stars_bot       integer NOT NULL DEFAULT 0,
    forks_seen      integer NOT NULL DEFAULT 0,
    forks_bot       integer NOT NULL DEFAULT 0,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (repo_host_id, day)
);
