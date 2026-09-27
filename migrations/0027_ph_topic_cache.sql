-- M22 owner decision 2026-09-27 (ADR-085 addendum 4; selection-v10, anchor-v9): the Product
-- Hunt topic scan is reused across runs and briefs. Forward-only.
--
-- A shared, instance-level cache of Product Hunt topic listings, holding the minimum: per topic
-- and listed post, its id, the SHA-256 (lowercase hex) of its product-slot key
-- (`launch_sources.ph_name_key(name)`: casefolded, only a-z and 0-9 kept), createdAt, featuredAt
-- and when pigtail first and last saw it. Nothing readable of the listing's content is stored:
-- no name, slug, tagline, description, vote or comment count, maker, hunter or user (Product
-- Hunt's site terms bar storing "any significant portion of the Content", TM-16). The selection
-- matches a repo by hashing its own key the same way. `name_key_sha256` hashes a product's
-- name, not a repo key, so the table is not in the repo opt-out registry (`REPO_TABLES`; the
-- decision is `deletion.NOT_REPO_KEYED`). Retention: rows not seen for 90 days are deleted by
-- `pigtail retention purge` (`PH_TOPIC_CACHE_RETENTION_DAYS`, retention-policy.md §2).

CREATE TABLE ph_topic_post (
    topic            text NOT NULL,
    post_id          text NOT NULL,
    name_key_sha256  text NOT NULL CHECK (name_key_sha256 ~ '^[0-9a-f]{64}$'),
    created_at       timestamptz,
    featured_at      timestamptz,
    first_seen_at    timestamptz NOT NULL,
    last_seen_at     timestamptz NOT NULL,
    PRIMARY KEY (topic, post_id)
);
CREATE INDEX ph_topic_post_name_key_idx ON ph_topic_post (topic, name_key_sha256);
CREATE INDEX ph_topic_post_created_idx ON ph_topic_post (topic, created_at);

-- One row per scanned interval of one topic (`posts(topic, postedAfter, postedBefore)`): every
-- gap is scanned as calendar-month intervals (UTC month boundaries, clipped to the gap), each
-- with its own page cap, resumable through the cursor stored after every page. Only `complete`
-- rows of the current cache rule count as coverage (the gap rule, ADR-085 addendum 4);
-- `truncated` (page cap) and `failed` (a page that could not be parsed) never do.
CREATE TABLE ph_topic_scan (
    id             bigserial PRIMARY KEY,
    topic          text NOT NULL,
    posted_after   timestamptz NOT NULL,
    posted_before  timestamptz NOT NULL CHECK (posted_before >= posted_after),
    status         text NOT NULL CHECK (status IN ('running', 'complete', 'truncated', 'failed')),
    cursor         text,
    pages          integer NOT NULL DEFAULT 0 CHECK (pages >= 0),
    started_at     timestamptz NOT NULL,
    finished_at    timestamptz,
    rule           text NOT NULL
);
CREATE INDEX ph_topic_scan_topic_idx ON ph_topic_scan (topic, rule, status, finished_at);
