-- 048 · align the public ObjectKind vocabulary with scientific durable objects.
--
-- Scientific Motif objects were introduced behind a domain-specific enum in
-- 037.  Public ObjectRef validation is governed by app.object_kind, so the two
-- vocabularies must contain the same canonical values exposed by
-- contracts/domain/object-kinds.json.
BEGIN;

ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'protein_structure_source';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'prepared_receptor_state';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'binding_site_hypothesis';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'complex_hypothesis';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'pose_hypothesis';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'pose_ensemble';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'conformer_ensemble';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'conformer_hypothesis';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'submitted_compound_record';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'chemical_entity';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'chemical_state_ensemble';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'chemical_microstate';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'parameterized_system';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'simulation_run';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'free_energy_transformation';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'evidence_item';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'evidence_snapshot';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'method_run';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'method_outcome';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'quality_assessment';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'routing_action';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'decision_snapshot';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'resource_lease';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'method_release';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'policy_release';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'condition';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'assumption';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'transformation';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'dataset_snapshot';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'model_release';

INSERT INTO meta.migration (filename,sha256,content_sha256,hash_source)
VALUES ('048_object_kind_scientific_alignment.sql','\xeedc2d832ba50a8870535cb02b5e3c77588edff80fba28de21026a91228ab26f'::bytea,
        '\xeedc2d832ba50a8870535cb02b5e3c77588edff80fba28de21026a91228ab26f'::bytea,'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
