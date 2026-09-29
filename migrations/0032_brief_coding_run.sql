-- M24 (ADR-089): the full coding of a brief (`pigtail brief code`) and its report
-- (`pigtail report brief`). Forward-only. Ids, labels, counts and derived project-level facts
-- only: no prompt text, brief content or person-level value.

-- 1. Run kinds. A full coding run is a `brief_runs` row of kind `coding` with a `brief_pilot`
--    row (case rule `full-cases-v1`: every winner, matched loser, exemplar and exemplar loser
--    of every view), so the pilot's batches, checkpoints, cost ledger and reports serve it
--    unchanged. The report's synthesis calls key on a row of kind `report`.
--    `pigtail run --brief` still reads rows of kind `run` only.
ALTER TABLE brief_runs DROP CONSTRAINT brief_runs_kind_check;
ALTER TABLE brief_runs ADD CONSTRAINT brief_runs_kind_check
    CHECK (kind IN ('run', 'pilot', 'coding', 'report'));

-- 2. A full coding run holds every selected case of a brief (brief 1: 147).
ALTER TABLE brief_pilot DROP CONSTRAINT brief_pilot_cases_requested_check;
ALTER TABLE brief_pilot ADD CONSTRAINT brief_pilot_cases_requested_check
    CHECK (cases_requested BETWEEN 1 AND 5000);

-- 3. Report-only evidence kinds (never shown to the coders): the HN stories of a case's launch
--    events (title, time, front-page flag; no author, no comment) and the case's daily star
--    trajectory around its events with the bursts derived from it (derived JSON snapshots).
ALTER TABLE brief_case_evidence DROP CONSTRAINT brief_case_evidence_kind_check;
ALTER TABLE brief_case_evidence ADD CONSTRAINT brief_case_evidence_kind_check
    CHECK (kind IN ('repo_metadata', 'launch_events', 'readme_at_anchor', 'readme_current',
                    'releases', 'homepage', 'hn_stories', 'star_trajectory'));

-- 4. The case's report facts (`report-facts-v1`: launch events, assets at launch, amplifiers by
--    role and bucket, the star trajectory and bursts; deterministic code, every fact citing an
--    evidence id) and, for a case whose coding was copied from a finished pilot, the pilot run
--    it came from (not paid twice).
ALTER TABLE brief_pilot_case ADD COLUMN facts jsonb;
ALTER TABLE brief_pilot_case ADD COLUMN facts_version text;
ALTER TABLE brief_pilot_case ADD COLUMN reused_from text;
