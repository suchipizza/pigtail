-- CB-13b (ADR-040.4): repo-name opt-outs use a KEYED hash from now on: `rk_` + 32 hex of
-- HMAC-SHA256(PSEUDONYM_KEY, "repo_name:<owner/name>") (pigtail.privacy.suppression.repo_name_key).
-- Forward-only: never edit an applied migration; add a new file instead.
--
-- Existing entries are unkeyed SHA-256 hashes (`rn_`). SQL cannot convert them: the name is not
-- stored and the key is not (and must not be) in the database. Dropping them would silently
-- resume processing of repos whose owners objected, so they are KEPT under a separate kind,
-- `repo_name_unkeyed`, and still matched at ingest until they are converted:
--   * `pigtail privacy optout rekey` converts every entry whose name is found in local data;
--   * `pigtail privacy optout add --repo owner/name` adds the keyed entry and removes the
--     unkeyed one for that name;
--   * `pigtail doctor` warns (and the scheduler alerts) while any unkeyed entry is left.
-- No new unkeyed entry can be written (trigger below).

ALTER TABLE privacy_suppression DROP CONSTRAINT privacy_suppression_kind_check;
ALTER TABLE privacy_suppression DROP CONSTRAINT privacy_suppression_repo_name_check;

UPDATE privacy_suppression SET kind = 'repo_name_unkeyed' WHERE kind = 'repo_name';

ALTER TABLE privacy_suppression ADD CONSTRAINT privacy_suppression_kind_check
    CHECK (kind IN ('pseudonym', 'repo', 'repo_name', 'repo_name_unkeyed'));
ALTER TABLE privacy_suppression ADD CONSTRAINT privacy_suppression_repo_name_check
    CHECK (kind <> 'repo_name' OR value ~ '^rk_[0-9a-f]{32}$');
ALTER TABLE privacy_suppression ADD CONSTRAINT privacy_suppression_repo_name_unkeyed_check
    CHECK (kind <> 'repo_name_unkeyed' OR value ~ '^rn_[0-9a-f]{32}$');

CREATE FUNCTION privacy_suppression_no_new_unkeyed() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.kind = 'repo_name_unkeyed' THEN
        RAISE EXCEPTION 'unkeyed repo-name opt-outs are legacy (CB-13b); store repo_name_key()';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER privacy_suppression_no_new_unkeyed
    BEFORE INSERT OR UPDATE ON privacy_suppression
    FOR EACH ROW EXECUTE FUNCTION privacy_suppression_no_new_unkeyed();

DO $$
DECLARE n integer;
BEGIN
    SELECT count(*) INTO n FROM privacy_suppression WHERE kind = 'repo_name_unkeyed';
    IF n > 0 THEN
        RAISE WARNING 'CB-13b: % repo-name opt-out(s) still use the unkeyed hash. They stay matched. Run `pigtail privacy optout rekey`, then re-add any that remain with `pigtail privacy optout add --repo owner/name`.', n;
    END IF;
END;
$$;
