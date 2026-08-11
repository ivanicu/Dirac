-- Dirac production database — core schema.
--
-- Design rule for every table below: the database, not the application, is
-- where a fact is allowed to be wrong. Anything a chemist could get wrong by
-- typing is an enum, a CHECK, or a foreign key. Anything that could be
-- silently overwritten is append-only with a retraction column.
--
-- Four questions a medicinal-chemistry database must answer correctly, and
-- the structures that answer them:
--
--   1. IDENTITY   — is this the same molecule as that one?
--                   chem.compound is keyed on the STANDARDIZED PARENT's
--                   InChIKey, never on SMILES. Salts, solvates and batches
--                   are separate layers, because "the compound" and "the
--                   thing in the vial" are different objects and only the
--                   second one was measured.
--   2. MEASUREMENT— what does this number mean?
--                   Every measured value carries a qualifier ('>' is not
--                   '='), a unit that is dimension-checked against its
--                   result type by foreign key, and a canonical value in SI
--                   so cross-unit comparison cannot be done by eye.
--   3. PROVENANCE — who says so, and with what?
--                   Every computed number references the exact toolkit
--                   version that produced it (RDKit's logP moves between
--                   releases). Every observed number references a source.
--                   Predicted and measured values can never share a column.
--   4. HISTORY    — what did we believe last month?
--                   No DELETE on fact tables (revoked at the role level);
--                   retracted_at instead, and a row-level audit trail.
--
-- Environment note (verified 2026-08-10, PostgreSQL 18.4): the RDKit
-- cartridge is NOT installed on this server, so there is no `mol` column
-- type and no native substructure index. Structure search is therefore
-- split: similarity runs IN the database over Morgan fingerprints as
-- bit(2048) with pgvector's HNSW `bit_jaccard_ops` (Tanimoto == 1 - Jaccard
-- distance, positive-controlled against a hand-computed case), while exact
-- substructure matching runs in the Python backend (rdkit 2026.03.5) over a
-- fingerprint-prescreened candidate set. If `postgresql-18-rdkit` is ever
-- installed, add a `mol` column to chem.compound and a GiST index; nothing
-- else in this schema has to change.

BEGIN;

-- ── schemas ────────────────────────────────────────────────────────────────
CREATE SCHEMA IF NOT EXISTS meta;    -- provenance, toolkits, vocabularies
CREATE SCHEMA IF NOT EXISTS chem;    -- what a molecule IS
CREATE SCHEMA IF NOT EXISTS bio;     -- what was MEASURED on it
CREATE SCHEMA IF NOT EXISTS design;  -- what a chemist DECIDED
CREATE SCHEMA IF NOT EXISTS app;     -- what the Dirac client persists
CREATE SCHEMA IF NOT EXISTS audit;   -- immutable row history

-- ── meta: vocabularies ─────────────────────────────────────────────────────

-- How much a value is worth. A predicted logP and a measured logP are not
-- the same kind of thing and may never be averaged together.
CREATE TYPE meta.evidence_level AS ENUM (
    'measured',    -- an instrument produced it in our hands
    'literature',  -- an instrument produced it in someone else's hands
    'derived',     -- computed from measured values (e.g. LE from IC50 + HAC)
    'predicted',   -- a model produced it
    'asserted'     -- a human typed it with no attached evidence
);

CREATE TYPE meta.source_kind AS ENUM (
    'doi', 'pubmed', 'patent', 'url', 'pdb', 'chembl',
    'internal_run', 'manual', 'dataset'
);

CREATE TYPE meta.unit AS ENUM (
    -- concentration (canonical: nM)
    'M', 'mM', 'uM', 'nM', 'pM',
    -- mass / amount
    'g', 'mg', 'ug', 'mol', 'mmol', 'umol',
    -- dimensionless
    'percent', 'ratio', 'log_unit', 'count', 'none',
    -- energy (canonical: kcal/mol)
    'kcal_per_mol', 'kj_per_mol', 'hartree',
    -- temperature (canonical: K)
    'celsius', 'kelvin',
    -- time (canonical: s)
    's', 'min', 'h',
    -- solubility / length
    'ug_per_ml', 'mg_per_ml', 'angstrom', 'nm_length'
);

CREATE TYPE meta.dimension AS ENUM (
    'concentration', 'mass', 'amount', 'dimensionless',
    'energy', 'temperature', 'time', 'mass_per_volume', 'length'
);

-- What kind of number this is. Drives which units are legal (see
-- meta.result_type_unit) and how it may be aggregated.
CREATE TYPE meta.result_type AS ENUM (
    'IC50', 'EC50', 'Ki', 'Kd', 'MIC', 'CC50', 'GI50',
    'percent_inhibition', 'percent_activity',
    'kon', 'koff', 'residence_time',
    'solubility', 'logP', 'logD', 'pKa', 'permeability',
    'clearance', 'half_life', 'plasma_protein_binding',
    'melting_temperature', 'binding_enthalpy', 'binding_free_energy',
    'selectivity_ratio', 'yield', 'purity'
);

