-- M1-T4 (R1.2): HN mentions of repos, and CB-02 (R1.5): deletion-sync tracking of upstream items.
-- Both tables hold pseudonyms and are registered in pigtail.privacy.deletion.PERSON_TABLES
-- (retention CB-01 and erasure CB-08 reach them). No raw handle and no comment text is stored
-- here; the text lives only in private raw snapshots. Forward-only.

CREATE TABLE hn_mention (
    repo_full_name   text NOT NULL,                -- lowercase owner/name searched for
    item_id          bigint NOT NULL,
    item_type        text NOT NULL CHECK (item_type IN ('story', 'comment', 'poll', 'job', 'other')),
    author           text CHECK (author IS NULL OR author ~ '^p_[0-9a-f]{16}$'),  -- pseudonym
    created_at       timestamptz,
    story_id         bigint,
    parent_id        bigint,
    points           integer,
    num_comments     integer,
    title            text,                         -- stories only
    url              text,                         -- stories only
    match_kind       text NOT NULL
        CHECK (match_kind IN ('url', 'full_name', 'name_and_owner', 'name')),
    front_page_tag   boolean NOT NULL DEFAULT false,
    show_hn          boolean NOT NULL DEFAULT false,
    ask_hn           boolean NOT NULL DEFAULT false,
    repo_id          text REFERENCES repos (id) ON DELETE CASCADE,
    case_id          text REFERENCES cases (id) ON DELETE SET NULL,
    evidence_id      text REFERENCES evidence (id) ON DELETE SET NULL,  -- search page
    item_evidence_id text REFERENCES evidence (id) ON DELETE SET NULL,  -- Firebase item
    first_seen_at    timestamptz NOT NULL,
    last_seen_at     timestamptz NOT NULL,
    PRIMARY KEY (repo_full_name, item_id)
);
CREATE INDEX hn_mention_case_idx ON hn_mention (case_id);
CREATE INDEX hn_mention_item_idx ON hn_mention (item_id);
CREATE INDEX hn_mention_author_idx ON hn_mention (author);

-- Upstream items whose content sits in person-level snapshots, re-checked by deletion sync.
CREATE TABLE upstream_items (
    platform         text NOT NULL,
    item_id          text NOT NULL,
    author_pseudonym text CHECK (author_pseudonym IS NULL OR author_pseudonym ~ '^p_[0-9a-f]{16}$'),
    state            text NOT NULL DEFAULT 'present'
        CHECK (state IN ('present', 'deleted', 'dead', 'missing')),
    first_seen_at    timestamptz NOT NULL,
    last_seen_at     timestamptz NOT NULL,
    last_checked_at  timestamptz,
    next_check_at    timestamptz NOT NULL,
    detected_at      timestamptz,                  -- when a deletion was first seen
    acted_at         timestamptz,                  -- when raw copies were dropped
    PRIMARY KEY (platform, item_id)
);
CREATE INDEX upstream_items_due_idx ON upstream_items (platform, next_check_at)
    WHERE state = 'present';
CREATE INDEX upstream_items_author_idx ON upstream_items (platform, author_pseudonym);

-- Which evidence snapshots contain which upstream items (a search page holds many).
CREATE TABLE evidence_upstream_items (
    evidence_id text NOT NULL REFERENCES evidence (id) ON DELETE CASCADE,
    platform    text NOT NULL,
    item_id     text NOT NULL,
    PRIMARY KEY (evidence_id, platform, item_id),
    FOREIGN KEY (platform, item_id) REFERENCES upstream_items (platform, item_id) ON DELETE CASCADE
);
CREATE INDEX evidence_upstream_items_item_idx ON evidence_upstream_items (platform, item_id);
