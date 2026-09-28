-- M23 (PRD F5, F7, R7.1-R7.6, R15.11, R18.6, R19.8; WORK_ORDER §4.5 M23; ADR-073.1, ADR-073.2,
-- ADR-086): the pilot of a brief's first cases: project-level case evidence and its gaps,
-- double coding (passes A and B), adjudication and the final values, Krippendorff's alpha per
-- field and run, the measured cost per case (the estimate's calibration) and the evidence-decay
-- observations. Forward-only.
--
-- Project-level only. No handle, person name or pseudonym is stored: coded values are codebook
-- enums; excerpts are copied from redacted text (aliases refused by the citation check); repo
-- names appear only in the case-keyed rows (`repo_full_name`, `candidate_ref`, `case_key`),
-- which a repo opt-out deletes (`REPO_TABLES`, CB-13c). Run-level rows (`brief_pilot`,
-- `brief_reliability`, `brief_case_cost_model`) hold ids, versions, counts and statistics only.
-- Brief content is never stored: briefs are referenced by id, version and content hash. The
-- pilot's reports stay in the instance's private data directory (ADR-073.1), not here.

-- A pilot is a `brief_runs` row of kind `pilot` (its estimate, approval, spend, stop and
-- checkpoint), so its batches and cost-ledger rows key on it like a run's. `pigtail run --brief`
-- only ever reads rows of kind `run`.
ALTER TABLE brief_runs ADD COLUMN kind text NOT NULL DEFAULT 'run'
    CHECK (kind IN ('run', 'pilot'));
CREATE INDEX brief_runs_kind_idx ON brief_runs (brief_id, brief_version, kind, created_at DESC);

CREATE TABLE brief_pilot (
    brief_run_id        text PRIMARY KEY REFERENCES brief_runs (id) ON DELETE CASCADE,
    brief_id            text NOT NULL,
    brief_version       integer NOT NULL CHECK (brief_version >= 1),
    brief_hash          text NOT NULL CHECK (brief_hash ~ '^[0-9a-f]{64}$'),
    selection_id        text NOT NULL REFERENCES brief_selection (id),
    data_version        text,
    cases_requested     integer NOT NULL CHECK (cases_requested BETWEEN 1 AND 200),
    case_rule_version   text NOT NULL,
    frame_version       text NOT NULL,
    codebook_version    text NOT NULL,
    code_commit         text,
    prompt_fingerprints jsonb NOT NULL,
    models              jsonb NOT NULL,
    batch_ids           jsonb NOT NULL DEFAULT '[]',
    summary             jsonb NOT NULL DEFAULT '{}',   -- counts, alpha labels, cost (no names)
    created_at          timestamptz NOT NULL DEFAULT now(),
    finished_at         timestamptz
);
CREATE INDEX brief_pilot_brief_idx ON brief_pilot (brief_id, brief_version, created_at DESC);

-- The pilot's cases, in the pilot rule's order. `role`, `pair_id` and `anchor` come from the
-- stored selection and are never shown to the coders (codebook §11.6): they see `coding_id`.
CREATE TABLE brief_pilot_case (
    brief_run_id    text NOT NULL REFERENCES brief_pilot (brief_run_id) ON DELETE CASCADE,
    case_key        text NOT NULL,                   -- <view>:<candidate_ref>
    coding_id       text NOT NULL UNIQUE CHECK (coding_id ~ '^cod_[0-9a-f]{16}$'),
    view            text NOT NULL,
    candidate_ref   text NOT NULL,
    repo_full_name  text NOT NULL CHECK (repo_full_name = lower(repo_full_name)),
    repo_id         text,
    repo_host_id    bigint,
    position        integer NOT NULL CHECK (position >= 1),
    role            text NOT NULL,
    pair_id         integer,
    anchor          jsonb NOT NULL,
    evidence_status text NOT NULL DEFAULT 'pending' CHECK (evidence_status IN ('pending', 'done')),
    evidence_stats  jsonb NOT NULL DEFAULT '{}',     -- request counts per bucket, items, gaps
    PRIMARY KEY (brief_run_id, case_key)
);

-- One evidence item of a case (snapshot or drop: `evidence_id` has the content hash). Decay
-- re-checks `decay_url` (a project-level GET) against `upstream_hash`, the hash of the raw
-- upstream bytes at capture (the item's own hash for items stored as fetched).
CREATE TABLE brief_case_evidence (
    id             bigserial PRIMARY KEY,
    brief_run_id   text NOT NULL REFERENCES brief_pilot (brief_run_id) ON DELETE CASCADE,
    case_key       text NOT NULL,
    brief_id       text NOT NULL,
    brief_version  integer NOT NULL,
    selection_id   text NOT NULL,
    candidate_ref  text NOT NULL,
    repo_full_name text NOT NULL,
    repo_id        text,
    repo_host_id   bigint,
    kind           text NOT NULL CHECK (kind IN ('repo_metadata', 'launch_events',
                       'readme_at_anchor', 'readme_current', 'releases', 'homepage')),
    evidence_id    text NOT NULL REFERENCES evidence (id) ON DELETE CASCADE,
    content_hash   text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    decay_url      text,
    upstream_hash  text CHECK (upstream_hash ~ '^[0-9a-f]{64}$'),
    etag           text,
    last_modified  text,
    item_date      text,
    captured_at    timestamptz NOT NULL,
    detail         jsonb NOT NULL DEFAULT '{}',      -- counts and rule versions only
    code_commit    text,
    UNIQUE (brief_run_id, case_key, kind)
);
CREATE INDEX brief_case_evidence_evidence_idx ON brief_case_evidence (evidence_id);
CREATE INDEX brief_case_evidence_name_idx ON brief_case_evidence (repo_full_name);

-- Sources not collected for a case, by name and reason (person-level sources held by ADR-073.2,
-- gap sources, fetch failures, robots refusals).
CREATE TABLE brief_case_gap (
    brief_run_id   text NOT NULL REFERENCES brief_pilot (brief_run_id) ON DELETE CASCADE,
    case_key       text NOT NULL,
    candidate_ref  text NOT NULL,
    repo_full_name text NOT NULL,
    repo_id        text,
    repo_host_id   bigint,
    source         text NOT NULL,
    reason         text NOT NULL,
    detail         jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (brief_run_id, case_key, source)
);

-- Coded values: pass A, pass B (after citation validation), the adjudicator's decisions and the
-- final value per unit, each with its provenance (R7.4, R18.6).
CREATE TABLE brief_coding (
    id                 bigserial PRIMARY KEY,
    brief_run_id       text NOT NULL REFERENCES brief_pilot (brief_run_id) ON DELETE CASCADE,
    brief_id           text NOT NULL,
    brief_version      integer NOT NULL,
    selection_id       text NOT NULL,
    case_key           text NOT NULL,
    coding_id          text NOT NULL,
    candidate_ref      text NOT NULL,
    repo_full_name     text NOT NULL,
    repo_id            text,
    repo_host_id       bigint,
    pass               text NOT NULL CHECK (pass IN ('A', 'B', 'adjudicator', 'final')),
    unit               text NOT NULL,
    field              text NOT NULL,
    value              text NOT NULL,
    unknown_reason     text,
    evidence_ids       text[] NOT NULL DEFAULT '{}',
    excerpts           jsonb NOT NULL DEFAULT '[]',  -- [{evidence_id, quote}] <= 1 per item
    confidence         text,
    status             text NOT NULL CHECK (status IN ('ok', 'unknown', 'citation_failed',
                           'excluded', 'agreed', 'adjudicated', 'derived')),
    excluded           text,
    reason             text,                          -- the adjudicator's reason
    detail             jsonb NOT NULL DEFAULT '{}',
    model              text,
    llm_backend        text,
    prompt_id          text,
    prompt_version     text,
    prompt_fingerprint text,
    batch_id           text,
    input_hash         text,
    codebook_version   text NOT NULL,
    frame_version      text NOT NULL,
    code_commit        text,
    coded_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (brief_run_id, case_key, pass, unit)
);
CREATE INDEX brief_coding_name_idx ON brief_coding (repo_full_name);

-- Krippendorff's alpha per field (and pooled group) per pilot run, on passes A and B after
-- citation validation and before adjudication (codebook §10.1).
CREATE TABLE brief_reliability (
    brief_run_id        text NOT NULL REFERENCES brief_pilot (brief_run_id) ON DELETE CASCADE,
    brief_id            text NOT NULL,
    brief_version       integer NOT NULL,
    selection_id        text NOT NULL,
    field               text NOT NULL,
    statistic           text NOT NULL CHECK (statistic IN ('nominal', 'ordinal',
                            'known_unknown')),
    alpha               double precision,
    n_cases             integer NOT NULL,
    n_units             integer NOT NULL,
    n_pairable          integer NOT NULL,
    n_excluded          integer NOT NULL,
    disagreements       integer NOT NULL,
    raw_agreement       double precision,
    ci                  jsonb NOT NULL DEFAULT '{}',
    assessed            boolean NOT NULL,
    labels              text[] NOT NULL DEFAULT '{}',
    reason              text,
    rates               jsonb NOT NULL DEFAULT '{}',   -- unknown and citation_failed per pass
    alpha_version       text NOT NULL,
    codebook_version    text NOT NULL,
    frame_version       text NOT NULL,
    code_commit         text,
    prompt_fingerprints jsonb NOT NULL,
    models              jsonb NOT NULL,
    computed_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (brief_run_id, field, statistic)
);

-- Measured per-case cost of a pilot (the estimate's calibration, ADR-086) and its projection
-- for the full brief. Averages and totals only.
CREATE TABLE brief_case_cost_model (
    id            bigserial PRIMARY KEY,
    brief_id      text NOT NULL,
    brief_version integer NOT NULL,
    brief_run_id  text REFERENCES brief_runs (id) ON DELETE SET NULL,
    model_version text NOT NULL,
    n_cases       integer NOT NULL CHECK (n_cases >= 1),
    per_case      jsonb NOT NULL,
    projection    jsonb NOT NULL,
    h6            boolean NOT NULL,
    prices_as_of  text,
    code_commit   text,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX brief_case_cost_model_idx ON brief_case_cost_model (created_at DESC);

-- Evidence decay (R19.8, Directive §5.4): each pilot item's URL re-checked at +1, +7 and +30
-- days with a conditional GET; status, hash changed, gone. No content is stored.
CREATE TABLE brief_evidence_decay (
    id               bigserial PRIMARY KEY,
    case_evidence_id bigint NOT NULL REFERENCES brief_case_evidence (id) ON DELETE CASCADE,
    brief_run_id     text NOT NULL,
    brief_id         text NOT NULL,
    brief_version    integer NOT NULL,
    repo_full_name   text NOT NULL,
    repo_id          text,
    repo_host_id     bigint,
    kind             text NOT NULL,
    offset_days      integer NOT NULL CHECK (offset_days IN (1, 7, 30)),
    due_at           timestamptz NOT NULL,
    checked_at       timestamptz,
    http_status      integer,
    result           text CHECK (result IN ('unchanged', 'changed', 'not_modified', 'gone',
                         'error', 'retrievable')),
    hash_changed     boolean,
    gone             boolean,
    detail           jsonb NOT NULL DEFAULT '{}',
    code_commit      text,
    UNIQUE (case_evidence_id, offset_days)
);
CREATE INDEX brief_evidence_decay_due_idx ON brief_evidence_decay (due_at)
    WHERE checked_at IS NULL;
