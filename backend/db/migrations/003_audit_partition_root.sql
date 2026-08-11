-- Fix: the audit trail fragmented itself by partition.
--
-- Found by backend/db/check_constraints.sql gate P9, which asserted that
-- inserting a result leaves an audit row for 'bio.result' and got zero.
--
-- Mechanism (confirmed by probe, not inferred): a row-level trigger declared
-- on a partitioned table is cloned onto each partition, and inside the clone
-- TG_TABLE_NAME is the PARTITION — 'result_2026'. So every audit row for the
-- growth table was filed under a name that changes every January, and the
-- obvious query (WHERE table_name = 'bio.result') returned nothing at all.
--
-- This is the failure mode the audit table exists to prevent, wearing the
-- audit table's own clothes: the writes were happening, the trail looked
-- populated, and the only query anyone would actually run was empty. A
-- fragmented audit trail is indistinguishable from no audit trail until
-- someone needs it.
--
-- The fact being recorded is "a result row changed", not "partition 2026
-- changed": the logical table is the fact's home, and the partition is a
-- storage detail that is kept beside it rather than in place of it.

BEGIN;

ALTER TABLE audit.row_history ADD COLUMN IF NOT EXISTS physical_table text;

CREATE OR REPLACE FUNCTION audit.track() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    key_text  text;
    logical   text;
    physical  text;
BEGIN
    key_text := COALESCE(
        to_jsonb(COALESCE(NEW, OLD))->>'id',
        to_jsonb(COALESCE(NEW, OLD))->>'compound_id'
    );
    physical := TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME;
    -- pg_partition_root returns NULL for a table that is not part of a
    -- partition tree, so a plain table keeps reporting itself.
    logical  := COALESCE(pg_partition_root(TG_RELID)::text, physical);

    INSERT INTO audit.row_history (table_name, physical_table, op, row_key, before, after)
    VALUES (logical, physical, left(TG_OP, 1), key_text,
            CASE WHEN TG_OP IN ('UPDATE','DELETE') THEN to_jsonb(OLD) END,
            CASE WHEN TG_OP IN ('INSERT','UPDATE') THEN to_jsonb(NEW) END);
    RETURN COALESCE(NEW, OLD);
END;
$$;

-- Rows already written under a partition name are repaired rather than left
-- as a second, silently different, convention.
UPDATE audit.row_history
   SET physical_table = table_name,
       table_name     = regexp_replace(table_name, '^bio\.result_.*$', 'bio.result')
 WHERE table_name ~ '^bio\.result_';

INSERT INTO meta.migration (filename, sha256)
VALUES ('003_audit_partition_root.sql', digest('003_audit_partition_root.sql', 'sha256'))
ON CONFLICT (filename) DO NOTHING;

COMMIT;
