-- Attack suite for the Dirac schema.
--
-- A schema is a set of claims about what cannot happen. This file tries to
-- make each of them happen. Run after every migration:
--
--     psql -U ivan -d dirac -v ON_ERROR_STOP=1 -f backend/db/check_constraints.sql
--
-- Two properties make this a test rather than a ritual:
--
--   1. EXPECTED SQLSTATE. A gate passes only when the statement is rejected
--      by the class of error it was supposed to trigger. Without this, a typo
--      in the test SQL (42703 undefined_column) scores as a successful guard,
--      and the suite becomes a check that cannot fail.
--   2. POSITIVE CONTROLS. Legal inserts must SUCCEED. A schema that rejects
--      everything would otherwise post a perfect score.
--
-- The whole run is one transaction and ends in ROLLBACK: the database is left
-- exactly as it was found.

BEGIN;

CREATE TEMP TABLE gate (
    id serial, name text, kind text, expect text, got text, ok boolean
) ON COMMIT DROP;

CREATE FUNCTION pg_temp.expect_reject(gate_name text, expected_sqlstate text, stmt text)
RETURNS void LANGUAGE plpgsql AS $fn$
BEGIN
    BEGIN
        EXECUTE stmt;
        INSERT INTO gate (name, kind, expect, got, ok)
        VALUES (gate_name, 'reject', expected_sqlstate, 'ACCEPTED', false);
    EXCEPTION WHEN others THEN
        INSERT INTO gate (name, kind, expect, got, ok)
        VALUES (gate_name, 'reject', expected_sqlstate, SQLSTATE,
                SQLSTATE LIKE expected_sqlstate || '%');
    END;
END;
$fn$;

CREATE FUNCTION pg_temp.expect_accept(gate_name text, stmt text)
RETURNS void LANGUAGE plpgsql AS $fn$
BEGIN
    BEGIN
        EXECUTE stmt;
        INSERT INTO gate (name, kind, expect, got, ok) VALUES (gate_name, 'accept', 'ok', 'ok', true);
    EXCEPTION WHEN others THEN
        INSERT INTO gate (name, kind, expect, got, ok)
        VALUES (gate_name, 'accept', 'ok', SQLSTATE || ' ' || SQLERRM, false);
    END;
END;
$fn$;

-- ── fixtures ───────────────────────────────────────────────────────────────

CREATE TEMP TABLE fx AS
SELECT (SELECT id FROM chem.compound ORDER BY registry_id LIMIT 1)      AS compound_a,
       (SELECT id FROM chem.compound ORDER BY registry_id DESC LIMIT 1) AS compound_b,
       (SELECT id FROM chem.standardizer LIMIT 1)                       AS std,
       (SELECT id FROM meta.toolkit LIMIT 1)                            AS toolkit;

INSERT INTO bio.target (id, name, kind, organism)
VALUES ('11111111-1111-1111-1111-111111111111', 'TEST-KINASE', 'protein', 'Homo sapiens');
INSERT INTO bio.assay (id, code, name, kind, target_id)
VALUES ('22222222-2222-2222-2222-222222222222', 'TEST-IC50', 'Test biochemical IC50',
        'biochemical', '11111111-1111-1111-1111-111111111111');
INSERT INTO meta.source (id, kind, locator)
VALUES ('33333333-3333-3333-3333-333333333333', 'doi', '10.1000/test');

-- Two forms + batches, on two DIFFERENT compounds, for the cross-filing attack.
INSERT INTO chem.form (id, compound_id, form_kind, full_inchikey, components, mw_form)
SELECT '44444444-4444-4444-4444-444444444444', compound_a, 'neutral',
       'AAAAAAAAAAAAAA-BBBBBBBBBB-C', '[{"smiles":"X","stoichiometry":1}]'::jsonb, 200.0 FROM fx;
INSERT INTO chem.form (id, compound_id, form_kind, full_inchikey, components, mw_form)
SELECT '55555555-5555-5555-5555-555555555555', compound_b, 'salt',
       'DDDDDDDDDDDDDD-EEEEEEEEEE-F', '[{"smiles":"Y","stoichiometry":1}]'::jsonb, 236.5 FROM fx;
INSERT INTO chem.batch (id, form_id, batch_code, provenance, purity_pct, purity_method)
VALUES ('66666666-6666-6666-6666-666666666666', '44444444-4444-4444-4444-444444444444',
        'TEST-BATCH-A', 'internal_synthesis', 98.5, 'hplc_uv'),
       ('77777777-7777-7777-7777-777777777777', '55555555-5555-5555-5555-555555555555',
        'TEST-BATCH-B', 'purchase', 95.0, 'lcms');

