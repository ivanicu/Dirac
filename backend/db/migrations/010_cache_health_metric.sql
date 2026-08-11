-- 010 · the cache-health metric, corrected to the number that means something.
--
-- 009 shipped `method_generations` as a count of DISTINCT (method_id, version)
-- across all units, and its comment said "producer_generations far above
-- method_generations is the symptom". Measured immediately after applying it:
-- both were 12, which reads as "the granularity fix did nothing" and is FALSE.
--
-- The real distribution, per unit:
--   fields.mep        2 generations   760acf0c557d -> 1628131db888
--   fields.mlp        2               8dd39fc2bcbf -> 44c54a307db9
--   fields.qm.homo    2               1a773b9369b1 -> 24c3efa5ad24
--   fields.qm.lumo    2               (the same two versions)
--   fields.qm.density 2               (the same two versions)
--   fields.qm.mep_qm  2               (the same two versions)
--
-- Two things that matter are visible there and were invisible in the total.
-- First, the correct comparison is 12 PRODUCER generations against 2 per
-- compute unit — a 6x reduction in invalidation events, not parity. Second,
-- the four quantum units share BOTH of their versions because they share an
-- implementation, which is the granularity working exactly as designed: a
-- change to run_scf moves all four together and a change to field_mep moves
-- none of them. Both of today's method bumps were real physics (the ECP fix
-- and the non-finite-budget clamp), which is what a version SHOULD track.
--
-- 009 is left exactly as applied. Editing an applied migration is what
-- backend/db/check_migration_hashes.sh now catches, and the discipline is worth
-- more than the tidiness — a correction is a new file, not a rewrite of history.
--
-- Run:  psql -U ivan -d dirac -f backend/db/migrations/010_cache_health_metric.sql

BEGIN;

DROP VIEW app.v_cache_health;

CREATE VIEW app.v_cache_health AS
SELECT (SELECT count(*) FROM app.field_cube)                          AS rows_total,
       (SELECT count(*) FROM app.field_cube WHERE method_row_id IS NOT NULL)
                                                                      AS rows_with_method,
       (SELECT count(*) FROM app.v_field_cube_servable)               AS rows_servable,
       (SELECT count(*) FROM app.v_field_cube_current)                AS rows_producer_current,
       (SELECT count(DISTINCT version) FROM meta.producer
         WHERE service = 'dirac-fields')                              AS producer_generations,
       -- The comparable number: how many times the WORST-CHURNING compute unit
       -- has moved. Against producer_generations, this is the invalidation
       -- events a cached row actually has to survive.
       (SELECT coalesce(max(g), 0) FROM (
            SELECT count(*) AS g FROM meta.method GROUP BY method_id) x)
                                                                      AS max_generations_per_unit,
       (SELECT count(DISTINCT method_id) FROM meta.method)            AS compute_units;

COMMENT ON VIEW app.v_cache_health IS
    'rows_servable vs rows_total is the payoff of 007. The pair to read together '
    'is producer_generations against max_generations_per_unit: 12 vs 2 on '
    '2026-08-11, i.e. a whole-file hash was invalidating caches six times more '
    'often than the physics changed. If max_generations_per_unit ever approaches '
    'producer_generations, the UNITS table in method_registry.py has drifted to '
    'include something that cannot change a number.';

INSERT INTO meta.migration (filename, sha256, content_sha256, hash_source)
VALUES ('010_cache_health_metric.sql', '\xe22071e1907b220a9ec2baf4ba14134f2a04f390317e403c7d4320e5ccda1e6c'::bytea,
        '\xe22071e1907b220a9ec2baf4ba14134f2a04f390317e403c7d4320e5ccda1e6c'::bytea, 'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
