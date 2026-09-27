-- M22 verifier round 8 (ADR-085 addendum 5; selection-v11, anchor-v10). Forward-only.
--
-- The measured density of the Product Hunt topic listing, one row per topic: the pages and the
-- days of the last complete topic scan a run used (its plan's scan rows, reused and scanned).
-- Aggregates only (no post, id, name or hash). The retention purge keeps it, so the cost
-- estimate keeps a measured density after the cached rows of `ph_topic_post` and
-- `ph_topic_scan` are purged (now after 30 days, `PH_TOPIC_CACHE_RETENTION_DAYS`).
-- `rule` is the cache rule the scan was read under (`PH_TOPIC_CACHE_RULE`).

CREATE TABLE ph_topic_density (
    topic        text PRIMARY KEY,
    rule         text NOT NULL,
    pages        integer NOT NULL CHECK (pages > 0),
    days         double precision NOT NULL CHECK (days > 0),
    measured_at  timestamptz NOT NULL
);