-- The single most important enum in the schema. A '>' result is a CENSORED
-- observation: it says the true value lies beyond the tested range. Storing
-- it as a bare number and averaging it is the most common way a compound
-- series acquires a fictitious SAR trend.
CREATE TYPE meta.qualifier AS ENUM ('=', '>', '<', '>=', '<=', '~');

CREATE TYPE meta.descriptor_name AS ENUM (
    'mw', 'logp', 'tpsa', 'hbd', 'hba', 'rotatable_bonds',
    'heavy_atoms', 'rings', 'aromatic_rings', 'aliphatic_rings',
    'heterocycles', 'aromatic_heterocycles', 'stereocenters',
    'unspecified_stereocenters', 'fraction_csp3', 'amide_bonds',
    'formal_charge', 'qed', 'sa_score', 'bertz_ct', 'num_spiro', 'num_bridgehead'
);

-- ── meta: tables ───────────────────────────────────────────────────────────

CREATE TABLE meta.migration (
    filename    text PRIMARY KEY,
    sha256      bytea       NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now(),
    applied_by  text        NOT NULL DEFAULT current_user
);

-- Every computed number in this database points at one row here. Without
-- it, a descriptor is unreproducible: RDKit's logP and TPSA change between
-- releases, and a value with no toolkit version cannot be re-derived or
-- compared across time.
CREATE TABLE meta.toolkit (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        citext      NOT NULL,
    version     citext      NOT NULL,
    build_note  text,
    verified_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (name, version)
);

CREATE TABLE meta.source (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind         meta.source_kind NOT NULL,
    locator      citext           NOT NULL,   -- DOI, PDB id, run id, URL
    title        text,
    retrieved_at timestamptz      NOT NULL DEFAULT now(),
    note         text,
    UNIQUE (kind, locator)
);

-- Dimensional legality as data, enforced by foreign key rather than by a
-- trigger: bio.result FKs (result_type, unit) here, so "IC50 in percent"
-- is rejected by the planner, not by a code review.
CREATE TABLE meta.result_type_unit (
    result_type meta.result_type NOT NULL,
    unit        meta.unit        NOT NULL,
    dimension   meta.dimension   NOT NULL,
    is_default  boolean          NOT NULL DEFAULT false,
    PRIMARY KEY (result_type, unit)
);
CREATE UNIQUE INDEX result_type_one_default
    ON meta.result_type_unit (result_type) WHERE is_default;

-- Canonicalisation is a property of the CODE (an enum→factor mapping), not
-- of a lookup table, so it can be IMMUTABLE and drive a generated column.
CREATE FUNCTION meta.to_canonical(value numeric, u meta.unit)
RETURNS numeric LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE u
        -- concentration → nM
        WHEN 'M'   THEN value * 1e9
        WHEN 'mM'  THEN value * 1e6
        WHEN 'uM'  THEN value * 1e3
        WHEN 'nM'  THEN value
        WHEN 'pM'  THEN value / 1e3
        -- energy → kcal/mol
        WHEN 'kcal_per_mol' THEN value
        WHEN 'kj_per_mol'   THEN value / 4.184
        WHEN 'hartree'      THEN value * 627.5094740631
        -- temperature → K
        WHEN 'celsius' THEN value + 273.15
        WHEN 'kelvin'  THEN value
        -- time → s
        WHEN 's'   THEN value
        WHEN 'min' THEN value * 60
        WHEN 'h'   THEN value * 3600
        -- mass → mg, amount → umol, mass/volume → ug/ml
        WHEN 'g'   THEN value * 1e3
        WHEN 'mg'  THEN value
        WHEN 'ug'  THEN value / 1e3
        WHEN 'mol'   THEN value * 1e6
        WHEN 'mmol'  THEN value * 1e3
        WHEN 'umol'  THEN value
        WHEN 'mg_per_ml' THEN value * 1e3
        WHEN 'ug_per_ml' THEN value
        -- length → Å
        WHEN 'angstrom'  THEN value
        WHEN 'nm_length' THEN value * 10
        -- genuinely dimensionless: identity
        ELSE value
    END;
$$;

-- ── chem: identity ─────────────────────────────────────────────────────────

CREATE TYPE chem.stereo_completeness AS ENUM (
    'no_stereocenters',
    'fully_defined',
    'partially_defined',   -- a registered MIXTURE; treat activity accordingly
    'undefined',
    'racemic',
    'unknown'
);

CREATE TYPE chem.form_kind AS ENUM (
    'neutral', 'free_base', 'free_acid', 'salt', 'hydrate',
    'solvate', 'cocrystal', 'mixture'
);

CREATE TYPE chem.provenance_kind AS ENUM (
    'internal_synthesis', 'purchase', 'gift', 'literature_only', 'virtual'
);

