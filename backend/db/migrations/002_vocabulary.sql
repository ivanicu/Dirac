-- Dirac production database — controlled vocabularies.
--
-- meta.result_type_unit is not reference data in the decorative sense: it is
-- the dimensional analysis of the entire result table, expressed as a foreign
-- key target. Every (result_type, unit) pair absent from this table is an
-- INSERT that the database rejects. "IC50 = 12 percent" and "logP = 3 nM"
-- become unwritable rather than reviewable.
--
-- NOTE ON TRANSACTIONS: this file deliberately has no BEGIN/COMMIT. A new
-- enum label may not be used in the same transaction that adds it, so the
-- ALTER TYPE statements must commit (autocommit, one statement each) before
-- the INSERTs below can reference them.

ALTER TYPE meta.unit ADD VALUE IF NOT EXISTS 'per_M_per_s';
ALTER TYPE meta.unit ADD VALUE IF NOT EXISTS 'per_s';
ALTER TYPE meta.unit ADD VALUE IF NOT EXISTS 'cm_per_s';
ALTER TYPE meta.unit ADD VALUE IF NOT EXISTS 'ml_per_min_per_kg';
ALTER TYPE meta.dimension ADD VALUE IF NOT EXISTS 'velocity';
ALTER TYPE meta.dimension ADD VALUE IF NOT EXISTS 'rate_constant';
ALTER TYPE meta.dimension ADD VALUE IF NOT EXISTS 'clearance';

-- Potency and cytotoxicity: concentration, canonical nM.
INSERT INTO meta.result_type_unit (result_type, unit, dimension, is_default)
SELECT rt, u, 'concentration', u = 'nM'
  FROM unnest(ARRAY['IC50','EC50','Ki','Kd','MIC','CC50','GI50']::meta.result_type[]) rt
 CROSS JOIN unnest(ARRAY['M','mM','uM','nM','pM']::meta.unit[]) u
ON CONFLICT DO NOTHING;

-- Fractional readouts: percent only. A "percent inhibition" of 0.4 meaning
-- 40% is a recurring data-entry defect; one legal unit makes it visible.
INSERT INTO meta.result_type_unit (result_type, unit, dimension, is_default) VALUES
    ('percent_inhibition', 'percent', 'dimensionless', true),
    ('percent_activity',   'percent', 'dimensionless', true),
    ('plasma_protein_binding', 'percent', 'dimensionless', true),
    ('yield',  'percent', 'dimensionless', true),
    ('purity', 'percent', 'dimensionless', true)
ON CONFLICT DO NOTHING;

-- Log-scale physicochemistry: dimensionless log units, never molar.
INSERT INTO meta.result_type_unit (result_type, unit, dimension, is_default) VALUES
    ('logP', 'log_unit', 'dimensionless', true),
    ('logD', 'log_unit', 'dimensionless', true),
    ('pKa',  'log_unit', 'dimensionless', true),
    ('selectivity_ratio', 'ratio', 'dimensionless', true)
ON CONFLICT DO NOTHING;

-- Solubility is reported both ways in the literature; both are legal and the
-- canonical column keeps them separable.
INSERT INTO meta.result_type_unit (result_type, unit, dimension, is_default) VALUES
    ('solubility', 'ug_per_ml', 'mass_per_volume', true),
    ('solubility', 'mg_per_ml', 'mass_per_volume', false),
    ('solubility', 'uM',        'concentration',   false),
    ('solubility', 'M',         'concentration',   false)
ON CONFLICT DO NOTHING;

-- Kinetics, energetics, thermal, time.
INSERT INTO meta.result_type_unit (result_type, unit, dimension, is_default) VALUES
    ('kon',  'per_M_per_s', 'rate_constant', true),
    ('koff', 'per_s',       'rate_constant', true),
    ('residence_time', 's',   'time', true),
    ('residence_time', 'min', 'time', false),
    ('half_life', 'h',   'time', true),
    ('half_life', 'min', 'time', false),
    ('half_life', 's',   'time', false),
    ('binding_free_energy', 'kcal_per_mol', 'energy', true),
    ('binding_free_energy', 'kj_per_mol',   'energy', false),
    ('binding_enthalpy',    'kcal_per_mol', 'energy', true),
    ('binding_enthalpy',    'kj_per_mol',   'energy', false),
    ('melting_temperature', 'celsius', 'temperature', true),
    ('melting_temperature', 'kelvin',  'temperature', false),
    ('permeability', 'cm_per_s', 'velocity', true),
    ('clearance', 'ml_per_min_per_kg', 'clearance', true)
ON CONFLICT DO NOTHING;

INSERT INTO meta.migration (filename, sha256)
VALUES ('002_vocabulary.sql', digest('002_vocabulary.sql', 'sha256'))
ON CONFLICT (filename) DO NOTHING;
