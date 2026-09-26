-- M12 verifier fix (PRD R18.7; D7): LLM expansion proposals requested from the web app are
-- audited like other brief actions (event only: no brief content, no brief id). Forward-only.
ALTER TABLE ui_audit_log DROP CONSTRAINT ui_audit_log_event_check;
ALTER TABLE ui_audit_log ADD CONSTRAINT ui_audit_log_event_check CHECK (event IN (
    'login_success', 'login_failure', 'login_rate_limited', 'logout',
    'snapshot_view', 'snapshot_gone', 'snapshot_missing', 'snapshot_integrity_failure',
    'brief_create', 'brief_version', 'brief_expansion'));
