-- 011 · the cached row stores the META it was computed with.
--
-- MEASURED DEFECT, found by a golden capture running the same request twice
-- (contracts/golden/v1_responses.json holds both states):
--
--   computed :  contour_closes_in_box true · iso_fixed 10.0 · net_charge 0 ·
--               dims · spacing · vmin · vmax · wall_max   — all present
--   cache hit:  twelve of those NULL, and `method` inconsistent the other way
--               (null on compute, 'gasteiger' on the hit)
--
-- Earlier today normalize_meta made the two paths agree on the KEY SET, and I
-- called that parity. It is not: the DB row has columns for the SCF numbers and
-- none for the grid geometry, so a cache hit honestly reported "not recorded" for
-- a dozen facts the computed path knows. Shape parity was necessary and not
-- sufficient — a panel showing twelve fewer facts is still a poorer answer, and
-- the faster path still looked like the worse instrument.
--
-- WHY A jsonb COLUMN AND NOT TWELVE TYPED COLUMNS. The typed columns that exist
-- (scf_energy_ha, homo_ev, n_basis…) are the ones QUERIED — v_cache_health, the
-- stale sweep and the admin surface all read them, and they stay. What is being
-- stored here is the response's own metadata, whose shape is owned by
-- FIELD_META_SCHEMA and differs per kind: mep has net_charge and charges, the
-- quantum kinds have ecp and scf_cycles, the region route has n_sources_used. A
-- column per key would be 46 columns, most NULL, and a migration for every new
-- caveat. The rule this repo follows is that a jsonb with no exclusion rule is a
-- schema bug — so the exclusion rule is stated and enforced below: keys must be a
-- subset of what the codec declares, which is checked in Python at write time by
-- normalize_meta and asserted here as a documented contract rather than a trigger,
-- because the authority for that set is a Python module and duplicating it in SQL
-- would create exactly the second home this project keeps paying for.
--
-- NOT BACKFILLED, deliberately. The 200-odd existing rows were computed before
-- this column existed and their metadata is genuinely gone; inventing values from
-- the typed columns would produce a row that says iso_fixed 10.0 when nobody
-- recorded it. A cache hit on an old row keeps returning nulls, and
-- v_cache_health now reports how many such rows remain so the number is visible
-- instead of assumed.

BEGIN;

ALTER TABLE app.field_cube
    ADD COLUMN IF NOT EXISTS meta jsonb;

COMMENT ON COLUMN app.field_cube.meta IS
    'The normalized response meta this cube was computed with, so a cache hit '
    'returns the same VALUES and not merely the same KEYS. Written through '
    'envelope.normalize_meta, whose FIELD_META_SCHEMA is the authority on which '
    'keys may appear; volatile keys (cache, stored, total_seconds, computed_at) '
    'are stripped before storage because they describe the REQUEST, not the cube, '
    'and a stored "cache: computed" would be a lie on every subsequent read.';

-- What a reader needs in order to distinguish "this row predates the column" from
-- "this row was written without meta", which are different facts about the system.
CREATE OR REPLACE VIEW app.v_cache_meta_coverage AS
SELECT count(*)                                              AS rows_total,
       count(*) FILTER (WHERE meta IS NOT NULL)              AS rows_with_meta,
       count(*) FILTER (WHERE meta IS NULL)                  AS rows_without_meta,
       min(computed_at) FILTER (WHERE meta IS NOT NULL)      AS first_with_meta,
       max(computed_at) FILTER (WHERE meta IS NULL)          AS last_without_meta
  FROM app.field_cube;

COMMENT ON VIEW app.v_cache_meta_coverage IS
    'rows_without_meta is expected to be non-zero and to stop growing: every row '
    'written from 011 onward carries meta, and the pre-011 rows are not '
    'backfilled because their metadata is genuinely gone. If rows_without_meta '
    'GROWS after this migration, a writer is bypassing the meta path.';

-- Self-recorded content hash. A migration cannot contain the hash of itself, so
-- the recorded value is the hash of this file with its own 64-hex digest replaced
-- by the literal PENDING; backend/db/check_migration_hashes.sh applies the same
-- substitution before comparing. Deterministic, and identical on both sides.
INSERT INTO meta.migration (filename, sha256, content_sha256, hash_source)
VALUES ('011_field_cube_meta.sql',
        '\xfc50f481d38264c3a3939d4bddaef4046dcbbf07bf637bdc65d7c8c1d007a696'::bytea,
        '\xfc50f481d38264c3a3939d4bddaef4046dcbbf07bf637bdc65d7c8c1d007a696'::bytea, 'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
