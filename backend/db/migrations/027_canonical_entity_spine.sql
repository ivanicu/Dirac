-- 027 · One canonical entity identity across every Dirac workspace.
--
-- Program, Design, Campaign, Synthesis, Experiment and Compute exchange ObjectRef;
-- none of them owns a private copy of a compound or any other durable object.
BEGIN;

CREATE TABLE app.entity (
    kind app.object_kind NOT NULL,
    id text NOT NULL CHECK (btrim(id) <> ''),
    canonical_key text,
    label text,
    origin_schema text,
    origin_table text,
    registered_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (kind, id)
);
CREATE UNIQUE INDEX entity_canonical_key_unique
    ON app.entity(kind,canonical_key) WHERE canonical_key IS NOT NULL;

CREATE TABLE app.entity_alias (
    namespace text NOT NULL CHECK (btrim(namespace) <> ''),
    alias citext NOT NULL,
    entity_kind app.object_kind NOT NULL,
    entity_id text NOT NULL,
    source_id uuid REFERENCES meta.source(id),
    registered_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (namespace, alias),
    FOREIGN KEY (entity_kind, entity_id) REFERENCES app.entity(kind, id) ON DELETE RESTRICT
);

CREATE FUNCTION app.register_entity(
    p_kind app.object_kind, p_id text, p_canonical_key text, p_label text,
    p_origin_schema text, p_origin_table text
) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO app.entity(kind,id,canonical_key,label,origin_schema,origin_table)
    VALUES (p_kind,p_id,NULLIF(p_canonical_key,''),NULLIF(p_label,''),p_origin_schema,p_origin_table)
    ON CONFLICT (kind,id) DO UPDATE SET
        canonical_key = COALESCE(app.entity.canonical_key, EXCLUDED.canonical_key),
        label = COALESCE(EXCLUDED.label, app.entity.label),
        origin_schema = COALESCE(app.entity.origin_schema, EXCLUDED.origin_schema),
        origin_table = COALESCE(app.entity.origin_table, EXCLUDED.origin_table);
END $$;

CREATE FUNCTION app.sync_entity_trigger() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    body jsonb := to_jsonb(NEW);
    entity_id text := body->>TG_ARGV[1];
    canonical text := CASE WHEN TG_NARGS > 2 THEN body->>TG_ARGV[2] ELSE NULL END;
    display_label text := CASE WHEN TG_NARGS > 3 THEN body->>TG_ARGV[3] ELSE NULL END;
BEGIN
    PERFORM app.register_entity(TG_ARGV[0]::app.object_kind, entity_id, canonical,
                                display_label, TG_TABLE_SCHEMA, TG_TABLE_NAME);
    RETURN NEW;
END $$;

CREATE FUNCTION app.sync_compound_alias_trigger() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO app.entity_alias(namespace,alias,entity_kind,entity_id,source_id)
    VALUES (NEW.kind,NEW.alias,'compound',NEW.compound_id::text,NEW.source_id);
    RETURN NEW;
END $$;

-- Seed the durable identity spine from existing relational truth.
SELECT app.register_entity('program', id::text, code::text, name, 'design', 'project') FROM design.project;
SELECT app.register_entity('portfolio', id::text, code::text, name, 'design', 'portfolio') FROM design.portfolio;
SELECT app.register_entity('target', id::text, COALESCE(uniprot::text,name::text), name::text, 'bio', 'target') FROM bio.target;
SELECT app.register_entity('compound', id::text, inchikey::text, registry_id, 'chem', 'compound') FROM chem.compound;
SELECT app.register_entity('compound_form', id::text, full_inchikey::text, label, 'chem', 'form') FROM chem.form;
SELECT app.register_entity('batch', id::text, batch_code::text, batch_code::text, 'chem', 'batch') FROM chem.batch;
SELECT app.register_entity('series', id::text, project_id::text||':'||name::text, name::text, 'design', 'series') FROM design.series;
SELECT app.register_entity('campaign', id::text, program_id::text||':'||name::text, name::text, 'design', 'campaign') FROM design.campaign;
SELECT app.register_entity('objective', id::text, program_id::text||':'||objective_key::text||':'||revision, title, 'design', 'program_objective') FROM design.program_objective;
SELECT app.register_entity('hypothesis', id::text, id::text, title, 'design', 'hypothesis') FROM design.hypothesis;
SELECT app.register_entity('decision', id::text, id::text, action, 'design', 'decision') FROM design.decision;
SELECT app.register_entity('milestone', id::text, program_id::text||':'||milestone_key::text||':'||revision, title, 'design', 'program_milestone') FROM design.program_milestone;
SELECT app.register_entity('stage_gate', id::text, program_id::text||':'||gate_key::text||':'||revision, title, 'design', 'program_stage_gate') FROM design.program_stage_gate;
SELECT app.register_entity('work_package', id::text, program_id::text||':'||work_key::text||':'||revision, title, 'design', 'program_work_package') FROM design.program_work_package;
SELECT app.register_entity('evidence', id::text, id::text, claim, 'design', 'evidence') FROM design.evidence;
SELECT app.register_entity('protein_structure', id::text, COALESCE(pdb_id::text,id::text), pdb_id::text, 'bio', 'structure') FROM bio.structure;
SELECT app.register_entity('assay', id::text, code::text, name, 'bio', 'assay') FROM bio.assay;
SELECT app.register_entity('dataset', id::text, encode(digest,'hex'), schema_version, 'app', 'dataset_snapshot') FROM app.dataset_snapshot;
SELECT app.register_entity('model', id::text, model_object_id||':'||release_name, release_name, 'meta', 'model_release') FROM meta.model_release;
SELECT app.register_entity('artifact', id::text, encode(blob_sha256,'hex')||':'||role, role, 'app', 'artifact') FROM app.artifact;
SELECT app.register_entity('mission', id::text, id::text, objective, 'app', 'mission') FROM app.mission;
SELECT app.register_entity('run', id::text, mission_id::text||':'||attempt, NULL, 'app', 'run') FROM app.run;
SELECT app.register_entity('job', id::text, id::text, state::text, 'app', 'job') FROM app.job;
SELECT app.register_entity('measurement', id::text, measurement_key, measurement_key, 'bio', 'measurement_v2') FROM bio.measurement_v2;

