-- M23 fix (ADR-086 addendum 1): the pilot's live coder calls all failed with the API's
-- `invalid_request_error` (the nested coder schema compiled to a grammar the API refused) and
-- the pilot still reported success. Forward-only.
--
-- 1. The API's error message per failed batch request, scrubbed of identifiers and anything
--    shaped like a key and cut to 300 characters (`pigtail.llm.errors.safe_error_message`). API
--    error messages describe the request's shape (a schema, a parameter), never its content;
--    the type alone ("invalid_request_error") did not say what to fix.
ALTER TABLE llm_batch_requests ADD COLUMN error_message text
    CHECK (error_message IS NULL OR length(error_message) <= 400);

-- 2. A coded value outside its field's enum (checked in code after parsing the flat coder
--    output, schema 2.0.0) is kept as `unknown` with reason `schema_invalid` and its own status,
--    counted like citation failures.
ALTER TABLE brief_coding DROP CONSTRAINT brief_coding_status_check;
ALTER TABLE brief_coding ADD CONSTRAINT brief_coding_status_check
    CHECK (status IN ('ok', 'unknown', 'citation_failed', 'schema_invalid', 'excluded', 'agreed',
                      'adjudicated', 'derived'));
