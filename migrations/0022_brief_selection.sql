-- M22 remainder (PRD R4.3, R4.8, R4.9, R4.10, R4.11, R18.6, R18.8, §9.2; outcome-model v2.1
-- §5.7; ADR-054, ADR-057, ADR-077): the outcome sort, winners and matched losers, balance
-- diagnostics and the sensitivity check of a brief version's final shortlist. Forward-only.
--
-- Project-level only: repo names and ids, metric values, statuses, percentiles, covariates and
-- anomaly flags. No handles, no brief text: the brief is referenced by id, version and content
-- hash. Repo names appear only in `brief_selection_case` rows (candidate_ref, repo_full_name),
-- so a repo opt-out removes them with those rows (CB-13c, `REPO_TABLES`); the selection-level
-- summary, balance and sensitivity hold counts and statistics only, and pairs are linked by a
-- numeric `pair_id`, never by name.

CREATE TABLE brief_selection (
    id                    text PRIMARY KEY CHECK (id ~ '^sel_[0-9a-f]{20}$'),
    brief_id              text NOT NULL,
    brief_version         integer NOT NULL CHECK (brief_version >= 1),
    brief_hash            text NOT NULL,                  -- content hash of the brief version
    brief_run_id          text REFERENCES brief_runs (id) ON DELETE SET NULL,
    data_version          text NOT NULL,                  -- after the stage's star-history fetch
    as_of                 date NOT NULL,                  -- the date `pending` is judged against
    selection_version     text NOT NULL,                  -- selection-v1
    outcome_model_version text NOT NULL,                  -- 2.1
    params_version        text NOT NULL,                  -- analysis-params (anomaly-v0, bursts)
    code_commit           text,
    inputs_hash           text NOT NULL CHECK (inputs_hash ~ '^[0-9a-f]{64}$'),
    result_hash           text NOT NULL CHECK (result_hash ~ '^[0-9a-f]{64}$'),
    params                jsonb NOT NULL,                 -- N, exact match, SMD rules, tie-break
    summary               jsonb NOT NULL,                 -- roles, fallback/widening steps (counts)
    balance               jsonb NOT NULL,                 -- SMD per covariate, exact-match check
    sensitivity           jsonb NOT NULL,                 -- per alternative: counts, Jaccard
    created_at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX brief_selection_brief_idx
    ON brief_selection (brief_id, brief_version, created_at DESC, id DESC);

-- One row per shortlisted repo of a selection: its role, rank, pair, and the detail behind them
-- (anchor, values with status/tag/percentile, qualification checks, covariates, anomaly flag,
-- sensitivity statuses and flags).
CREATE TABLE brief_selection_case (
    selection_id   text NOT NULL REFERENCES brief_selection (id) ON DELETE CASCADE,
    candidate_ref  text NOT NULL CHECK (candidate_ref ~ '^gh:[a-z0-9][a-z0-9-]*/[a-z0-9._-]+$'),
    repo_full_name text NOT NULL CHECK (repo_full_name = lower(repo_full_name)),
    repo_host_id   bigint,
    repo_id        text,
    panel          text NOT NULL CHECK (panel IN ('field', 'exemplar', 'reference')),
    distance       smallint CHECK (distance BETWEEN 0 AND 2),
    named_index    integer CHECK (named_index >= 0),
    is_reference   boolean NOT NULL DEFAULT false,
    role           text NOT NULL CHECK (role IN (
                       'winner', 'matched_loser', 'qualified_not_selected', 'unrankable',
                       'loser_pool_unmatched', 'undetermined', 'no_anchor', 'outside_widening',
                       'exemplar', 'exemplar_matched_loser')),
    rank           integer CHECK (rank >= 1),
    pair_id        integer CHECK (pair_id >= 1),
    pair_panel     text CHECK (pair_panel IN ('field', 'exemplar')),
    headline       boolean,                               -- field pairs: in headline patterns
    sensitivity_flags text[] NOT NULL DEFAULT '{}',
    detail         jsonb NOT NULL,
    CHECK (candidate_ref = 'gh:' || repo_full_name),
    PRIMARY KEY (selection_id, candidate_ref)
);
CREATE INDEX brief_selection_case_name_idx ON brief_selection_case (repo_full_name);
CREATE INDEX brief_selection_case_repo_idx ON brief_selection_case (repo_id)
    WHERE repo_id IS NOT NULL;
