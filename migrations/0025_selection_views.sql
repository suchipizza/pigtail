-- M22 owner decision 2026-09-27 (ADR-083; selection-v5): one selection holds two headline views
-- (A follow-through, B launch) and a descriptive context view (C). Forward-only.
--
-- No new table. `brief_selection` gains the per-view summaries and result hashes (`views`) and
-- the context view (`context`); `balance` and `sensitivity` are keyed by view from selection-v5
-- on. `brief_selection_case` gets one row per shortlisted repo **per view**: a `view` column in
-- the primary key. Rows written before this migration were the single selection-v1..v4 result
-- and are labelled `plain`. Project-level only, as before (counts, statistics, repo names only
-- in case rows); purge (`REPO_TABLES`), export and inventory already cover both tables, and a
-- repo opt-out removes its rows of every view.

ALTER TABLE brief_selection ADD COLUMN views jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE brief_selection ADD COLUMN context jsonb NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE brief_selection_case ADD COLUMN view text NOT NULL DEFAULT 'plain'
    CHECK (view IN ('plain', 'follow_through', 'launch'));
ALTER TABLE brief_selection_case ALTER COLUMN view DROP DEFAULT;
ALTER TABLE brief_selection_case DROP CONSTRAINT brief_selection_case_pkey;
ALTER TABLE brief_selection_case ADD PRIMARY KEY (selection_id, view, candidate_ref);

-- view B leaves burst-anchored cases out of its population: role `not_in_view`
ALTER TABLE brief_selection_case DROP CONSTRAINT brief_selection_case_role_check;
ALTER TABLE brief_selection_case ADD CONSTRAINT brief_selection_case_role_check CHECK (role IN (
    'winner', 'matched_loser', 'qualified_not_selected', 'unrankable',
    'loser_pool_unmatched', 'undetermined', 'no_anchor', 'outside_widening',
    'exemplar', 'exemplar_matched_loser', 'not_in_view'));
