-- M1-T14: HN front-page rank poller (own rank history; it cannot be backfilled later).
-- Project-level only: story ids, ranks, urls, titles, scores. No handle is stored, not even a
-- pseudonym (the item's `by` field is dropped at parse). Forward-only.

-- One row per poll of /v0/topstories (snapshotted as project-level evidence).
CREATE TABLE hn_rank_poll (
    observed_at   timestamptz PRIMARY KEY,         -- fetch time of the topstories snapshot
    list          text NOT NULL DEFAULT 'topstories' CHECK (list IN ('topstories')),
    n_items       integer NOT NULL CHECK (n_items >= 0),
    items_fetched integer NOT NULL DEFAULT 0 CHECK (items_fetched >= 0),
    content_hash  text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    evidence_id   text REFERENCES evidence (id) ON DELETE SET NULL,
    run_id        text REFERENCES runs (id)
);

-- Compact rank history: every id of every poll (up to 500; ranks 1-30 = front page, codebook
-- §6.2). score/descendants are filled for the items whose metadata was fetched in that poll.
CREATE TABLE hn_rank_observation (
    item_id     integer NOT NULL,
    observed_at timestamptz NOT NULL REFERENCES hn_rank_poll (observed_at) ON DELETE CASCADE,
    rank        smallint NOT NULL CHECK (rank >= 1),
    score       integer,
    descendants integer,
    PRIMARY KEY (item_id, observed_at)
);
CREATE INDEX hn_rank_observation_time_rank_idx ON hn_rank_observation (observed_at, rank);

-- Latest metadata per story seen near the top. No `by`, no story text.
CREATE TABLE hn_story (
    item_id        integer PRIMARY KEY,
    type           text,
    url            text,
    title          text,
    created_at     timestamptz,                    -- item `time`
    score          integer,
    descendants    integer,
    deleted        boolean NOT NULL DEFAULT false,
    dead           boolean NOT NULL DEFAULT false,
    repo_full_name text,                           -- normalized github.com/owner/repo, lowercase
    repo_id        text REFERENCES repos (id) ON DELETE SET NULL,
    best_rank      smallint,
    first_seen_at  timestamptz NOT NULL,
    last_seen_at   timestamptz NOT NULL,
    evidence_id    text REFERENCES evidence (id) ON DELETE SET NULL
);
CREATE INDEX hn_story_repo_idx ON hn_story (repo_full_name);
