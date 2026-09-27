-- M22 owner decisions 2026-09-27 after verifier round 6 (ADR-084; selection-v6). Forward-only.
--
-- View B has its own launch-event anchor, and repos without a declared launch event are
-- anchored on their first external mention or first public release and reported as their own
-- sub-population: `brief_selection_case` rows of that sub-population carry the view key
-- `launch_undeclared`. No new table and no new column: view B's launch events (releases and
-- the first mention) and the distribution surface are stored on `brief_candidate` (signals and
-- metadata), which purge (`REPO_TABLES`), export and inventory already cover; the selection's
-- stored per-view summaries, balance and sensitivity are jsonb.

ALTER TABLE brief_selection_case DROP CONSTRAINT brief_selection_case_view_check;
ALTER TABLE brief_selection_case ADD CONSTRAINT brief_selection_case_view_check
    CHECK (view IN ('plain', 'follow_through', 'launch', 'launch_undeclared'));