-- ── positive controls: the schema must accept correct chemistry ────────────

SELECT pg_temp.expect_accept('P1 legal IC50 in nM against own batch', $$
    INSERT INTO bio.result (assay_id, compound_id, batch_id, result_type, qualifier,
                            value_num, unit, evidence_level, measured_on, n_replicates)
    SELECT '22222222-2222-2222-2222-222222222222', compound_a,
           '66666666-6666-6666-6666-666666666666', 'IC50', '=', 45.0, 'nM',
           'measured', DATE '2026-06-01', 3 FROM fx $$);

SELECT pg_temp.expect_accept('P2 censored result (>) is storable', $$
    INSERT INTO bio.result (assay_id, compound_id, batch_id, result_type, qualifier,
                            value_num, unit, evidence_level, measured_on)
    SELECT '22222222-2222-2222-2222-222222222222', compound_a,
           '66666666-6666-6666-6666-666666666666', 'IC50', '>', 10.0, 'uM',
           'measured', DATE '2026-06-02' FROM fx $$);

SELECT pg_temp.expect_accept('P3 literature result with citation, no batch', $$
    INSERT INTO bio.result (assay_id, compound_id, result_type, value_num, unit,
                            evidence_level, source_id, measured_on)
    SELECT '22222222-2222-2222-2222-222222222222', compound_a, 'IC50', 12.0, 'uM',
           'literature', '33333333-3333-3333-3333-333333333333', DATE '2026-05-01' FROM fx $$);

SELECT pg_temp.expect_accept('P4 logP in log units', $$
    INSERT INTO bio.result (assay_id, compound_id, batch_id, result_type, value_num, unit,
                            evidence_level, measured_on)
    SELECT '22222222-2222-2222-2222-222222222222', compound_a,
           '66666666-6666-6666-6666-666666666666', 'logP', 2.3, 'log_unit',
           'measured', DATE '2026-06-03' FROM fx $$);

-- ── unit and dimension attacks ─────────────────────────────────────────────

SELECT pg_temp.expect_reject('A1 IC50 expressed in percent', '23503', $$
    INSERT INTO bio.result (assay_id, compound_id, batch_id, result_type, value_num, unit,
                            evidence_level, measured_on)
    SELECT '22222222-2222-2222-2222-222222222222', compound_a,
           '66666666-6666-6666-6666-666666666666', 'IC50', 45.0, 'percent',
           'measured', DATE '2026-06-04' FROM fx $$);

SELECT pg_temp.expect_reject('A2 logP expressed in nM', '23503', $$
    INSERT INTO bio.result (assay_id, compound_id, batch_id, result_type, value_num, unit,
                            evidence_level, measured_on)
    SELECT '22222222-2222-2222-2222-222222222222', compound_a,
           '66666666-6666-6666-6666-666666666666', 'logP', 2.3, 'nM',
           'measured', DATE '2026-06-05' FROM fx $$);

-- ── traceability attacks ───────────────────────────────────────────────────

SELECT pg_temp.expect_reject('A3 in-house measurement with no physical batch', '23514', $$
    INSERT INTO bio.result (assay_id, compound_id, result_type, value_num, unit,
                            evidence_level, measured_on)
    SELECT '22222222-2222-2222-2222-222222222222', compound_a, 'IC50', 45.0, 'nM',
           'measured', DATE '2026-06-06' FROM fx $$);

SELECT pg_temp.expect_reject('A4 literature number with no citation', '23514', $$
    INSERT INTO bio.result (assay_id, compound_id, result_type, value_num, unit,
                            evidence_level, measured_on)
    SELECT '22222222-2222-2222-2222-222222222222', compound_a, 'IC50', 45.0, 'nM',
           'literature', DATE '2026-06-07' FROM fx $$);

-- The denormalised compound_id is what makes SAR queries fast; this is the
-- attack that proves it cannot drift from the batch it claims.
SELECT pg_temp.expect_reject('A5 result filed against another compound''s batch', 'P0001', $$
    INSERT INTO bio.result (assay_id, compound_id, batch_id, result_type, value_num, unit,
                            evidence_level, measured_on)
    SELECT '22222222-2222-2222-2222-222222222222', compound_a,
           '77777777-7777-7777-7777-777777777777', 'IC50', 45.0, 'nM',
           'measured', DATE '2026-06-08' FROM fx $$);

-- ── identity attacks ───────────────────────────────────────────────────────

