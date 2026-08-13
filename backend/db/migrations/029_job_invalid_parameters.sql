-- 029 · runtime parameter refusals are expected Job outcomes.
--
-- JSON Schema catches structural mistakes before a Job exists, but scientific
-- handlers must also validate values that require RDKit or another engine.  A
-- malformed SMILES can therefore be discovered after the durable row is open.
-- Mapping that typed INVALID_PARAMETERS refusal to INTERNAL made caller input
-- appear as an operational incident and polluted app.v_attention.

BEGIN;

ALTER TYPE app.job_error
    ADD VALUE IF NOT EXISTS 'INVALID_PARAMETERS' BEFORE 'INTERNAL';

COMMENT ON TYPE app.job_error IS
    'Terminal Job error vocabulary. INVALID_PARAMETERS includes runtime scientific validation after a Job has opened.';

INSERT INTO meta.migration (filename, sha256, content_sha256, hash_source)
VALUES ('029_job_invalid_parameters.sql', '\x6f2e4c72450ce1a6db7b071dd8d8ddc455ee26fccad0aaa54882e74738b0f597'::bytea,
        '\x6f2e4c72450ce1a6db7b071dd8d8ddc455ee26fccad0aaa54882e74738b0f597'::bytea, 'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
