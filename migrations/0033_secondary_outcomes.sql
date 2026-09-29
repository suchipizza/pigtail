-- M23b (ADR-090; ADR-088.3, ADR-083.9): exploratory secondary outcomes of a stored selection's
-- view-A cases, filled after the selection by `pigtail brief downloads` (npm TM-08, PyPI via
-- pypistats.org TM-35). Not pre-registered and never read by the outcome sort, the matching or
-- the selection's hashes: `brief_selection` and `brief_selection_case` are not touched.
-- Forward-only.
--
-- One row per selection, case and metric (`adopt.npm_downloads_launch@0-2`, ...). `record` holds
-- the value record (status, value, tag, reason, window, coverage with `coverage_start`, the
-- repo->package mapping, evidence ids and content hashes, source, terms basis, attribution, the
-- `exploratory` label). Repo names are keys like `brief_selection_case`'s, so a repo opt-out
-- removes the rows (`deletion.REPO_TABLES`); package names are public registry names.
CREATE TABLE brief_secondary_outcome (
    selection_id   text NOT NULL REFERENCES brief_selection (id) ON DELETE CASCADE,
    candidate_ref  text NOT NULL CHECK (candidate_ref ~ '^gh:[a-z0-9][a-z0-9-]*/[a-z0-9._-]+$'),
    repo_full_name text NOT NULL CHECK (repo_full_name = lower(repo_full_name)),
    repo_host_id   bigint,
    repo_id        text,
    metric         text NOT NULL CHECK (metric ~ '^[a-z]+\.[a-z0-9_]+@[0-9]+-[0-9]+$'),
    status         text NOT NULL CHECK (status IN ('observed', 'pending', 'unknown',
                                                   'not_applicable')),
    value          double precision CHECK (value IS NULL OR value >= 0),
    exploratory    boolean NOT NULL DEFAULT true CHECK (exploratory),
    record         jsonb NOT NULL,
    rule_version   text NOT NULL,
    as_of          date NOT NULL,
    run_id         text,
    updated_at     timestamptz NOT NULL DEFAULT now(),
    CHECK (candidate_ref = 'gh:' || repo_full_name),
    CHECK ((status = 'observed') = (value IS NOT NULL)),
    PRIMARY KEY (selection_id, candidate_ref, metric)
);
CREATE INDEX brief_secondary_outcome_name_idx ON brief_secondary_outcome (repo_full_name);
CREATE INDEX brief_secondary_outcome_repo_idx ON brief_secondary_outcome (repo_id)
    WHERE repo_id IS NOT NULL;