-- Preserve old polymorphic edges as known identities before installing FKs.
INSERT INTO app.entity(kind,id) SELECT DISTINCT object_kind,object_id FROM design.program_object_link ON CONFLICT DO NOTHING;
INSERT INTO app.entity(kind,id) SELECT DISTINCT source_kind,source_id FROM app.object_relation ON CONFLICT DO NOTHING;
INSERT INTO app.entity(kind,id) SELECT DISTINCT target_kind,target_id FROM app.object_relation ON CONFLICT DO NOTHING;

INSERT INTO app.entity_alias(namespace,alias,entity_kind,entity_id,source_id)
SELECT kind,alias,'compound',compound_id::text,source_id FROM chem.compound_alias
ON CONFLICT (namespace,alias) DO NOTHING;

ALTER TABLE design.program_object_link
    ADD CONSTRAINT program_link_canonical_entity_fk
    FOREIGN KEY (object_kind,object_id) REFERENCES app.entity(kind,id) ON DELETE RESTRICT;
ALTER TABLE app.object_relation
    ADD CONSTRAINT object_relation_source_entity_fk
    FOREIGN KEY (source_kind,source_id) REFERENCES app.entity(kind,id) ON DELETE RESTRICT,
    ADD CONSTRAINT object_relation_target_entity_fk
    FOREIGN KEY (target_kind,target_id) REFERENCES app.entity(kind,id) ON DELETE RESTRICT;
ALTER TABLE design.program_evidence_binding
    ADD CONSTRAINT evidence_subject_entity_fk
    FOREIGN KEY (subject_kind,subject_id) REFERENCES app.entity(kind,id) ON DELETE RESTRICT,
    ADD CONSTRAINT evidence_object_entity_fk
    FOREIGN KEY (evidence_kind,evidence_id) REFERENCES app.entity(kind,id) ON DELETE RESTRICT;

CREATE TRIGGER entity_program AFTER INSERT OR UPDATE ON design.project
    FOR EACH ROW EXECUTE FUNCTION app.sync_entity_trigger('program','id','code','name');
CREATE TRIGGER entity_portfolio AFTER INSERT OR UPDATE ON design.portfolio
    FOR EACH ROW EXECUTE FUNCTION app.sync_entity_trigger('portfolio','id','code','name');
CREATE TRIGGER entity_target AFTER INSERT OR UPDATE ON bio.target
    FOR EACH ROW EXECUTE FUNCTION app.sync_entity_trigger('target','id','uniprot','name');
CREATE TRIGGER entity_compound AFTER INSERT OR UPDATE ON chem.compound
    FOR EACH ROW EXECUTE FUNCTION app.sync_entity_trigger('compound','id','inchikey','registry_id');
CREATE TRIGGER entity_compound_form AFTER INSERT OR UPDATE ON chem.form
    FOR EACH ROW EXECUTE FUNCTION app.sync_entity_trigger('compound_form','id','full_inchikey','label');
CREATE TRIGGER entity_batch AFTER INSERT OR UPDATE ON chem.batch
    FOR EACH ROW EXECUTE FUNCTION app.sync_entity_trigger('batch','id','batch_code','batch_code');
CREATE TRIGGER entity_compound_alias AFTER INSERT ON chem.compound_alias
    FOR EACH ROW EXECUTE FUNCTION app.sync_compound_alias_trigger();
