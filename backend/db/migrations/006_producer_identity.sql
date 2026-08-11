-- Cache invalidation, and the cross-structure cache key.
--
-- Raised by the Field Wells workstream after a real incident: a Gasteiger
-- charge assignment returned NaN for PF6⁻, the field came out uniformly zero,
-- and the zero field was shipped as a normal result AND cached. The bug was
-- fixed at the producer; the poisoned row survived it, because the cache key
-- was (molfile, kind, basis) and none of those had changed.
--
-- The schema could stop "the SCF did not converge". It could not stop "the
-- producer believed it was right at the time", and no CHECK ever will —
-- the row was internally consistent. What was missing is that a computed
-- value is a fact about a COMPUTATION, not only about its inputs, and its
-- identity has to include the thing that computed it.
--
-- This is already how chem.descriptor works: its primary key carries
-- toolkit_id, so two RDKit versions coexist and neither silently overwrites
-- the other. app.field_cube did not, and that inconsistency in my own schema
-- is the defect being fixed here. The general rule, now enforced in both
-- places: A CACHE KEYED ONLY ON ITS INPUTS IS WRONG THE FIRST TIME ITS
-- PRODUCER CHANGES.
--
-- Consequences of putting the producer in the key rather than in a column:
--   · a new producer version simply misses the old rows — invalidation is a
--     lookup outcome, not a maintenance task somebody has to remember
--   · old rows stay readable as history instead of being deleted
--   · the sweep list becomes a query (app.v_field_cube_stale), not a memory
--
-- And because "bump the version after a bug fix" is discipline, and discipline
-- fails: meta.register_producer() RAISES when the same (service, version)
-- comes back with a different source hash. Forgetting the bump is then an
-- error at startup rather than a stale cache six weeks later.

BEGIN;

-- ── who computed it ────────────────────────────────────────────────────────

CREATE TABLE meta.producer (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    service       citext NOT NULL,         -- 'dirac-fields', 'dirac-physics'
    version       citext NOT NULL,         -- bumped by a human on behaviour change
    -- Hash of the producing source. Recorded rather than keyed on, so a
    -- comment edit does not throw away an expensive SCF cache — but a
    -- BEHAVIOUR change that forgets its version bump is still detectable,
    -- and register_producer() refuses it.
    source_sha256 bytea NOT NULL CHECK (octet_length(source_sha256) = 32),
    toolkit_id    uuid REFERENCES meta.toolkit(id),
    declared_at   timestamptz NOT NULL DEFAULT now(),
    superseded_at timestamptz,
    notes         text,
    UNIQUE (service, version)
);

-- At most one live version per service: the current producer is a fact, not a
-- convention, and "which one is current" must not be answerable two ways.
CREATE UNIQUE INDEX producer_one_current_per_service
    ON meta.producer (service) WHERE superseded_at IS NULL;

CREATE FUNCTION meta.register_producer(
    p_service citext, p_version citext, p_source_sha256 bytea,
    p_toolkit_id uuid DEFAULT NULL, p_notes text DEFAULT NULL)
RETURNS uuid LANGUAGE plpgsql AS $$
DECLARE existing meta.producer%ROWTYPE; new_id uuid;
BEGIN
    SELECT * INTO existing FROM meta.producer
     WHERE service = p_service AND version = p_version;

    IF FOUND THEN
        IF existing.source_sha256 <> p_source_sha256 THEN
            RAISE EXCEPTION
                'producer %/% is already registered with a different source hash — '
                'the code changed without a version bump, and every cached row it '
                'produced would keep being served', p_service, p_version;
        END IF;
        RETURN existing.id;
    END IF;

    UPDATE meta.producer SET superseded_at = now()
     WHERE service = p_service AND superseded_at IS NULL;

    INSERT INTO meta.producer (service, version, source_sha256, toolkit_id, notes)
    VALUES (p_service, p_version, p_source_sha256, p_toolkit_id, p_notes)
    RETURNING id INTO new_id;
    RETURN new_id;
END;
$$;

COMMENT ON FUNCTION meta.register_producer IS
    'Call at service startup. Returns the producer id to stamp on every cached '
    'row. Raises when a version is reused with different source — forgetting a '
    'bump becomes a startup error instead of a silently stale cache.';

-- ── the cache key ──────────────────────────────────────────────────────────

-- Rows that predate producer tracking were made by code that has since been
-- fixed. They get an explicit placeholder producer rather than a guess, so
-- they appear in the stale sweep the moment the real service registers.
INSERT INTO meta.producer (service, version, source_sha256, notes)
VALUES ('dirac-fields', '0-pre-006', digest('pre-006-unrecorded', 'sha256'),
        'Backfill for rows cached before producer identity existed. Treat as '
        'unverified: this generation shipped a uniformly-zero field for PF6- '
        'when Gasteiger charges came back NaN.');

ALTER TABLE app.field_cube
    ADD COLUMN producer_id uuid REFERENCES meta.producer(id);

UPDATE app.field_cube
   SET producer_id = (SELECT id FROM meta.producer
                       WHERE service = 'dirac-fields' AND version = '0-pre-006')
 WHERE producer_id IS NULL;