-- The standardisation protocol that produced a parent structure. Two
-- compounds are "the same" only relative to a protocol; recording which one
-- ran is what makes a future re-registration auditable instead of a mystery
-- merge.
CREATE TABLE chem.standardizer (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    label      citext UNIQUE NOT NULL,
    toolkit_id uuid   NOT NULL REFERENCES meta.toolkit(id),
    rules      jsonb  NOT NULL,   -- ordered list: sanitize, strip salts, ...
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (jsonb_typeof(rules) = 'array' AND jsonb_array_length(rules) > 0)
);

CREATE SEQUENCE chem.registry_seq START 1;

-- The parent structure. One row per distinct standardized molecule, forever.
CREATE TABLE chem.compound (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    registry_id     text UNIQUE NOT NULL
                        DEFAULT 'DRC-' || lpad(nextval('chem.registry_seq')::text, 7, '0'),
    -- Identity. InChIKey of the STANDARDIZED PARENT, not of what was typed.
    inchikey        char(27) UNIQUE NOT NULL
                        CHECK (inchikey ~ '^[A-Z]{14}-[A-Z]{10}-[A-Z]$'),
    inchi           text     NOT NULL CHECK (inchi LIKE 'InChI=%'),
    smiles          text     NOT NULL,          -- canonical, informational only
    formula         text     NOT NULL,
    mw_monoisotopic numeric(12,5) NOT NULL CHECK (mw_monoisotopic > 0),
    net_charge      integer  NOT NULL DEFAULT 0,
    stereo          chem.stereo_completeness NOT NULL,
    standardizer_id uuid     NOT NULL REFERENCES chem.standardizer(id),
    is_virtual      boolean  NOT NULL DEFAULT false,  -- designed, never made
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      text     NOT NULL DEFAULT current_user,
    retracted_at    timestamptz,
    retraction_reason text,
    CHECK (retracted_at IS NULL OR retraction_reason IS NOT NULL)
);
COMMENT ON COLUMN chem.compound.smiles IS
    'Canonical SMILES for display and export only. Never join or deduplicate '
    'on this column: canonical SMILES is toolkit- and version-dependent.';

CREATE TABLE chem.compound_alias (
    compound_id uuid NOT NULL REFERENCES chem.compound(id) ON DELETE RESTRICT,
    kind        text NOT NULL CHECK (kind IN
                    ('chembl','cas','drugbank','pubchem','vendor','common_name','internal','inn')),
    alias       citext NOT NULL,
    source_id   uuid REFERENCES meta.source(id),
    PRIMARY KEY (kind, alias),
    UNIQUE (compound_id, kind, alias)
);

-- When a standardizer upgrade proves two registrations were one compound,
-- the loser is never deleted: it is aliased forward, so old reports keep
-- resolving.
CREATE TABLE chem.compound_merge (
    loser_id   uuid PRIMARY KEY REFERENCES chem.compound(id),
    winner_id  uuid NOT NULL REFERENCES chem.compound(id),
    merged_at  timestamptz NOT NULL DEFAULT now(),
    merged_by  text NOT NULL DEFAULT current_user,
    reason     text NOT NULL,
    CHECK (loser_id <> winner_id)
);

-- The salt / solvate form. This is what a bottle's label says.
CREATE TABLE chem.form (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    compound_id  uuid NOT NULL REFERENCES chem.compound(id) ON DELETE RESTRICT,
    form_kind    chem.form_kind NOT NULL,
    -- InChIKey of the FULL multi-component structure (parent + counterion).
    full_inchikey char(27) UNIQUE NOT NULL
                    CHECK (full_inchikey ~ '^[A-Z]{14}-[A-Z]{10}-[A-Z]$'),
    components   jsonb NOT NULL,   -- [{smiles, stoichiometry}, ...]
    mw_form      numeric(12,5) NOT NULL CHECK (mw_form > 0),
    label        text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    CHECK (jsonb_typeof(components) = 'array')
);

-- The physical sample. Assay results attach HERE, not to a compound: two
-- batches of the same compound at 99% and 62% purity are two different
-- experimental objects.
CREATE TABLE chem.batch (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    form_id        uuid   NOT NULL REFERENCES chem.form(id) ON DELETE RESTRICT,
    batch_code     citext UNIQUE NOT NULL,
    provenance     chem.provenance_kind NOT NULL,
    purity_pct     numeric(5,2) CHECK (purity_pct > 0 AND purity_pct <= 100),
    purity_method  text CHECK (purity_method IN ('hplc_uv','lcms','nmr','elemental','qnmr','supplier_coa')),
    amount_mg      numeric(12,4) CHECK (amount_mg >= 0),
    supplier       citext,
    synthesized_on date,
    registered_at  timestamptz NOT NULL DEFAULT now(),
    -- A purity number with no method is a rumour.
    CHECK ((purity_pct IS NULL) = (purity_method IS NULL))
);

-- ── chem: computed properties ──────────────────────────────────────────────

