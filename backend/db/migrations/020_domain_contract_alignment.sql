-- 020 · align PostgreSQL domain vocabulary with canonical JSON contracts.
--
-- The contracts are authoritative.  Registry views make drift observable from
-- scripts/verify_contract_db_alignment.py and CI against the live schema.
BEGIN;

ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'compound_form';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'batch';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'sample';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'formulation';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'quality_release';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'protocol';

ALTER TYPE app.relation_kind ADD VALUE IF NOT EXISTS 'has_form';
ALTER TYPE app.relation_kind ADD VALUE IF NOT EXISTS 'produced_as';
ALTER TYPE app.relation_kind ADD VALUE IF NOT EXISTS 'sampled_from';
ALTER TYPE app.relation_kind ADD VALUE IF NOT EXISTS 'formulated_as';
ALTER TYPE app.relation_kind ADD VALUE IF NOT EXISTS 'released_by';
ALTER TYPE app.relation_kind ADD VALUE IF NOT EXISTS 'assayed_under';
ALTER TYPE app.relation_kind ADD VALUE IF NOT EXISTS 'has_measurement';

CREATE OR REPLACE VIEW meta.v_object_kind_registry AS
SELECT e.enumsortorder::integer AS ordinal,
       e.enumlabel::text AS kind
  FROM pg_type t
  JOIN pg_enum e ON e.enumtypid = t.oid
  JOIN pg_namespace n ON n.oid = t.typnamespace
 WHERE n.nspname = 'app'
   AND t.typname = 'object_kind'
 ORDER BY e.enumsortorder;

CREATE OR REPLACE VIEW meta.v_relation_kind_registry AS
SELECT e.enumsortorder::integer AS ordinal,
       e.enumlabel::text AS relation
  FROM pg_type t
  JOIN pg_enum e ON e.enumtypid = t.oid
  JOIN pg_namespace n ON n.oid = t.typnamespace
 WHERE n.nspname = 'app'
   AND t.typname = 'relation_kind'
 ORDER BY e.enumsortorder;

COMMENT ON VIEW meta.v_object_kind_registry IS
    'Live PostgreSQL object-kind vocabulary for canonical-contract drift checks.';
COMMENT ON VIEW meta.v_relation_kind_registry IS
    'Live PostgreSQL relation-kind vocabulary for canonical-contract drift checks.';

INSERT INTO meta.migration (filename, sha256, content_sha256, hash_source)
VALUES ('020_domain_contract_alignment.sql', '\xc77215c682f46fa27d48a66862f4ec36dd6aeed385f0aaa11a2ac262268e6bcb'::bytea,
        '\xc77215c682f46fa27d48a66862f4ec36dd6aeed385f0aaa11a2ac262268e6bcb'::bytea, 'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