SELECT pg_temp.expect_reject('A6 malformed InChIKey', '23514', $$
    INSERT INTO chem.compound (inchikey, inchi, smiles, formula, mw_monoisotopic, stereo, standardizer_id)
    SELECT 'NOT-AN-INCHIKEY-AT-ALL-XXXX', 'InChI=1S/CH4/h1H4', 'C', 'CH4', 16.031, 'no_stereocenters', std FROM fx $$);

SELECT pg_temp.expect_reject('A7 duplicate parent registration', '23505', $$
    INSERT INTO chem.compound (inchikey, inchi, smiles, formula, mw_monoisotopic, stereo, standardizer_id)
    SELECT c.inchikey, c.inchi, c.smiles, c.formula, c.mw_monoisotopic, c.stereo, c.standardizer_id
      FROM chem.compound c, fx WHERE c.id = fx.compound_a $$);

SELECT pg_temp.expect_reject('A8 purity number with no method', '23514', $$
    INSERT INTO chem.batch (form_id, batch_code, provenance, purity_pct)
    VALUES ('44444444-4444-4444-4444-444444444444', 'TEST-BATCH-C', 'purchase', 99.0) $$);

SELECT pg_temp.expect_reject('A9 retraction with no reason', '23514', $$
    UPDATE chem.compound SET retracted_at = now()
     WHERE id = (SELECT compound_a FROM fx) $$);

-- ── Dirac artefact attacks ─────────────────────────────────────────────────

INSERT INTO design.pharmacophore_model (id, name, was_edited, model_json)
VALUES ('88888888-8888-8888-8888-888888888888', 'test-model', false,
        '{"format":"dirac-pharmacophore-model","version":1}'::jsonb);

SELECT pg_temp.expect_accept('P5 legal pharmacophore feature', $$
    INSERT INTO design.pharmacophore_feature (model_id, feature_no, kind, x, y, z, dx, dy, dz, radius_a, origin)
    VALUES ('88888888-8888-8888-8888-888888888888', 1, 'hba', 1, 2, 3, 0, 0, 1, 1.0, 'ligand') $$);

SELECT pg_temp.expect_reject('A10 tolerance radius outside the UI bounds', '23514', $$
    INSERT INTO design.pharmacophore_feature (model_id, feature_no, kind, x, y, z, dx, dy, dz, radius_a, origin)
    VALUES ('88888888-8888-8888-8888-888888888888', 2, 'hba', 1, 2, 3, 0, 0, 1, 5.0, 'ligand') $$);

SELECT pg_temp.expect_reject('A11 directional feature with no direction', '23514', $$
    INSERT INTO design.pharmacophore_feature (model_id, feature_no, kind, x, y, z, radius_a, origin)
    VALUES ('88888888-8888-8888-8888-888888888888', 3, 'aromatic', 1, 2, 3, 1.2, 'user') $$);

SELECT pg_temp.expect_reject('A12 model JSON of the wrong format', '23514', $$
    INSERT INTO design.pharmacophore_model (name, was_edited, model_json)
    VALUES ('bogus', false, '{"format":"something-else"}'::jsonb) $$);

SELECT pg_temp.expect_reject('A13 screening run matching more than it screened', '23514', $$
    INSERT INTO design.screening_run (model_id, mode, library_name, library_size, required,
                                      n_screened, n_matched, engine_toolkit_id)
    SELECT '88888888-8888-8888-8888-888888888888', 'topological', 'test', 10, '{}'::jsonb,
           10, 11, toolkit FROM fx $$);

-- The content-addressed store must not be able to hold a mislabelled blob.
SELECT pg_temp.expect_accept('P6 honest blob', $$
    INSERT INTO app.blob (sha256, media_type, byte_len, bytes)
    VALUES (digest('hello', 'sha256'), 'text/plain', 5, 'hello'::bytea) $$);

SELECT pg_temp.expect_reject('A14 blob whose hash does not match its bytes', '23514', $$
    INSERT INTO app.blob (sha256, media_type, byte_len, bytes)
    VALUES (digest('hello', 'sha256'), 'text/plain', 7, 'goodbye'::bytea) $$);

SELECT pg_temp.expect_reject('A15 cached quantum field whose SCF never converged', '23514', $$
    INSERT INTO app.field_cube (molfile_sha256, kind, basis, blob_sha256, converged,
                                scf_reference, scf_converger, toolkit_id)
    SELECT digest('mol', 'sha256'), 'homo', 'sto-3g', digest('hello', 'sha256'), false,
           'RHF', 'diis', toolkit FROM fx $$);

