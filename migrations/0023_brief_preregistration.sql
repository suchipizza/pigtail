-- M22 verifier round 2 (PRD R8.2, R4.7; Directive §7, ADR-065; outcome-model v2.1 §5.8;
-- ADR-078). Forward-only.
--
-- 1. `brief_preregistration`: the record that a brief version's hypotheses and tests were
--    pre-registered before its outcome sort. The selection stage refuses to run without a row
--    for the exact brief version, content hash and selection-parameter hash. No brief content:
--    the brief is referenced by id, version and SHA-256, the success definition and selection
--    parameters by SHA-256 only, and the pre-registration file (public, in
--    docs/preregistration/) by its path and SHA-256. No repo names, no handles.
-- 2. `shortlist_decision.bulk_id` / `bulk_verdict`: which decisions came from one bulk action
--    (`accept|reject --verdict V`), so a precision made only of bulk decisions on the filter's
--    own verdict is labelled "not item-reviewed" (R4.7, BACKLOG M22-P). NULL for decisions made
--    one by one. Existing rows stay NULL (they can't be told apart after the fact).

CREATE TABLE brief_preregistration (
    id                      bigserial PRIMARY KEY,
    brief_id                text NOT NULL,
    brief_version           integer NOT NULL CHECK (brief_version >= 1),
    brief_hash              text NOT NULL CHECK (brief_hash ~ '^[0-9a-f]{64}$'),
    success_sha256          text NOT NULL CHECK (success_sha256 ~ '^[0-9a-f]{64}$'),
    selection_params_sha256 text NOT NULL CHECK (selection_params_sha256 ~ '^[0-9a-f]{64}$'),
    file_path               text NOT NULL CHECK (length(file_path) BETWEEN 1 AND 1000),
    file_sha256             text NOT NULL CHECK (file_sha256 ~ '^[0-9a-f]{64}$'),
    git_commit              text CHECK (git_commit ~ '^[0-9a-f]{7,40}$'),
    recorded_at             timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX brief_preregistration_brief_idx
    ON brief_preregistration (brief_id, brief_version, recorded_at DESC, id DESC);

ALTER TABLE shortlist_decision ADD COLUMN bulk_id text CHECK (bulk_id ~ '^bulk_[0-9a-f]{16}$');
ALTER TABLE shortlist_decision ADD COLUMN bulk_verdict text
    CHECK (bulk_verdict IN ('relevant', 'not_relevant', 'uncertain', 'none'));
ALTER TABLE shortlist_decision ADD CONSTRAINT shortlist_decision_bulk_check
    CHECK (bulk_verdict IS NULL OR bulk_id IS NOT NULL);