ALTER TABLE app.field_cube
    ALTER COLUMN producer_id SET NOT NULL,
    DROP CONSTRAINT field_cube_molfile_sha256_kind_basis_key,
    ADD CONSTRAINT field_cube_exact_key
        UNIQUE (molfile_sha256, kind, basis, producer_id);

-- ── the coarse key: same ligand, same conformer, different structure ───────

-- The exact key hits only when the bytes match. Two PDB entries containing the
-- same ligand in the same conformation produce different molfiles and miss,
-- even though the answer is identical. compound_id + conformer_hash is the
-- chemical identity of that pair.
--
-- CONTRACT for conformer_hash (32-byte sha256), so two producers agree:
--   1. heavy atoms only, in RDKit canonical rank order
--   2. translate to the heavy-atom centroid
--   3. rotate onto the principal axes of the inertia tensor, with a
--      deterministic sign convention per axis
--   4. THE ROTATION MUST HAVE det = +1. Eigenvectors are defined up to sign,
--      and an odd number of flips is a REFLECTION: enantiomers and mirrored
--      conformers would hash identically, and the cache would serve one
--      molecule's field for another's.
--   5. quantise coordinates to 0.01 Å, hash the byte string together with the
--      parent InChIKey
-- Positive controls the producer owes: a random rigid transform of the same
-- conformer must give the SAME hash; a mirror image must give a DIFFERENT one.
--
-- READ CONTRACT for a coarse-key hit, and it is not optional. A cached cube is
-- coordinate-bound, so serving one into a different structure's scene requires
-- a rigid transform. Do NOT store the write-time frame in a column: the frame
-- would then have two homes (the row and the canonicalisation), and near
-- degenerate inertia tensors — benzene, CH4, anything with two equal principal
-- moments — let coordinate noise swap two axes, so the two sides recompute
-- frames that differ by 90° and the field lands in the wrong place. That is
-- internally consistent, invisible, and wrong.
--
-- The cube already carries its own atom block, so superpose on THAT:
--   1. parse the cached cube's atoms
--   2. Kabsch-superpose the requesting molecule's atoms onto them, in the same
--      canonical rank order this hash contract uses
--   3. ASSERT rmsd < 0.1 Å — otherwise treat the hit as a MISS, recompute, and
--      log it. This is the positive control that fires on every coarse hit:
--      a hash collision, a genuinely different conformer, or a mis-ordered
--      atom list all show up as an exploded RMSD. Without it the coarse key is
--      a mechanism that can only fail silently.
--   4. apply the transform to the cube's origin, its three axis vectors and its
--      atom block. The cube format allows non-axis-aligned vectors, so this is
--      twelve numbers in the header and zero voxel resampling.
--
-- Near-degeneracy still costs hash agreement, and that is the safe direction:
-- two identical conformers that hash differently merely miss the cache.
ALTER TABLE app.field_cube
    ADD COLUMN compound_id uuid REFERENCES chem.compound(id),
    ADD COLUMN conformer_hash bytea
        CHECK (conformer_hash IS NULL OR octet_length(conformer_hash) = 32),
    -- All-or-nothing: half a coarse key cannot be looked up and would silently
    -- never match, which reads as "the cache is cold" forever.
    ADD CONSTRAINT field_cube_coarse_key_complete
        CHECK ((compound_id IS NULL) = (conformer_hash IS NULL));

CREATE UNIQUE INDEX field_cube_coarse_key
    ON app.field_cube (compound_id, conformer_hash, kind, basis, producer_id)
 WHERE compound_id IS NOT NULL;

-- ── the safe read path, and the sweep list ─────────────────────────────────

-- Read through this view and a superseded producer's row can never be served.
CREATE VIEW app.v_field_cube_current AS
SELECT c.*, p.service, p.version AS producer_version
  FROM app.field_cube c
  JOIN meta.producer p ON p.id = c.producer_id
 WHERE p.superseded_at IS NULL;

COMMENT ON VIEW app.v_field_cube_current IS
    'Cache lookups belong here, not on app.field_cube. The base table keeps '
    'every generation as history; this view is the one that cannot hand back '
    'a result from a producer that has since been fixed.';

-- Invalidation becomes a query instead of something to remember.
CREATE VIEW app.v_field_cube_stale AS
SELECT p.service, p.version AS producer_version, p.superseded_at,
       count(*) AS rows_to_sweep,
       pg_size_pretty(sum(b.byte_len)::bigint) AS reclaimable,
       coalesce(sum(c.seconds), 0) AS compute_seconds_represented
  FROM app.field_cube c
  JOIN meta.producer p ON p.id = c.producer_id
  JOIN app.blob b ON b.sha256 = c.blob_sha256
 WHERE p.superseded_at IS NOT NULL
 GROUP BY p.service, p.version, p.superseded_at;

COMMENT ON VIEW app.v_field_cube_stale IS
    'Cached rows whose producer has been superseded. compute_seconds_represented '
    'is what re-deriving them will cost — the number that decides whether a '
    'sweep is a delete or a recompute.';

INSERT INTO meta.migration (filename, sha256)
VALUES ('006_producer_identity.sql', digest('006_producer_identity.sql', 'sha256'))
ON CONFLICT (filename) DO NOTHING;

COMMIT;