-- The exact case the fields workstream had just unblocked when this schema was
-- written: an Fe-heme HOMO that only converged under second-order SCF. The
-- original free-text method CHECK would have rejected it.
SELECT pg_temp.expect_accept('P10 SOSCF-rescued quantum field caches', $$
    INSERT INTO app.field_cube (molfile_sha256, kind, basis, blob_sha256, converged,
                                scf_reference, scf_converger, scf_energy_ha, n_atoms,
                                n_basis, seconds, toolkit_id)
    SELECT digest('heme', 'sha256'), 'homo', 'sto-3g', digest('hello', 'sha256'), true,
           'RHF', 'soscf', -2244.123456, 75, 430, 365.0, toolkit FROM fx $$);

SELECT pg_temp.expect_reject('A21 quantum field claiming no SCF reference', '23514', $$
    INSERT INTO app.field_cube (molfile_sha256, kind, basis, blob_sha256, converged,
                                scf_reference, scf_converger, toolkit_id)
    SELECT digest('mol2', 'sha256'), 'lumo', 'sto-3g', digest('hello', 'sha256'), true,
           'none', 'none', toolkit FROM fx $$);

SELECT pg_temp.expect_reject('A22 classical MEP borrowing a quantum reference', '23514', $$
    INSERT INTO app.field_cube (molfile_sha256, kind, basis, blob_sha256,
                                scf_reference, scf_converger, toolkit_id)
    SELECT digest('mol3', 'sha256'), 'mep', 'none', digest('hello', 'sha256'),
           'RHF', 'diis', toolkit FROM fx $$);

-- A new solver must arrive as a migration, never as an unrecognised string.
SELECT pg_temp.expect_reject('A23 unknown SCF method label', 'P0001', $$
    SELECT app.parse_scf_method('B3LYP/def2-TZVP') $$);

DO $$
DECLARE label text;
BEGIN
    SELECT app.scf_method_label((p).scf_reference, (p).scf_converger) INTO label
      FROM (SELECT app.parse_scf_method('RHF+SOSCF') AS p) x;
    INSERT INTO gate (name, kind, expect, got, ok)
    VALUES ('P11 SCF label survives split and reassembly', 'compute',
            'RHF+SOSCF', label, label = 'RHF+SOSCF');
END $$;

SELECT pg_temp.expect_reject('A16 conformer whose coordinate buffer is truncated', '23514', $$
    WITH s AS (
        INSERT INTO chem.conformer_set (compound_id, method, forcefield, random_seed,
                                        n_requested, n_generated, toolkit_id)
        SELECT compound_a, 'etkdg_v3', 'mmff94s', 42, 10, 10, toolkit FROM fx
        RETURNING id)
    INSERT INTO chem.conformer (set_id, ordinal, atom_count, coords, molblock)
    SELECT id, 0, 10, '\x0011'::bytea, 'molblock' FROM s $$);

-- ── structural / lifecycle attacks ─────────────────────────────────────────

SELECT pg_temp.expect_reject('A17 crystallographic resolution on an NMR structure', '23514', $$
    INSERT INTO bio.structure (pdb_id, method, resolution_a) VALUES ('9xyz', 'nmr', 1.8) $$);

SELECT pg_temp.expect_reject('A18 closed project with no closing date', '23514', $$
    INSERT INTO design.project (code, name, status) VALUES ('TESTP', 'Test', 'closed') $$);

SELECT pg_temp.expect_reject('A19 excluded dose point with no justification', '23514', $$
    WITH c AS (
        INSERT INTO bio.dose_response (assay_id, n_points, fit_quality)
        VALUES ('22222222-2222-2222-2222-222222222222', 8, 'not_fitted') RETURNING id)
    INSERT INTO bio.dose_point (curve_id, ordinal, conc_nm, response, is_excluded)
    SELECT id, 1, 100, 45, true FROM c $$);

-- ── numeric hygiene (found by brute force, held here) ──────────────────────
-- NaN compares GREATER than every number in PostgreSQL, so it passes any
-- one-sided `>= 0` check and then passes the activity view's `> 0` filter and
-- poisons the geometric mean for the whole compound. These five gates are the
-- adversarial sweep that found it, frozen.

SELECT pg_temp.expect_reject('A24 NaN as a measured value', '23514', $$
    INSERT INTO bio.result (assay_id, compound_id, batch_id, result_type, value_num, unit,
                            evidence_level, measured_on)
    SELECT '22222222-2222-2222-2222-222222222222', compound_a,
           '66666666-6666-6666-6666-666666666666', 'IC50', 'NaN'::numeric, 'nM',
           'measured', DATE '2026-06-10' FROM fx $$);

