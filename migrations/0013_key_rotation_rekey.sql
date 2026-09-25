-- CB-26 (ADR-043): rotating PSEUDONYM_KEY by re-deriving every stored pseudonym in one
-- transaction (`pigtail privacy rekey`, pigtail.privacy.rekey). Forward-only: never edit an
-- applied migration; add a new file instead.
--
-- * The key fingerprint (CB-25) records a rotation done by re-derivation as `rekey`, distinct
--   from a bare `reset` (which only accepts a new key and re-derives nothing).
-- * Person-level rows that could not be mapped to the new key are deleted (or their pseudonym
--   cleared) with tombstones of reason `key_rotation`.

ALTER TABLE pseudonym_key_fingerprint DROP CONSTRAINT pseudonym_key_fingerprint_set_by_check;
ALTER TABLE pseudonym_key_fingerprint ADD CONSTRAINT pseudonym_key_fingerprint_set_by_check
    CHECK (set_by IN ('first_use', 'reset', 'rekey'));

ALTER TABLE pseudonym_key_fingerprint_log DROP CONSTRAINT pseudonym_key_fingerprint_log_event_check;
ALTER TABLE pseudonym_key_fingerprint_log ADD CONSTRAINT pseudonym_key_fingerprint_log_event_check
    CHECK (event IN ('recorded', 'reset', 'rekey'));

ALTER TABLE deletion_log DROP CONSTRAINT deletion_log_reason_check;
ALTER TABLE deletion_log ADD CONSTRAINT deletion_log_reason_check
    CHECK (reason IN ('retention', 'erasure', 'objection', 'deleted_upstream', 'key_rotation'));
