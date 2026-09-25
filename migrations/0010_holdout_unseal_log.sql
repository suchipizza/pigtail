-- M4-T4: append-only log of every H-sealed unseal (threshold-calibration pre-registration §1.4,
-- §5.3; ADR-039.5). Written by pigtail.analysis.split.PgUnsealLog *before* the computation runs.
-- Case ids are case_… ids (no repo names, no person-level data). Forward-only.

CREATE TABLE holdout_unseal_log (
    id              bigserial PRIMARY KEY,
    logged_at       timestamptz NOT NULL DEFAULT now(),
    adr_id          text NOT NULL CHECK (adr_id ~ '^ADR-[0-9]{3,}$'),
    reason          text NOT NULL CHECK (length(reason) BETWEEN 1 AND 500),
    computation     text NOT NULL,
    n_cases         integer NOT NULL CHECK (n_cases > 0),
    case_ids        text[] NOT NULL,
    case_ids_sha256 text NOT NULL CHECK (case_ids_sha256 ~ '^[0-9a-f]{64}$'),
    code_commit     text,
    run_id          text REFERENCES runs (id),
    CHECK (cardinality(case_ids) = n_cases)
);
CREATE INDEX holdout_unseal_log_adr_idx ON holdout_unseal_log (adr_id);

-- Generic append-only guard (usable by later append-only tables).
CREATE FUNCTION pigtail_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only (% not allowed)', TG_TABLE_NAME, TG_OP;
END;
$$;

CREATE TRIGGER holdout_unseal_log_no_update_delete
    BEFORE UPDATE OR DELETE ON holdout_unseal_log
    FOR EACH ROW EXECUTE FUNCTION pigtail_append_only();

CREATE TRIGGER holdout_unseal_log_no_truncate
    BEFORE TRUNCATE ON holdout_unseal_log
    FOR EACH STATEMENT EXECUTE FUNCTION pigtail_append_only();
