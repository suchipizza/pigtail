-- M21a (Owner Directive §8.3, ADR-066.3; PRD R1.2 minimal collection): mentions are collected
-- for shortlisted projects only. A repo is in mention scope while it is on a brief version's
-- shortlist with status `in_review` or `final` (pigtail.capture.scope). No user sweeps, no
-- follower lists. Project-level: repo names and ids only, no brief content. Forward-only.

CREATE TABLE brief_shortlist_entry (
    brief_id       text NOT NULL,
    brief_version  integer NOT NULL CHECK (brief_version >= 1),
    repo_full_name text NOT NULL CHECK (repo_full_name = lower(repo_full_name)),  -- owner/name
    repo_id        text,                                    -- repos.id when known
    status         text NOT NULL CHECK (status IN ('in_review', 'final', 'removed')),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (brief_id, brief_version, repo_full_name)
);
CREATE INDEX brief_shortlist_entry_name_idx ON brief_shortlist_entry (repo_full_name)
    WHERE status IN ('in_review', 'final');
CREATE INDEX brief_shortlist_entry_repo_idx ON brief_shortlist_entry (repo_id)
    WHERE repo_id IS NOT NULL;
