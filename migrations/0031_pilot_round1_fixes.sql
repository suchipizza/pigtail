-- M23 verifier round 1 (ADR-086 addendum 3). Forward-only. Ids, labels, counts and times only:
-- no prompt text, model output, brief content or person-level value.

-- 1. The cost ledger records what changes a call's cost behaviour, and how many requests a row
--    covers. `thinking` is the label sent (ADR-087: `disabled`, `adaptive`,
--    `adaptive+effort:low`, `cli-default`); NULL on rows written before this migration.
--    `requests` is 1 for every row pigtail writes (one row per call or batch result); a row
--    back-filled by hand for several batch requests says how many, so a cost model per call
--    weights it by that count. The status vocabulary (`pigtail.llm.store.LEDGER_STATUSES`) is
--    checked for new rows; NOT VALID leaves rows written before it unchecked.
ALTER TABLE llm_cost_ledger ADD COLUMN thinking text;
ALTER TABLE llm_cost_ledger ADD COLUMN requests integer NOT NULL DEFAULT 1
    CHECK (requests >= 1);
ALTER TABLE llm_cost_ledger ADD CONSTRAINT llm_cost_ledger_status_check
    CHECK (status IN ('ok', 'invalid_output', 'error', 'error_billed', 'diagnostic'))
    NOT VALID;

-- 2. Code-commit provenance of a resumed pilot: one entry per invocation (the commit that ran
--    it, when, whether it created or resumed the run, and the steps it did), and operator
--    annotations appended without rewriting history (`pigtail brief pilot-annotate`).
ALTER TABLE brief_pilot ADD COLUMN invocations jsonb NOT NULL DEFAULT '[]';
ALTER TABLE brief_pilot ADD COLUMN annotations jsonb NOT NULL DEFAULT '[]';

-- 3. Evidence decay: the actual age of the evidence at the check (hours between its capture and
--    the check) and whether the check ran on time (within the offset's tolerance).
ALTER TABLE brief_evidence_decay ADD COLUMN age_hours double precision;
ALTER TABLE brief_evidence_decay ADD COLUMN on_time boolean;
