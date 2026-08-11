-- Fix, before it is hit: app.field_cube could not store an SOSCF result, and
-- the column it would have stored it in held two facts at once.
--
-- The fields workstream landed a second-order SCF rescue this round, so its
-- meta now reports method='RHF+SOSCF' (that is what finally converged the
-- Fe-heme HOMO on 4HHB HEM, 365 s). Two defects in the original column:
--
--   1. `CHECK (method IN ('RHF','UHF','gasteiger'))` rejects 'RHF+SOSCF'
--      outright. The first cube from the newly-working heavy-metal path
--      would have failed to cache.
--   2. More durable: 'RHF+SOSCF' is TWO facts in one string. The reference
--      (RHF / UHF / ROHF) and the convergence strategy (DIIS / SOSCF /
--      Newton) are orthogonal — every solver variant would otherwise be a
--      new migration and a new spelling, and `GROUP BY method` would split
--      'RHF' from 'RHF+SOSCF' while answering "how many restricted runs".
--
-- One home per fact: two enums. A composite label is a presentation concern
-- and is derived, never stored.

BEGIN;

CREATE TYPE app.scf_reference AS ENUM ('none', 'RHF', 'UHF', 'ROHF');
CREATE TYPE app.scf_converger AS ENUM ('none', 'diis', 'soscf', 'newton');

ALTER TABLE app.field_cube
    DROP CONSTRAINT IF EXISTS field_cube_method_check,
    ADD COLUMN scf_reference app.scf_reference,
    ADD COLUMN scf_converger app.scf_converger;

-- Backfill from the old free-text column, then retire it.
UPDATE app.field_cube
   SET scf_reference = CASE
           WHEN method IS NULL OR method = 'gasteiger' THEN 'none'
           WHEN method LIKE 'ROHF%' THEN 'ROHF'
           WHEN method LIKE 'RHF%'  THEN 'RHF'
           WHEN method LIKE 'UHF%'  THEN 'UHF'
       END::app.scf_reference,
       scf_converger = CASE
           WHEN method IS NULL OR method = 'gasteiger' THEN 'none'
           WHEN method ILIKE '%soscf%'  THEN 'soscf'
           WHEN method ILIKE '%newton%' THEN 'newton'
           ELSE 'diis'
       END::app.scf_converger;

ALTER TABLE app.field_cube DROP COLUMN method;

ALTER TABLE app.field_cube
    ALTER COLUMN scf_reference SET NOT NULL,
    ALTER COLUMN scf_converger SET NOT NULL,
    -- The classical Coulomb well has no SCF at all; a quantum field must name
    -- the reference it converged under. Neither can borrow the other's story.
    ADD CONSTRAINT field_cube_classical_has_no_scf
        CHECK ((kind = 'mep') = (scf_reference = 'none')),
    ADD CONSTRAINT field_cube_scf_pairing
        CHECK ((scf_reference = 'none') = (scf_converger = 'none'));

-- Display label, derived so the two facts stay separable underneath.
CREATE FUNCTION app.scf_method_label(ref app.scf_reference, conv app.scf_converger)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN ref = 'none' THEN 'gasteiger'
        WHEN conv IN ('none', 'diis') THEN ref::text
        ELSE ref::text || '+' || upper(conv::text)
    END;
$$;

-- Accepts what the fields backend already emits ('RHF', 'RHF+SOSCF',
-- 'gasteiger'), so wiring the cache is one call rather than a parser at the
-- call site — and one parser rather than one per caller.
CREATE FUNCTION app.parse_scf_method(label text,
                                     OUT scf_reference app.scf_reference,
                                     OUT scf_converger app.scf_converger)
LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
    IF label IS NULL OR lower(label) IN ('gasteiger', 'none', 'classical') THEN
        scf_reference := 'none'; scf_converger := 'none'; RETURN;
    END IF;
    scf_reference := CASE
        WHEN label ILIKE 'ROHF%' THEN 'ROHF'
        WHEN label ILIKE 'RHF%'  THEN 'RHF'
        WHEN label ILIKE 'UHF%'  THEN 'UHF'
    END;
    IF scf_reference IS NULL THEN
        RAISE EXCEPTION 'unrecognised SCF method label %; add it to app.parse_scf_method rather than storing free text', label;
    END IF;
    scf_converger := CASE
        WHEN label ILIKE '%soscf%'  THEN 'soscf'
        WHEN label ILIKE '%newton%' THEN 'newton'
        ELSE 'diis'
    END;
END;
$$;

COMMENT ON FUNCTION app.parse_scf_method(text) IS
    'Maps the fields backend meta.method label onto the two orthogonal enums. '
    'Raises on an unknown label: a new solver is a migration, never a silent '
    'free-text row.';

INSERT INTO meta.migration (filename, sha256)
VALUES ('004_scf_method_split.sql', digest('004_scf_method_split.sql', 'sha256'))
ON CONFLICT (filename) DO NOTHING;

COMMIT;
