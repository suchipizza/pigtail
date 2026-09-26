-- M21a (Owner Directive 001 §8.2, ADR-066.2; PRD R19.9): raw snapshots are kept until the
-- brief's report is final plus 12 months, then purged by the scheduled retention job, which keeps
-- the coded facts and the content hash (evidence rows stay, `deletion_state = 'raw_dropped'`).
-- Forward-only.
--
-- A snapshot's retention anchor is the latest `report_final_at` of the briefs whose runs used it
-- (`brief_evidence` -> `brief_runs` -> `brief_report_final`). While any referencing brief's
-- report is not final, or when no brief references the snapshot, the ceiling
-- PERSON_LEVEL_RETENTION_DAYS (24 months from fetch) applies (pigtail.privacy.snapshot_retention).
-- Brief content is never stored here: ids, versions and timestamps only (R18.9).

-- When a brief version's report became final. Re-finalizing a revised report moves the anchor.
CREATE TABLE brief_report_final (
    brief_id        text NOT NULL,
    brief_version   integer NOT NULL CHECK (brief_version >= 1),
    report_final_at timestamptz NOT NULL,
    brief_run_id    text REFERENCES brief_runs (id) ON DELETE SET NULL,
    marked_at       timestamptz NOT NULL DEFAULT now(),
    run_id          text REFERENCES runs (id),
    PRIMARY KEY (brief_id, brief_version)
);

-- Which evidence a brief run used (written by the brief pipeline, `snapshot_retention.link`).
CREATE TABLE brief_evidence (
    brief_run_id text NOT NULL REFERENCES brief_runs (id) ON DELETE CASCADE,
    evidence_id  text NOT NULL REFERENCES evidence (id) ON DELETE CASCADE,
    linked_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (brief_run_id, evidence_id)
);
CREATE INDEX brief_evidence_evidence_idx ON brief_evidence (evidence_id);