SELECT pg_temp.expect_reject('A25 Infinity as a measured value', '23514', $$
    INSERT INTO bio.result (assay_id, compound_id, batch_id, result_type, value_num, unit,
                            evidence_level, measured_on)
    SELECT '22222222-2222-2222-2222-222222222222', compound_a,
           '66666666-6666-6666-6666-666666666666', 'IC50', 'Infinity'::numeric, 'nM',
           'measured', DATE '2026-06-11' FROM fx $$);

SELECT pg_temp.expect_reject('A26 negative potency', '23514', $$
    INSERT INTO bio.result (assay_id, compound_id, batch_id, result_type, value_num, unit,
                            evidence_level, measured_on)
    SELECT '22222222-2222-2222-2222-222222222222', compound_a,
           '66666666-6666-6666-6666-666666666666', 'IC50', -45, 'nM',
           'measured', DATE '2026-06-12' FROM fx $$);

SELECT pg_temp.expect_reject('A27 zero potency', '23514', $$
    INSERT INTO bio.result (assay_id, compound_id, batch_id, result_type, value_num, unit,
                            evidence_level, measured_on)
    SELECT '22222222-2222-2222-2222-222222222222', compound_a,
           '66666666-6666-6666-6666-666666666666', 'IC50', 0, 'nM',
           'measured', DATE '2026-06-13' FROM fx $$);

SELECT pg_temp.expect_reject('A28 NaN descriptor', '23514', $$
    INSERT INTO chem.descriptor (compound_id, name, value, toolkit_id)
    SELECT compound_a, 'qed', 'NaN'::numeric, toolkit FROM fx $$);

-- A percent inhibition legitimately reads negative (activation) or above 100
-- (noise). Over-constraining is its own way of losing real data.
SELECT pg_temp.expect_accept('P12 negative percent inhibition is real data', $$
    INSERT INTO bio.result (assay_id, compound_id, batch_id, result_type, value_num, unit,
                            evidence_level, measured_on)
    SELECT '22222222-2222-2222-2222-222222222222', compound_a,
           '66666666-6666-6666-6666-666666666666', 'percent_inhibition', -12.5, 'percent',
           'measured', DATE '2026-06-14' FROM fx $$);

-- ── the generated canonical value must actually convert ────────────────────

DO $$
DECLARE v numeric;
BEGIN
    SELECT value_canonical INTO v FROM bio.result
     WHERE unit = 'uM' AND value_num = 10.0 AND qualifier = '>' LIMIT 1;
    INSERT INTO gate (name, kind, expect, got, ok)
    VALUES ('P7 generated column converts 10 uM to 10000 nM', 'compute',
            '10000', COALESCE(v::text, 'NULL'), v = 10000);
END $$;

-- ── privilege: the application may not delete facts ────────────────────────

DO $$
DECLARE can_delete boolean;
BEGIN
    SELECT has_table_privilege('dirac_app', 'chem.compound', 'DELETE') INTO can_delete;
    INSERT INTO gate (name, kind, expect, got, ok)
    VALUES ('A20 dirac_app cannot DELETE compounds', 'privilege',
            'false', can_delete::text, can_delete = false);
    SELECT has_table_privilege('dirac_app', 'chem.compound', 'INSERT') INTO can_delete;
    INSERT INTO gate (name, kind, expect, got, ok)
    VALUES ('P8 dirac_app can still INSERT compounds', 'privilege',
            'true', can_delete::text, can_delete = true);
END $$;

-- ── the audit trail must have recorded the legal writes ────────────────────

DO $$
DECLARE n integer;
BEGIN
    SELECT count(*) INTO n FROM audit.row_history
     WHERE table_name = 'bio.result' AND op = 'I';
    INSERT INTO gate (name, kind, expect, got, ok)
    VALUES ('P9 audit trail captured the accepted results', 'audit',
            '>=4', n::text, n >= 4);
END $$;

-- ── report ─────────────────────────────────────────────────────────────────

SELECT lpad(id::text, 2) AS n, name,
       CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS status,
       expect, got
  FROM gate ORDER BY id;

DO $$
DECLARE failed integer; total integer;
BEGIN
    SELECT count(*) FILTER (WHERE NOT ok), count(*) INTO failed, total FROM gate;
    IF failed > 0 THEN
        RAISE EXCEPTION '% of % schema gates FAILED', failed, total;
    END IF;
    RAISE NOTICE 'all % schema gates passed (% rejections, % positive controls)',
        total,
        (SELECT count(*) FROM gate WHERE kind = 'reject'),
        (SELECT count(*) FROM gate WHERE kind <> 'reject');
END $$;

ROLLBACK;
