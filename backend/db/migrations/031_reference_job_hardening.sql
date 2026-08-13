-- 031 · Adversarial hardening for Program-scoped reference jobs.
--
-- The first hostile pass found that several globally unique keys accidentally
-- collapsed independent Programs and that raw SQL could cross Program-owned
-- work, experiment, dataset, observation, and snapshot boundaries. Keep global
-- scientific identities global, but scope operational records to their Program.

BEGIN;

ALTER TABLE bio.disease
    DROP CONSTRAINT IF EXISTS disease_ontology_namespace_ontology_id_key;
CREATE UNIQUE INDEX IF NOT EXISTS disease_ontology_identity_unique
    ON bio.disease(ontology_namespace, ontology_id)
    WHERE ontology_namespace IS NOT NULL AND ontology_id IS NOT NULL;

ALTER TABLE bio.experiment
    DROP CONSTRAINT IF EXISTS experiment_experiment_key_key;
ALTER TABLE bio.experiment
    ADD CONSTRAINT experiment_program_key_unique UNIQUE (program_id, experiment_key);

ALTER TABLE app.dataset_version
    DROP CONSTRAINT IF EXISTS dataset_version_dataset_key_version_key,
    DROP CONSTRAINT IF EXISTS dataset_version_dataset_key_digest_key;
DROP INDEX IF EXISTS app.dataset_version_one_current;
ALTER TABLE app.dataset_version
    ADD CONSTRAINT dataset_version_program_key_version_unique
        UNIQUE (program_id, dataset_key, version),
    ADD CONSTRAINT dataset_version_program_key_digest_unique
        UNIQUE (program_id, dataset_key, digest);
CREATE UNIQUE INDEX dataset_version_one_current
    ON app.dataset_version(program_id, dataset_key)
    WHERE status='committed';

ALTER TABLE bio.structure_observation
    DROP CONSTRAINT IF EXISTS structure_observation_observation_key_key;
ALTER TABLE bio.structure_observation
    ADD CONSTRAINT structure_observation_program_key_unique
        UNIQUE (program_id, observation_key);

CREATE FUNCTION bio.assert_experiment_program_scope() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE owner uuid;
BEGIN
    SELECT program_id INTO owner FROM design.program_work_item WHERE id=NEW.work_item_id;
    IF owner IS DISTINCT FROM NEW.program_id THEN
        RAISE EXCEPTION 'experiment work item must belong to the same Program';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER experiment_program_scope_guard
    BEFORE INSERT OR UPDATE OF program_id,work_item_id ON bio.experiment
    FOR EACH ROW EXECUTE FUNCTION bio.assert_experiment_program_scope();

CREATE FUNCTION app.assert_dataset_program_scope() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE owner uuid;
BEGIN
    IF NEW.experiment_id IS NULL THEN RETURN NEW; END IF;
    SELECT program_id INTO owner FROM bio.experiment WHERE id=NEW.experiment_id;
    IF owner IS DISTINCT FROM NEW.program_id THEN
        RAISE EXCEPTION 'dataset experiment must belong to the same Program';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER dataset_program_scope_guard
    BEFORE INSERT OR UPDATE OF program_id,experiment_id ON app.dataset_version
    FOR EACH ROW EXECUTE FUNCTION app.assert_dataset_program_scope();

CREATE FUNCTION app.assert_dataset_parent_scope() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE child_program uuid; parent_program uuid;
BEGIN
    SELECT program_id INTO child_program FROM app.dataset_version WHERE id=NEW.dataset_version_id;
    SELECT program_id INTO parent_program FROM app.dataset_version WHERE id=NEW.parent_dataset_version_id;
    IF child_program IS DISTINCT FROM parent_program THEN
        RAISE EXCEPTION 'dataset parent must belong to the same Program';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER dataset_parent_scope_guard
    BEFORE INSERT OR UPDATE ON app.dataset_version_parent
    FOR EACH ROW EXECUTE FUNCTION app.assert_dataset_parent_scope();

CREATE FUNCTION bio.assert_structure_observation_program_scope() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE dataset_program uuid; experiment_program uuid;
BEGIN
    SELECT program_id INTO dataset_program
      FROM app.dataset_version WHERE id=NEW.source_dataset_version_id;
    IF dataset_program IS DISTINCT FROM NEW.program_id THEN
        RAISE EXCEPTION 'structure observation dataset must belong to the same Program';
    END IF;
    IF NEW.experiment_id IS NOT NULL THEN
        SELECT program_id INTO experiment_program FROM bio.experiment WHERE id=NEW.experiment_id;
        IF experiment_program IS DISTINCT FROM NEW.program_id THEN
            RAISE EXCEPTION 'structure observation experiment must belong to the same Program';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER structure_observation_program_scope_guard
    BEFORE INSERT OR UPDATE OF program_id,experiment_id,source_dataset_version_id
    ON bio.structure_observation
    FOR EACH ROW EXECUTE FUNCTION bio.assert_structure_observation_program_scope();

CREATE FUNCTION design.assert_analysis_snapshot_program_scope() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE owner uuid; foreign_datasets integer;
BEGIN
    IF NEW.work_item_id IS NOT NULL THEN
        SELECT program_id INTO owner FROM design.program_work_item WHERE id=NEW.work_item_id;
        IF owner IS DISTINCT FROM NEW.program_id THEN
            RAISE EXCEPTION 'analysis snapshot work item must belong to the same Program';
        END IF;
    END IF;
    SELECT count(*) INTO foreign_datasets
      FROM unnest(NEW.dataset_version_ids) AS item(dataset_id)
      LEFT JOIN app.dataset_version dataset ON dataset.id=item.dataset_id
     WHERE dataset.id IS NULL OR dataset.program_id IS DISTINCT FROM NEW.program_id;
    IF foreign_datasets > 0 THEN
        RAISE EXCEPTION 'analysis snapshot datasets must belong to the same Program';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER analysis_snapshot_program_scope_guard
    BEFORE INSERT OR UPDATE OF program_id,work_item_id,dataset_version_ids
    ON design.analysis_snapshot
    FOR EACH ROW EXECUTE FUNCTION design.assert_analysis_snapshot_program_scope();

INSERT INTO meta.migration (filename,sha256,content_sha256,hash_source)
VALUES ('031_reference_job_hardening.sql','\x1a6ede37512aa80629f6300b5abcf00068fcaae283a05aa3936e3051e008e33f'::bytea,
        '\x1a6ede37512aa80629f6300b5abcf00068fcaae283a05aa3936e3051e008e33f'::bytea,'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
