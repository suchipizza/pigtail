-- M22 owner decision 2026-09-27 (ADR-085 addendum 4; selection-v10, anchor-v9): the Product
-- Hunt topic scan is reused across runs and briefs. Forward-only.
--
-- A shared, instance-level cache of Product Hunt topic listings. Project-level fields only: a
-- post's id, name, slug, createdAt and featuredAt, and the topic it was listed under; never a
-- tagline, description, vote or comment count, maker, hunter or user (person-level objects are
-- never requested, TM-16). `name_key` is `launch_sources.ph_name_key(name)` (casefolded, only
-- a-z and 0-9 kept), the product-slot key the selection matches repos on. It is a product's
-- name, not a repo key, so the table is not in the repo opt-out registry (`REPO_TABLES`; the
-- decision is `deletion.NOT_REPO_KEYED`). Retention: rows not seen for 90 days are deleted by
-- `pigtail retention purge` (`PH_TOPIC_CACHE_RETENTION_DAYS`, retention-policy.md §2).

CREATE TABLE ph_topic_post (
    topic          text NOT NULL,
    post_id        text NOT NULL,
    name           text,
    name_key       text NOT NULL,
    slug           text,
    created_at     timestamptz,
    featured_at    timestamptz,
    first_seen_at  timestamptz NOT NULL,
    last_seen_at   timestamptz NOT NULL,
    PRIMARY KEY (topic, post_id)
);
CREATE INDEX ph_topic_post_name_key_idx ON ph_topic_post (topic, name_key);
CREATE INDEX ph_topic_post_created_idx ON ph_topic_post (topic, created_at);

-- One row per scanned interval of one topic (`posts(topic, postedAfter, postedBefore)`),
-- resumable through the cursor stored after every page. Only `complete` rows of the current
-- cache rule count as coverage (the gap rule, ADR-085 addendum 4); `truncated` (page cap) and
-- `failed` (a page that could not be parsed) never do.
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