CREATE TRIGGER entity_series AFTER INSERT OR UPDATE ON design.series
    FOR EACH ROW EXECUTE FUNCTION app.sync_entity_trigger('series','id','id','name');
CREATE TRIGGER entity_campaign AFTER INSERT OR UPDATE ON design.campaign
    FOR EACH ROW EXECUTE FUNCTION app.sync_entity_trigger('campaign','id','id','name');
CREATE TRIGGER entity_objective AFTER INSERT OR UPDATE ON design.program_objective
    FOR EACH ROW EXECUTE FUNCTION app.sync_entity_trigger('objective','id','id','title');
CREATE TRIGGER entity_hypothesis AFTER INSERT OR UPDATE ON design.hypothesis
    FOR EACH ROW EXECUTE FUNCTION app.sync_entity_trigger('hypothesis','id','id','title');
CREATE TRIGGER entity_decision AFTER INSERT OR UPDATE ON design.decision
    FOR EACH ROW EXECUTE FUNCTION app.sync_entity_trigger('decision','id','id','action');
CREATE TRIGGER entity_milestone AFTER INSERT OR UPDATE ON design.program_milestone
    FOR EACH ROW EXECUTE FUNCTION app.sync_entity_trigger('milestone','id','id','title');
CREATE TRIGGER entity_stage_gate AFTER INSERT OR UPDATE ON design.program_stage_gate
    FOR EACH ROW EXECUTE FUNCTION app.sync_entity_trigger('stage_gate','id','id','title');
CREATE TRIGGER entity_work_package AFTER INSERT OR UPDATE ON design.program_work_package
    FOR EACH ROW EXECUTE FUNCTION app.sync_entity_trigger('work_package','id','id','title');
CREATE TRIGGER entity_evidence AFTER INSERT OR UPDATE ON design.evidence
    FOR EACH ROW EXECUTE FUNCTION app.sync_entity_trigger('evidence','id','id','claim');
CREATE TRIGGER entity_structure AFTER INSERT OR UPDATE ON bio.structure
    FOR EACH ROW EXECUTE FUNCTION app.sync_entity_trigger('protein_structure','id','pdb_id','pdb_id');
CREATE TRIGGER entity_assay AFTER INSERT OR UPDATE ON bio.assay
    FOR EACH ROW EXECUTE FUNCTION app.sync_entity_trigger('assay','id','code','name');
CREATE TRIGGER entity_artifact AFTER INSERT OR UPDATE ON app.artifact
    FOR EACH ROW EXECUTE FUNCTION app.sync_entity_trigger('artifact','id','id','role');
CREATE TRIGGER entity_mission AFTER INSERT OR UPDATE ON app.mission
    FOR EACH ROW EXECUTE FUNCTION app.sync_entity_trigger('mission','id','id','objective');
CREATE TRIGGER entity_run AFTER INSERT OR UPDATE ON app.run
    FOR EACH ROW EXECUTE FUNCTION app.sync_entity_trigger('run','id','id','state');
CREATE TRIGGER entity_job AFTER INSERT OR UPDATE ON app.job
    FOR EACH ROW EXECUTE FUNCTION app.sync_entity_trigger('job','id','id','state');
CREATE TRIGGER entity_dataset AFTER INSERT OR UPDATE ON app.dataset_snapshot
    FOR EACH ROW EXECUTE FUNCTION app.sync_entity_trigger('dataset','id','id','schema_version');
CREATE TRIGGER entity_model AFTER INSERT OR UPDATE ON meta.model_release
    FOR EACH ROW EXECUTE FUNCTION app.sync_entity_trigger('model','id','id','release_name');
CREATE TRIGGER entity_measurement AFTER INSERT OR UPDATE ON bio.measurement_v2
    FOR EACH ROW EXECUTE FUNCTION app.sync_entity_trigger('measurement','id','measurement_key','measurement_key');

CREATE VIEW app.v_entity_resolver AS
SELECT kind, id, canonical_key, label, origin_schema, origin_table, registered_at
  FROM app.entity;

COMMENT ON TABLE app.entity IS
    'Canonical identity spine. Every durable ObjectRef resolves here; workspaces attach context, never copy the entity.';
COMMENT ON TABLE app.entity_alias IS
    'External and historical identifiers resolve to one canonical entity without duplicating it.';

INSERT INTO meta.migration (filename, sha256, content_sha256, hash_source)
VALUES ('027_canonical_entity_spine.sql', '\x29823862f661318d88f4a67b4f12f53df9635f7a9634b1a8bf737cbdc20c373a'::bytea,
        '\x29823862f661318d88f4a67b4f12f53df9635f7a9634b1a8bf737cbdc20c373a'::bytea, 'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