-- One row per (compound, descriptor, toolkit). Two RDKit versions coexist
-- and stay distinguishable; nothing is ever overwritten in place.
CREATE TABLE chem.descriptor (
    compound_id uuid NOT NULL REFERENCES chem.compound(id) ON DELETE RESTRICT,
    name        meta.descriptor_name NOT NULL,
    value       numeric NOT NULL,
    toolkit_id  uuid NOT NULL REFERENCES meta.toolkit(id),
    computed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (compound_id, name, toolkit_id)
);

CREATE TYPE chem.fingerprint_kind AS ENUM (
    'morgan2', 'morgan3', 'rdkit', 'maccs', 'atompair', 'topological_torsion'
);

-- Fixed 2048-bit width so a single HNSW index can serve similarity search.
-- MACCS (166 bits) is left-padded by the loader; its similarity is still
-- meaningful because Jaccard ignores jointly-zero positions.
CREATE TABLE chem.fingerprint (
    compound_id uuid NOT NULL REFERENCES chem.compound(id) ON DELETE RESTRICT,
    kind        chem.fingerprint_kind NOT NULL,
    radius      smallint,
    nbits       smallint NOT NULL CHECK (nbits > 0 AND nbits <= 2048),
    bits        bit(2048) NOT NULL,
    popcount    smallint NOT NULL CHECK (popcount >= 0),
    toolkit_id  uuid NOT NULL REFERENCES meta.toolkit(id),
    PRIMARY KEY (compound_id, kind, toolkit_id)
);
-- Tanimoto = 1 - (bits <%> query). Verified against a hand-computed case.
CREATE INDEX fingerprint_morgan2_hnsw ON chem.fingerprint
    USING hnsw (bits bit_jaccard_ops) WHERE kind = 'morgan2';

