-- 009 · make the cache survive edits that cannot change the number.
--
-- MEASURED 2026-08-11, and it is the whole reason 007 exists:
--
--   app.field_cube            19 rows
--   on a CURRENT producer      1 row
--   => app.v_field_cube_current is effectively EMPTY, so every /field request
--      is a forced recompute. Not occasionally — always.
--
-- Cause: producer identity is the sha256 of the entire service file, and that
-- file was edited 12 times today (CORS headers, a docstring, a log line). Each
-- edit superseded the generation before it, and every row those generations had
-- produced went dark — including rows whose NUMBERS were never in question,
-- because a comment cannot change an SCF.
--
-- Fix: read on METHOD currency, not producer currency. A method version is the
-- hash of the compute unit plus its import closure (fields.mep = field_mep +
-- write_cube + prepare_mol), so it moves when the physics moves and stays put
-- when the HTTP layer moves. That is the difference between a cache and a
-- decoration.
--
-- WHAT STAYS DARK, honestly: the 18 rows written before the dual-write existed
-- carry no method_row_id. Their compute identity cannot be decomposed after the
-- fact — we know which service file version made them, not which functions —
-- so they are NOT resurrected here. Guessing would put a wrong provenance stamp
-- on a real number, which is the one thing this system claims never to do. They
-- age out through bin/dirac-sweep.
--
-- A NULL TRAP, recorded because it nearly shipped in this very migration: the
-- obvious predicate `LEFT JOIN meta.method m ... WHERE m.superseded_at IS NULL`
-- is TRUE for rows with NO method at all, since the outer join fills NULL and
-- NULL IS NULL holds. Written that way it counted all 19 rows as current — a
-- check that cannot fail, in the migration written to fix a check that could
-- not fire. The join must be INNER, and the count must name method_row_id
-- explicitly.
--
-- Run:  psql -U ivan -d dirac -f backend/db/migrations/009_servable_on_method.sql

BEGIN;

CREATE VIEW app.v_field_cube_servable AS
SELECT c.*, m.method_id, m.version AS method_version,
       p.service AS producer_service, p.version AS producer_version
  FROM app.field_cube c
  JOIN meta.method m ON m.id = c.method_row_id      -- INNER: no method, no service
  LEFT JOIN meta.producer p ON p.id = c.producer_id -- provenance, not a filter
 WHERE m.superseded_at IS NULL;

COMMENT ON VIEW app.v_field_cube_servable IS
    'The cache read path. A row is servable when the METHOD that produced it is '
    'still current — not when the service file that happened to host it is. '
    'app.v_field_cube_current (006) remains for provenance archaeology and for '
    'the stale sweep; it is no longer what a lookup reads, because a whole-file '
    'hash made every comment edit a cache flush.';

-- The number that says whether this worked. If servable stays near zero while
-- rows keep arriving, the method versions are churning too and the granularity
-- is still wrong — so the diagnosis is a query rather than a hunch.
CREATE VIEW app.v_cache_health AS
SELECT (SELECT count(*) FROM app.field_cube)                        AS rows_total,
       (SELECT count(*) FROM app.field_cube WHERE method_row_id IS NOT NULL)
                                                                    AS rows_with_method,
       (SELECT count(*) FROM app.v_field_cube_servable)             AS rows_servable,
       (SELECT count(*) FROM app.v_field_cube_current)              AS rows_producer_current,
       (SELECT count(DISTINCT version) FROM meta.producer
         WHERE service = 'dirac-fields')                            AS producer_generations,
       (SELECT count(DISTINCT method_id || version) FROM meta.method) AS method_generations;

COMMENT ON VIEW app.v_cache_health IS
    'rows_servable vs rows_total is the payoff of migration 007. '
    'producer_generations far above method_generations is the symptom that '
    'started this: 12 service-file versions in one day, against 6 compute units '
    'whose physics never moved.';

INSERT INTO meta.migration (filename, sha256, content_sha256, hash_source)
VALUES ('009_servable_on_method.sql', '\x94f66f88678973f8c77aa211def807720d7aad72f81cf4648ec362fdcc2f1f91'::bytea,
        '\x94f66f88678973f8c77aa211def807720d7aad72f81cf4648ec362fdcc2f1f91'::bytea, 'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
