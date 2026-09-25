-- CB-25 (ADR-043): detect a change of PSEUDONYM_KEY. Refusal-list entries and stored pseudonyms
-- are keyed hashes, so under a different key opt-outs silently stop matching. Every command that
-- pseudonymizes or matches opt-outs compares the running key's fingerprint with the one stored
-- here and refuses to run on a mismatch (pigtail.privacy.key_fingerprint).
--
-- The fingerprint is "kfp1_" + the first 32 hex of HMAC-SHA256(PSEUDONYM_KEY,
-- "pigtail-key-fingerprint-v1"). It is a keyed hash of a fixed label: it does not reveal the key
-- and cannot be used to compute pseudonyms. The key itself is never stored.
-- Forward-only: never edit an applied migration; add a new file instead.

-- One row: the fingerprint of the key this database's pseudonyms were made with.
CREATE TABLE pseudonym_key_fingerprint (
    singleton   boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    fingerprint text NOT NULL CHECK (fingerprint ~ '^kfp1_[0-9a-f]{32}$'),
    set_at      timestamptz NOT NULL DEFAULT now(),
    set_by      text NOT NULL CHECK (set_by IN ('first_use', 'reset')),
    run_id      text REFERENCES runs (id)
);

-- Append-only history: first recording and every reset (compromise rotation, runbook CB-09).
CREATE TABLE pseudonym_key_fingerprint_log (
    id              bigserial PRIMARY KEY,
    logged_at       timestamptz NOT NULL DEFAULT now(),
    event           text NOT NULL CHECK (event IN ('recorded', 'reset')),
    old_fingerprint text CHECK (old_fingerprint IS NULL OR old_fingerprint ~ '^kfp1_[0-9a-f]{32}$'),
    new_fingerprint text NOT NULL CHECK (new_fingerprint ~ '^kfp1_[0-9a-f]{32}$'),
    run_id          text REFERENCES runs (id)
);

CREATE TRIGGER pseudonym_key_fingerprint_log_no_update_delete
    BEFORE UPDATE OR DELETE ON pseudonym_key_fingerprint_log
    FOR EACH ROW EXECUTE FUNCTION pigtail_append_only();

CREATE TRIGGER pseudonym_key_fingerprint_log_no_truncate
    BEFORE TRUNCATE ON pseudonym_key_fingerprint_log
    FOR EACH STATEMENT EXECUTE FUNCTION pigtail_append_only();