-- ── chem: conformers (the Conformer Explorer workstream's home) ────────────

CREATE TYPE chem.conformer_method AS ENUM (
    'etkdg_v3', 'etkdg_v2', 'experimental', 'docked', 'md_snapshot', 'manual'
);
CREATE TYPE chem.forcefield AS ENUM ('mmff94', 'mmff94s', 'uff', 'none');

CREATE TABLE chem.conformer_set (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    compound_id  uuid NOT NULL REFERENCES chem.compound(id) ON DELETE RESTRICT,
    method       chem.conformer_method NOT NULL,
    forcefield   chem.forcefield NOT NULL,
    -- A conformer set without a seed is not reproducible.
    random_seed  integer NOT NULL,
    prune_rms_a  numeric(5,3) CHECK (prune_rms_a >= 0),
    n_requested  integer NOT NULL CHECK (n_requested > 0),
    n_generated  integer NOT NULL CHECK (n_generated >= 0),
    toolkit_id   uuid NOT NULL REFERENCES meta.toolkit(id),
    generated_at timestamptz NOT NULL DEFAULT now(),
    seconds      numeric(10,3) CHECK (seconds >= 0),
    CHECK (n_generated <= n_requested)
);

CREATE TABLE chem.conformer (
    set_id       uuid NOT NULL REFERENCES chem.conformer_set(id) ON DELETE CASCADE,
    ordinal      smallint NOT NULL CHECK (ordinal >= 0),
    energy_kcal  numeric(12,4),
    rel_energy_kcal numeric(12,4),
    rmsd_to_first_a numeric(8,4) CHECK (rmsd_to_first_a >= 0),
    atom_count   smallint NOT NULL CHECK (atom_count > 0),
    coords       bytea NOT NULL,   -- float32 xyz triples, atom order = molblock
    molblock     text  NOT NULL,
    PRIMARY KEY (set_id, ordinal),
    -- 3 axes × 4 bytes per atom: catches a truncated or mis-typed buffer.
    CHECK (octet_length(coords) = atom_count * 12)
);

-- ── bio: targets and assays ────────────────────────────────────────────────

CREATE TYPE bio.target_kind AS ENUM (
    'protein', 'nucleic_acid', 'complex', 'cell_line', 'organism',
    'phenotypic', 'unknown'
);
CREATE TYPE bio.assay_kind AS ENUM (
    'biochemical', 'cell_based', 'binding', 'biophysical', 'admet',
    'phenotypic', 'in_vivo', 'computational'
);
CREATE TYPE bio.structure_method AS ENUM ('xray', 'cryoem', 'nmr', 'predicted', 'model');

CREATE TABLE bio.target (
    id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name      citext UNIQUE NOT NULL,
    kind      bio.target_kind NOT NULL,
    uniprot   citext UNIQUE,
    organism  citext,
    gene      citext,
    note      text
);

CREATE TABLE bio.structure (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pdb_id       char(4) UNIQUE CHECK (pdb_id ~ '^[0-9][A-Za-z0-9]{3}$'),
    target_id    uuid REFERENCES bio.target(id),
    method       bio.structure_method NOT NULL,
    resolution_a numeric(4,2) CHECK (resolution_a > 0),
    deposited_on date,
    source_id    uuid REFERENCES meta.source(id),
    -- A crystallographic resolution is meaningless for an NMR or predicted model.
    CHECK (method IN ('xray','cryoem') OR resolution_a IS NULL)
);

-- The deposited ligand in a structure, tied to the registered compound when
-- one exists. This is the join Dirac's ligand focus needs to leave the browser.
CREATE TABLE bio.structure_ligand (
    structure_id uuid NOT NULL REFERENCES bio.structure(id) ON DELETE CASCADE,
    comp_id      text NOT NULL,          -- PDB chemical component id, e.g. 'REA'
    auth_chain   text NOT NULL,
    auth_seq_id  integer NOT NULL,
    compound_id  uuid REFERENCES chem.compound(id),
    PRIMARY KEY (structure_id, auth_chain, auth_seq_id)
);

CREATE TABLE bio.assay (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code        citext UNIQUE NOT NULL,
    name        text NOT NULL,
    kind        bio.assay_kind NOT NULL,
    target_id   uuid REFERENCES bio.target(id),
    protocol_source_id uuid REFERENCES meta.source(id),
    description text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    -- A biochemical or binding assay with no target is unfileable.
    CHECK (kind IN ('phenotypic','admet','in_vivo') OR target_id IS NOT NULL)
);

-- ── bio: dose-response curves ──────────────────────────────────────────────

CREATE TYPE bio.fit_quality AS ENUM ('good', 'acceptable', 'poor', 'failed', 'not_fitted');

-- The curve is kept separately from its summary so a suspicious IC50 can be
-- re-examined against the points it came from, years later.
CREATE TABLE bio.dose_response (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    assay_id      uuid NOT NULL REFERENCES bio.assay(id),
    batch_id      uuid REFERENCES chem.batch(id),
    top           numeric,
    bottom        numeric,
    hill_slope    numeric(8,4),
    r_squared     numeric(6,5) CHECK (r_squared >= 0 AND r_squared <= 1),
    n_points      smallint NOT NULL CHECK (n_points >= 0),
    fit_quality   bio.fit_quality NOT NULL,
    fitted_at     timestamptz NOT NULL DEFAULT now(),
    -- A curve declared fitted must say how well.
    CHECK (fit_quality = 'not_fitted' OR r_squared IS NOT NULL)
);

CREATE TABLE bio.dose_point (
    curve_id   uuid NOT NULL REFERENCES bio.dose_response(id) ON DELETE CASCADE,
    ordinal    smallint NOT NULL,
    conc_nm    numeric NOT NULL CHECK (conc_nm >= 0),
    response   numeric NOT NULL,
    replicate  smallint NOT NULL DEFAULT 1 CHECK (replicate > 0),
    is_excluded boolean NOT NULL DEFAULT false,
    exclusion_reason text,
    PRIMARY KEY (curve_id, ordinal, replicate),
    -- Dropping a point is a scientific act and must be justified in the row.
    CHECK (is_excluded = false OR exclusion_reason IS NOT NULL)
);

-- ── bio: results (the growth table) ────────────────────────────────────────

-- Partitioned by measurement date: this is the table that reaches 10^8 rows
-- in a real programme, and the queries that matter are date-bounded.
CREATE TABLE bio.result (
    id             uuid NOT NULL DEFAULT gen_random_uuid(),
    assay_id       uuid NOT NULL REFERENCES bio.assay(id),
    compound_id    uuid NOT NULL REFERENCES chem.compound(id),
    batch_id       uuid REFERENCES chem.batch(id),
    result_type    meta.result_type NOT NULL,
    qualifier      meta.qualifier   NOT NULL DEFAULT '=',
    value_num      numeric NOT NULL,
    unit           meta.unit NOT NULL,
    -- Canonical value in the dimension's SI-ish base (nM / kcal·mol⁻¹ / K / s).
    -- Generated, so no loader can forget it and no query has to convert by hand.
    value_canonical numeric GENERATED ALWAYS AS (meta.to_canonical(value_num, unit)) STORED,
    n_replicates   smallint CHECK (n_replicates > 0),
    std_dev        numeric CHECK (std_dev >= 0),
    evidence_level meta.evidence_level NOT NULL,
    source_id      uuid REFERENCES meta.source(id),
    curve_id       uuid REFERENCES bio.dose_response(id),
    measured_on    date NOT NULL,
    recorded_at    timestamptz NOT NULL DEFAULT now(),
    recorded_by    text NOT NULL DEFAULT current_user,
    qc_pass        boolean NOT NULL DEFAULT true,
    retracted_at   timestamptz,
    retraction_reason text,
    PRIMARY KEY (id, measured_on),
    -- Dimensional legality: 'IC50 in percent' cannot be inserted.
    FOREIGN KEY (result_type, unit) REFERENCES meta.result_type_unit (result_type, unit),
    -- An in-house measurement without a physical sample is not traceable.
    CHECK (evidence_level <> 'measured' OR batch_id IS NOT NULL),
    -- An outside number without a citation is a rumour.
    CHECK (evidence_level <> 'literature' OR source_id IS NOT NULL),
    CHECK (retracted_at IS NULL OR retraction_reason IS NOT NULL)
) PARTITION BY RANGE (measured_on);

CREATE TABLE bio.result_2025 PARTITION OF bio.result
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE bio.result_2026 PARTITION OF bio.result
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE TABLE bio.result_2027 PARTITION OF bio.result
    FOR VALUES FROM ('2027-01-01') TO ('2028-01-01');
-- Nothing is silently rejected for being out of range; it lands here and is
-- visible in a monitoring query.
CREATE TABLE bio.result_default PARTITION OF bio.result DEFAULT;

CREATE INDEX result_compound   ON bio.result (compound_id, result_type)
    WHERE retracted_at IS NULL;
CREATE INDEX result_assay      ON bio.result (assay_id, measured_on DESC)
    WHERE retracted_at IS NULL;
CREATE INDEX result_batch      ON bio.result (batch_id) WHERE batch_id IS NOT NULL;

-- The batch's form must belong to the compound the result names. Denormalised
-- compound_id is what makes SAR queries fast; this trigger is what keeps it true.
CREATE FUNCTION bio.assert_batch_matches_compound() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE owner_id uuid;
BEGIN
    IF NEW.batch_id IS NULL THEN RETURN NEW; END IF;
    SELECT f.compound_id INTO owner_id
      FROM chem.batch b JOIN chem.form f ON f.id = b.form_id
     WHERE b.id = NEW.batch_id;
    IF owner_id IS DISTINCT FROM NEW.compound_id THEN
        RAISE EXCEPTION
            'batch % belongs to compound %, not % — a result may not be filed against a foreign batch',
            NEW.batch_id, owner_id, NEW.compound_id;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER result_batch_matches_compound
    BEFORE INSERT OR UPDATE ON bio.result
    FOR EACH ROW EXECUTE FUNCTION bio.assert_batch_matches_compound();

-- Aggregation that cannot hide censoring. Exact and censored observations are
-- counted separately and the geometric mean is computed over EXACT rows only;
-- has_censored says whether the honest answer is a bound rather than a value.
CREATE VIEW bio.v_compound_activity AS
SELECT r.compound_id,
       r.assay_id,
       r.result_type,
       count(*)                                          AS n_total,
       count(*) FILTER (WHERE r.qualifier = '=')         AS n_exact,
       count(*) FILTER (WHERE r.qualifier <> '=')        AS n_censored,
       bool_or(r.qualifier <> '=')                       AS has_censored,
       CASE WHEN count(*) FILTER (WHERE r.qualifier = '=' AND r.value_canonical > 0) > 0
            THEN exp(avg(ln(r.value_canonical)) FILTER (WHERE r.qualifier = '=' AND r.value_canonical > 0))
       END                                               AS geomean_canonical,
       min(r.value_canonical) FILTER (WHERE r.qualifier = '=') AS min_exact,
       max(r.value_canonical) FILTER (WHERE r.qualifier = '=') AS max_exact,
       max(r.measured_on)                                AS last_measured_on
  FROM bio.result r
 WHERE r.retracted_at IS NULL AND r.qc_pass
 GROUP BY r.compound_id, r.assay_id, r.result_type;

COMMENT ON VIEW bio.v_compound_activity IS
    'Compound-level rollup. geomean_canonical is computed over EXACT results '
    'only; when has_censored is true the honest summary is a bound, not this '
    'number. Averaging censored ">" values is how a series acquires a '
    'fictitious SAR trend.';

-- ── design: projects, series, ideas ────────────────────────────────────────

CREATE TYPE design.project_status AS ENUM (
    'exploratory', 'hit_to_lead', 'lead_optimization', 'candidate', 'paused', 'closed'
);
CREATE TYPE design.idea_status AS ENUM (
    'proposed', 'triaged', 'accepted', 'rejected', 'synthesis_requested', 'realized'
);

CREATE TABLE design.project (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code       citext UNIQUE NOT NULL,
    name       text NOT NULL,
    target_id  uuid REFERENCES bio.target(id),
    status     design.project_status NOT NULL DEFAULT 'exploratory',
    started_on date NOT NULL DEFAULT current_date,
    closed_on  date,
    CHECK (closed_on IS NULL OR closed_on >= started_on),
    CHECK ((status = 'closed') = (closed_on IS NOT NULL))
);

CREATE TABLE design.series (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id     uuid NOT NULL REFERENCES design.project(id) ON DELETE RESTRICT,
    name           citext NOT NULL,
    scaffold_smarts text,
    description    text,
    UNIQUE (project_id, name)
);

CREATE TABLE design.project_compound (
    project_id  uuid NOT NULL REFERENCES design.project(id) ON DELETE RESTRICT,
    compound_id uuid NOT NULL REFERENCES chem.compound(id) ON DELETE RESTRICT,
    series_id   uuid REFERENCES design.series(id),
    added_at    timestamptz NOT NULL DEFAULT now(),
    added_by    text NOT NULL DEFAULT current_user,
    PRIMARY KEY (project_id, compound_id)
);

CREATE TABLE design.idea (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id   uuid NOT NULL REFERENCES design.project(id),
    series_id    uuid REFERENCES design.series(id),
    smiles       text NOT NULL,
    rationale    text NOT NULL,     -- why THIS molecule; the DMTA record
    status       design.idea_status NOT NULL DEFAULT 'proposed',
    compound_id  uuid REFERENCES chem.compound(id),   -- set when it exists
    proposed_by  text NOT NULL DEFAULT current_user,
    proposed_at  timestamptz NOT NULL DEFAULT now(),
    decided_at   timestamptz,
    -- A realized idea must point at the compound that realized it.
    CHECK ((status = 'realized') = (compound_id IS NOT NULL)),
    CHECK (status IN ('proposed','triaged') OR decided_at IS NOT NULL)
);

-- ── design: pharmacophore models (the Designer facet's persistence) ────────

-- Kept byte-compatible with the facet's exported JSON:
-- DesignerFeatureKind = 'hba' | 'hbd' | 'aromatic' | 'hydrophobic'.
CREATE TYPE design.pharmacophore_kind AS ENUM ('hba', 'hbd', 'aromatic', 'hydrophobic');
CREATE TYPE design.feature_origin AS ENUM ('ligand', 'user');

CREATE TABLE design.pharmacophore_model (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name              citext NOT NULL,
    project_id        uuid REFERENCES design.project(id),
    -- Where the model came from: a PDB structure's ligand, or nothing (built by hand).
    structure_id      uuid REFERENCES bio.structure(id),
    source_pdb_id     char(4),
    source_ligand_label text,
    source_compound_id uuid REFERENCES chem.compound(id),
    was_edited        boolean NOT NULL,   -- the facet's dirty flag; perception vs chemist
    model_json        jsonb NOT NULL,     -- the exact exported document, v1
    created_at        timestamptz NOT NULL DEFAULT now(),
    created_by        text NOT NULL DEFAULT current_user,
    UNIQUE (name, created_by, created_at),
    CHECK (model_json->>'format' = 'dirac-pharmacophore-model')
);

CREATE TABLE design.pharmacophore_feature (
    model_id   uuid NOT NULL REFERENCES design.pharmacophore_model(id) ON DELETE CASCADE,
    feature_no smallint NOT NULL CHECK (feature_no > 0),
    kind       design.pharmacophore_kind NOT NULL,
    x numeric(9,4) NOT NULL,
    y numeric(9,4) NOT NULL,
    z numeric(9,4) NOT NULL,
    dx numeric(7,4), dy numeric(7,4), dz numeric(7,4),
    -- Same bounds the UI slider enforces; the DB is where it becomes true.
    radius_a   numeric(4,2) NOT NULL CHECK (radius_a >= 0.5 AND radius_a <= 3.0),
    enabled    boolean NOT NULL DEFAULT true,
    origin     design.feature_origin NOT NULL,
    PRIMARY KEY (model_id, feature_no),
    -- Directional kinds carry a direction; hydrophobic spheres do not.
    CHECK ((kind = 'hydrophobic') OR (dx IS NOT NULL AND dy IS NOT NULL AND dz IS NOT NULL))
);

CREATE TYPE design.screening_mode AS ENUM ('topological', 'shape_3d', 'docking');

CREATE TABLE design.screening_run (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    model_id     uuid NOT NULL REFERENCES design.pharmacophore_model(id) ON DELETE RESTRICT,
    mode         design.screening_mode NOT NULL,
    library_name citext NOT NULL,
    library_size integer NOT NULL CHECK (library_size >= 0),
    required     jsonb  NOT NULL,     -- {hba: n, hbd: n, aromatic: n, hydrophobic: n}
    smarts_filter text,
    n_screened   integer NOT NULL CHECK (n_screened >= 0),
    n_matched    integer NOT NULL CHECK (n_matched >= 0),
    engine_toolkit_id uuid NOT NULL REFERENCES meta.toolkit(id),
    ran_at       timestamptz NOT NULL DEFAULT now(),
    seconds      numeric(10,3) CHECK (seconds >= 0),
    CHECK (n_matched <= n_screened),
    CHECK (n_screened <= library_size)
);

CREATE TABLE design.screening_hit (
    run_id      uuid NOT NULL REFERENCES design.screening_run(id) ON DELETE CASCADE,
    compound_id uuid NOT NULL REFERENCES chem.compound(id),
    matched     boolean NOT NULL,
    counts      jsonb NOT NULL,   -- per-kind counts actually found
    tanimoto_to_source numeric(5,4) CHECK (tanimoto_to_source >= 0 AND tanimoto_to_source <= 1),
    rank        integer CHECK (rank > 0),
    PRIMARY KEY (run_id, compound_id)
);

-- ── app: what the client persists ──────────────────────────────────────────

-- Content-addressed store. Cubes, molblocks and exports are deduplicated by
-- their own hash, so re-running an identical calculation costs one row.
CREATE TABLE app.blob (
    sha256     bytea PRIMARY KEY CHECK (octet_length(sha256) = 32),
    media_type text NOT NULL,
    byte_len   integer NOT NULL CHECK (byte_len > 0),
    bytes      bytea NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (octet_length(bytes) = byte_len),
    CHECK (digest(bytes, 'sha256') = sha256)   -- the store cannot hold a lie
);

CREATE TYPE app.field_kind AS ENUM ('mep', 'mep_qm', 'homo', 'lumo', 'density');

-- Persistent replacement for the fields backend's in-memory SCF cache: an
-- SCF that cost 40 seconds should survive a server restart.
CREATE TABLE app.field_cube (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    molfile_sha256 bytea NOT NULL CHECK (octet_length(molfile_sha256) = 32),
    kind           app.field_kind NOT NULL,
    basis          text NOT NULL CHECK (basis IN ('sto-3g','6-31g','6-31g*','def2-svp','none')),
    blob_sha256    bytea NOT NULL REFERENCES app.blob(sha256),
    method         text CHECK (method IN ('RHF','UHF','gasteiger')),
    scf_energy_ha  numeric(18,10),
    converged      boolean,
    n_atoms        smallint CHECK (n_atoms > 0),
    n_basis        integer  CHECK (n_basis > 0),
    homo_ev        numeric(10,4),
    lumo_ev        numeric(10,4),
    seconds        numeric(10,3) CHECK (seconds >= 0),
    toolkit_id     uuid NOT NULL REFERENCES meta.toolkit(id),
    computed_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (molfile_sha256, kind, basis),
    -- A quantum field may only be cached if its SCF converged: the backend
    -- refuses to ship a decorative field, and the cache must not resurrect one.
    CHECK (kind = 'mep' OR converged IS TRUE)
);

CREATE TABLE app.workspace (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name         citext NOT NULL,
    owner        text NOT NULL DEFAULT current_user,
    structure_ref text NOT NULL,        -- PDB id or uploaded blob sha (hex)
    layer_state  jsonb NOT NULL,        -- enabled semantic/VFX layer ids
    camera       jsonb,
    ligand_focus jsonb,
    saved_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (owner, name)
);

-- ── audit: nothing changes without a record ────────────────────────────────

CREATE TABLE audit.row_history (
    id         bigserial PRIMARY KEY,
    at         timestamptz NOT NULL DEFAULT now(),
    actor      text NOT NULL DEFAULT current_user,
    table_name text NOT NULL,
    op         char(1) NOT NULL CHECK (op IN ('I','U','D')),
    row_key    text,
    before     jsonb,
    after      jsonb
);
CREATE INDEX row_history_table_at ON audit.row_history (table_name, at DESC);

CREATE FUNCTION audit.track() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE key_text text;
BEGIN
    key_text := COALESCE(
        to_jsonb(COALESCE(NEW, OLD))->>'id',
        to_jsonb(COALESCE(NEW, OLD))->>'compound_id'
    );
    INSERT INTO audit.row_history (table_name, op, row_key, before, after)
    VALUES (TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME,
            left(TG_OP, 1), key_text,
            CASE WHEN TG_OP IN ('UPDATE','DELETE') THEN to_jsonb(OLD) END,
            CASE WHEN TG_OP IN ('INSERT','UPDATE') THEN to_jsonb(NEW) END);
    RETURN COALESCE(NEW, OLD);
END;
$$;

CREATE TRIGGER audit_compound AFTER INSERT OR UPDATE OR DELETE ON chem.compound
    FOR EACH ROW EXECUTE FUNCTION audit.track();
CREATE TRIGGER audit_batch AFTER INSERT OR UPDATE OR DELETE ON chem.batch
    FOR EACH ROW EXECUTE FUNCTION audit.track();
CREATE TRIGGER audit_result AFTER INSERT OR UPDATE OR DELETE ON bio.result
    FOR EACH ROW EXECUTE FUNCTION audit.track();
CREATE TRIGGER audit_model AFTER INSERT OR UPDATE OR DELETE ON design.pharmacophore_model
    FOR EACH ROW EXECUTE FUNCTION audit.track();

-- ── roles: the application may not delete history ──────────────────────────

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dirac_app') THEN
        CREATE ROLE dirac_app NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dirac_ro') THEN
        CREATE ROLE dirac_ro NOLOGIN;
    END IF;
END $$;

GRANT USAGE ON SCHEMA meta, chem, bio, design, app, audit TO dirac_app, dirac_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA meta, chem, bio, design, app, audit TO dirac_ro, dirac_app;
GRANT INSERT, UPDATE ON ALL TABLES IN SCHEMA chem, bio, design, app, meta TO dirac_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA chem, audit TO dirac_app;
-- No DELETE anywhere for the application: retraction is a column, not an
-- absence. Purging is a migration-role operation with a human behind it.
REVOKE DELETE ON ALL TABLES IN SCHEMA chem, bio, design, meta FROM dirac_app;

INSERT INTO meta.migration (filename, sha256)
VALUES ('001_core.sql', digest('001_core.sql', 'sha256'))
ON CONFLICT (filename) DO NOTHING;

COMMIT;
