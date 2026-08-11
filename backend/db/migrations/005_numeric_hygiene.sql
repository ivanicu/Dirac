-- Fix: every one-sided numeric CHECK in this schema was transparent to NaN,
-- and one NaN row silently destroyed the rollup for a whole compound.
--
-- Found by brute force, not by reading: an exhaustive adversarial-value sweep
-- pushed NaN, ±Infinity, 1e300, 0 and -45 at bio.result and ALL NINE were
-- accepted. The 34 hand-written gates missed this completely, because a
-- hand-written gate can only test the failure its author already imagined.
--
-- Two mechanisms, both PostgreSQL semantics rather than schema mistakes:
--
--   1. NaN COMPARES GREATER THAN EVERY NUMBER. So `CHECK (x >= 0)` is TRUE
--      for NaN — every one-sided non-negativity check in the schema was
--      permeable. A TWO-SIDED range (`x >= 0.5 AND x <= 3.0`) is NaN-safe by
--      accident, which is why the pharmacophore radius survived the sweep and
--      `conformer_set.seconds >= 0` did not.
--   2. `numeric(p,s)` rejects ±Infinity with 22003 (overflow) but accepts
--      NaN. A bare `numeric` accepts all three. So the columns that happened
--      to be guarded were guarded by their PRECISION SPEC, not by intent.
--
-- Consequence, measured: `bio.v_compound_activity` filters `value_canonical > 0`,
-- which is TRUE for NaN, so a single NaN row entered the geometric mean and
-- the summary for that compound came back NaN — no error, no warning, and
-- every downstream reader sees a poisoned number.
--
-- The fix is mechanical on purpose. Hand-listing the columns is the same act
-- that produced the hole: 48 numeric columns exist across chem/bio/design/app
-- and a human enumerating them will miss several. This loops over
-- information_schema so coverage is a property of the loop, not of my
-- attention.

BEGIN;

CREATE FUNCTION meta.is_finite(v numeric) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
    -- NULL is left to the column's own NOT NULL rule. NaN fails the second
    -- comparison (it sorts above +Infinity), so all three specials are caught.
    SELECT v IS NULL OR (v > '-Infinity'::numeric AND v < 'Infinity'::numeric);
$$;
COMMENT ON FUNCTION meta.is_finite(numeric) IS
    'True for a real measurable quantity. Rejects NaN and both infinities. '
    'Do not replace with `v >= 0`-style tests: NaN compares greater than every '
    'number in PostgreSQL and passes any one-sided check.';

-- Physically positive quantities. A concentration, a rate, a lifetime and a
-- solubility cannot be zero or negative; an inhibition percentage can be
-- (activation reads negative and noise reads above 100), so it is deliberately
-- absent — over-constraining is its own way of losing real data.
CREATE FUNCTION meta.requires_positive_value(rt meta.result_type) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
    SELECT rt IN ('IC50','EC50','Ki','Kd','MIC','CC50','GI50',
                  'kon','koff','residence_time','half_life',
                  'solubility','permeability','clearance','purity');
$$;

-- ── mechanical sweep: finiteness on every numeric column ───────────────────

DO $$
DECLARE
    r record;
    constraint_name text;
    added integer := 0;
BEGIN
    FOR r IN
        SELECT c.table_schema, c.table_name, c.column_name
          FROM information_schema.columns c
          JOIN information_schema.tables t
            ON t.table_schema = c.table_schema AND t.table_name = c.table_name
         WHERE c.table_schema IN ('chem','bio','design','app')
           AND c.data_type = 'numeric'
           AND t.table_type = 'BASE TABLE'
           -- Partitions inherit the parent's constraint; adding it again on
           -- each partition would duplicate rather than strengthen.
           AND NOT EXISTS (SELECT 1 FROM pg_inherits i
                            WHERE i.inhrelid = (c.table_schema || '.' || c.table_name)::regclass)
         ORDER BY 1, 2, 3
    LOOP
        constraint_name := left(r.table_name || '_' || r.column_name || '_finite', 63);
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint pc
             WHERE pc.conname = constraint_name
               AND pc.conrelid = (r.table_schema || '.' || r.table_name)::regclass
        ) THEN
            EXECUTE format('ALTER TABLE %I.%I ADD CONSTRAINT %I CHECK (meta.is_finite(%I))',
                           r.table_schema, r.table_name, constraint_name, r.column_name);
            added := added + 1;
        END IF;
    END LOOP;
    RAISE NOTICE 'numeric hygiene: % finiteness constraints added', added;
END $$;

-- ── targeted: a potency may not be zero or negative ────────────────────────

ALTER TABLE bio.result
    ADD CONSTRAINT result_positive_where_physical
        CHECK (NOT meta.requires_positive_value(result_type) OR value_num > 0);

INSERT INTO meta.migration (filename, sha256)
VALUES ('005_numeric_hygiene.sql', digest('005_numeric_hygiene.sql', 'sha256'))
ON CONFLICT (filename) DO NOTHING;

COMMIT;
