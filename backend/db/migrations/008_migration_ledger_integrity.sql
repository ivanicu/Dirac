-- 008 · repair the migration ledger's integrity claim.
--
-- meta.migration.sha256 was supposed to be the fingerprint of the applied
-- migration. Measured 2026-08-11: for 000 through 006 it is the sha256 of the
-- FILENAME — `digest('006_producer_identity.sql','sha256')` — so an applied
-- migration could be edited afterwards and nothing would ever notice. That is
-- the exact failure class the producer system one directory away was built to
-- prevent, and only 007 (written after the defect was found) records content.
--
--   recorded 006: 54a08e5d6239...  = sha256 of the string "006_producer_identity.sql"
--   content  006: 806bb4130034...  = sha256 of the file
--
-- WHAT THIS MIGRATION HONESTLY CAN AND CANNOT DO. It records the hash of each
-- file AS IT IS TODAY. That is not proof of what was applied months ago — if a
-- migration was edited after application, this backfill blesses the edited
-- version. Pretending otherwise would be the same lie one level up, so
-- `hash_source` says which rows are verified-at-write and which are
-- backfilled-after-the-fact, and it is a CHECK-constrained enum rather than a
-- comment. From 009 on, every migration writes 'content' at apply time and the
-- gate (backend/db/check_migration_hashes.sh) fails if a file has drifted.

BEGIN;

CREATE TYPE meta.hash_source AS ENUM ('content', 'backfilled', 'filename-legacy');

ALTER TABLE meta.migration
    ADD COLUMN content_sha256 bytea
        CHECK (content_sha256 IS NULL OR octet_length(content_sha256) = 32),
    ADD COLUMN hash_source meta.hash_source;

-- The true content hashes, computed by sha256sum at authoring time (the DB
-- cannot read the files: pg_read_file needs superuser, and granting that to
-- run a migration would be a worse trade than this comment).
WITH truth(filename, content) AS (VALUES
    ('000_extensions.sql', '\x32747e59d27170f238882cb48536ad62232ee89877983cf6b26aa409ef91f69a'::bytea),
    ('001_core.sql', '\x1d73eba6f0db5c37ef683ec680105ae4fe76322123dcc3e337657a74fa9a4a79'::bytea),
    ('002_vocabulary.sql', '\x71e7c5d4f995039afb9e2b15aad9b640c669ca7862e4ad569da0bc2b24cb7c53'::bytea),
    ('003_audit_partition_root.sql', '\x5f800f891bd4b3e0223fce2f3f8be7c5c2a0a804998b7f51bb1a1d4720721883'::bytea),
    ('004_scf_method_split.sql', '\x5ef79cb363a6c232ef157bf1b520d05cad74315351e7b21faf6d8fdaf08730f1'::bytea),
    ('005_numeric_hygiene.sql', '\x66a52c509affbee85eb2d18a01a51254b6fc870e45a4bdf0a34d8a366047de94'::bytea),
    ('006_producer_identity.sql', '\x806bb4130034fc2da299a0b4134e453d9e0c0d9309ffae5578f5c3ce0ab60b24'::bytea),
    ('007_method_registry_and_job_ledger.sql', '\x716f04fa444e5dc725c36e2985e459a4944487b32bb8f3346219cd3828769863'::bytea)
)
UPDATE meta.migration m
   SET content_sha256 = t.content,
       hash_source = CASE
           -- 007 recorded content at apply time; it is verified, not backfilled.
           WHEN m.sha256 = t.content THEN 'content'
           ELSE 'backfilled'
       END::meta.hash_source
  FROM truth t
 WHERE m.filename = t.filename;

-- Any row we could not match to a file on disk is a migration that was applied
-- from somewhere else. Marked, not guessed.
UPDATE meta.migration SET hash_source = 'filename-legacy'
 WHERE hash_source IS NULL;

ALTER TABLE meta.migration
    ALTER COLUMN hash_source SET NOT NULL,
    ADD CONSTRAINT migration_content_hash_present
        CHECK (hash_source = 'filename-legacy' OR content_sha256 IS NOT NULL);

COMMENT ON COLUMN meta.migration.content_sha256 IS
    'sha256 of the migration FILE. Compared against disk by '
    'backend/db/check_migration_hashes.sh; see hash_source for whether this row '
    'was verified when applied or backfilled afterwards.';

INSERT INTO meta.migration (filename, sha256, content_sha256, hash_source)
VALUES ('008_migration_ledger_integrity.sql', '\x9cde5194e7eed1a6dcf0803c12322d2f31458fffea377c0e0166f25d8c89eb63'::bytea,
        '\x9cde5194e7eed1a6dcf0803c12322d2f31458fffea377c0e0166f25d8c89eb63'::bytea, 'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
