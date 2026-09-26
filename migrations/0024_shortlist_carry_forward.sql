-- M22 follow-up (PRD R4.7, R4.8; ADR-079): carry a final shortlist forward to a new brief
-- version whose edit changes only fields that don't affect discovery, relevance or the
-- shortlist (`success.*`, `panel.*`, `report.*`, `notes`, store metadata). Forward-only.
--
-- No new table and no brief text: brief versions are referenced by number, carried rows keep
-- their original project-level content (repo names, verdicts, reasons, decisions), and the
-- carry-forward adds only the source version, the role, the operator's reason and the time.
-- Purge (`REPO_TABLES`), export and inventory already cover every table touched here.

-- The target version's run: a `brief_runs` row that did no discovery or relevance work of its
-- own; it points at the source version's run. The runner treats it as complete, so the next
-- `pigtail run --brief` runs only the selection on it (after the pre-registration, R8.2).
ALTER TABLE brief_runs DROP CONSTRAINT brief_runs_status_check;
ALTER TABLE brief_runs ADD CONSTRAINT brief_runs_status_check CHECK (status IN (
    'planned', 'running', 'waiting_batch', 'paused_budget', 'awaiting_review', 'succeeded',
    'failed', 'carried_forward'));
ALTER TABLE brief_runs ADD COLUMN carried_from text REFERENCES brief_runs (id) ON DELETE SET NULL;

-- Candidates copied from an earlier version of the same brief.
ALTER TABLE brief_candidate ADD COLUMN carried_from_version integer
    CHECK (carried_from_version >= 1);

-- Decisions copied from an earlier version: the original role, channel, reason, bulk marker and
-- time are kept as they were; the carry-forward's own role, reason and time sit next to them.
ALTER TABLE shortlist_decision ADD COLUMN carried_from_version integer
    CHECK (carried_from_version >= 1);
ALTER TABLE shortlist_decision ADD COLUMN carried_role text
    CHECK (carried_role IN ('user', 'owner', 'verifier'));
ALTER TABLE shortlist_decision ADD COLUMN carried_reason text
    CHECK (carried_reason IS NULL OR length(btrim(carried_reason)) > 0);
ALTER TABLE shortlist_decision ADD COLUMN carried_at timestamptz;
ALTER TABLE shortlist_decision ADD CONSTRAINT shortlist_decision_carry_check CHECK (
    (carried_from_version IS NULL AND carried_role IS NULL AND carried_reason IS NULL
     AND carried_at IS NULL)
    OR (carried_from_version IS NOT NULL AND carried_role IS NOT NULL
        AND carried_reason IS NOT NULL AND carried_at IS NOT NULL));

-- The shortlist records which version it was carried from and why.
ALTER TABLE brief_shortlist ADD COLUMN carried_from_version integer
    CHECK (carried_from_version >= 1);
ALTER TABLE brief_shortlist ADD COLUMN carried_reason text
    CHECK (carried_reason IS NULL OR length(btrim(carried_reason)) > 0);
ALTER TABLE brief_shortlist ADD CONSTRAINT brief_shortlist_carry_check
    CHECK ((carried_from_version IS NULL) = (carried_reason IS NULL));
