-- 030 · Native foundations for the seven Program reference-job families.
--
-- Dirac absorbs behavior, identity and provenance invariants from DAIKON,
-- openBIS, OpenProject, Chemotion, GSRS, Fragalysis and Open Targets without
-- turning any of those products into a runtime dependency.

ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'disease';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'substance_registration';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'protocol_version';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'dataset_version';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'structure_observation';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'analysis_snapshot';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'external_evidence_release';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'external_evidence_record';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'annotation';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'review';
ALTER TYPE app.relation_kind ADD VALUE IF NOT EXISTS 'associated_with';
ALTER TYPE app.relation_kind ADD VALUE IF NOT EXISTS 'input_to';
ALTER TYPE app.relation_kind ADD VALUE IF NOT EXISTS 'observed_in';

BEGIN;

-- ── Phase A: canonical disease, registration and physical samples ─────────

CREATE TABLE bio.disease (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    disease_key citext UNIQUE NOT NULL,
    name text NOT NULL CHECK (btrim(name) <> ''),
    ontology_namespace text,
    ontology_id text,
    description text,
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by_kind app.actor_kind NOT NULL,
    created_by_id text NOT NULL,
    UNIQUE NULLS NOT DISTINCT (ontology_namespace, ontology_id),
    CHECK ((ontology_namespace IS NULL) = (ontology_id IS NULL))
);

CREATE TABLE design.program_target_disease (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    program_id uuid NOT NULL REFERENCES design.project(id) ON DELETE RESTRICT,
    target_id uuid NOT NULL REFERENCES bio.target(id) ON DELETE RESTRICT,
    disease_id uuid NOT NULL REFERENCES bio.disease(id) ON DELETE RESTRICT,
    role text NOT NULL DEFAULT 'primary'
        CHECK (role IN ('primary','secondary','safety','biomarker','exploratory')),
    rationale text NOT NULL CHECK (btrim(rationale) <> ''),
    linked_at timestamptz NOT NULL DEFAULT now(),
    linked_by_kind app.actor_kind NOT NULL,
    linked_by_id text NOT NULL,
    retired_at timestamptz,
    UNIQUE NULLS NOT DISTINCT (program_id,target_id,disease_id,role,retired_at),
    CHECK (retired_at IS NULL OR retired_at >= linked_at)
);

CREATE TABLE chem.substance_registration (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    compound_id uuid NOT NULL REFERENCES chem.compound(id) ON DELETE RESTRICT,
    revision integer NOT NULL CHECK (revision > 0),
    status text NOT NULL CHECK (status IN (
        'draft','candidate_match','conflict','validated','approved','rejected','superseded'
    )),
    definition jsonb NOT NULL CHECK (jsonb_typeof(definition) = 'object'),
    validation jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(validation) = 'object'),
    decision text,
    supersedes_id uuid REFERENCES chem.substance_registration(id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by_kind app.actor_kind NOT NULL,
    created_by_id text NOT NULL,
    assessed_at timestamptz,
    assessed_by_kind app.actor_kind,
    assessed_by_id text,
    UNIQUE (compound_id,revision),
    CHECK (supersedes_id IS NULL OR supersedes_id <> id),
    CHECK ((assessed_at IS NULL) = (assessed_by_kind IS NULL)),
    CHECK ((assessed_at IS NULL) = (assessed_by_id IS NULL)),
    CHECK (status NOT IN ('approved','rejected') OR assessed_at IS NOT NULL),
    CHECK (status <> 'approved' OR btrim(coalesce(decision,'')) <> '')
);
CREATE UNIQUE INDEX substance_registration_one_current
    ON chem.substance_registration(compound_id)
    WHERE status <> 'superseded';

CREATE TABLE chem.sample (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id uuid NOT NULL REFERENCES chem.batch(id) ON DELETE RESTRICT,
    parent_sample_id uuid REFERENCES chem.sample(id) ON DELETE RESTRICT,
    sample_code citext UNIQUE NOT NULL,
    amount_value numeric(16,6) NOT NULL CHECK (amount_value >= 0),
    amount_unit meta.unit NOT NULL,
    container text,
    location text,
    status text NOT NULL DEFAULT 'available'
        CHECK (status IN ('available','reserved','consumed','depleted','quarantined','destroyed')),
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by_kind app.actor_kind NOT NULL,
    created_by_id text NOT NULL,
    CHECK (amount_unit::text IN ('g','mg','ug','mol','mmol','umol')),
    CHECK (parent_sample_id IS NULL OR parent_sample_id <> id)
);
CREATE INDEX sample_batch_idx ON chem.sample(batch_id,status);

CREATE FUNCTION chem.assert_sample_parent_batch() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent_batch uuid;
BEGIN
    IF NEW.parent_sample_id IS NULL THEN RETURN NEW; END IF;
    SELECT batch_id INTO parent_batch FROM chem.sample WHERE id=NEW.parent_sample_id;
    IF parent_batch IS DISTINCT FROM NEW.batch_id THEN
        RAISE EXCEPTION 'child sample and parent sample must belong to the same batch';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER sample_parent_batch_guard
    BEFORE INSERT OR UPDATE ON chem.sample
    FOR EACH ROW EXECUTE FUNCTION chem.assert_sample_parent_batch();

CREATE TABLE chem.sample_custody_event (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    sample_id uuid NOT NULL REFERENCES chem.sample(id) ON DELETE RESTRICT,
    event_kind text NOT NULL CHECK (event_kind IN (
        'created','reserved','released','transferred','consumed','adjusted','quarantined','destroyed'
    )),
    from_location text,
    to_location text,
    amount_delta numeric(16,6),
    reason text NOT NULL CHECK (btrim(reason) <> ''),
    occurred_at timestamptz NOT NULL DEFAULT now(),
    actor_kind app.actor_kind NOT NULL,
    actor_id text NOT NULL
);
CREATE INDEX sample_custody_timeline ON chem.sample_custody_event(sample_id,occurred_at DESC);

-- ── Phase B: work collaboration and criterion-level gates ────────────────

CREATE TABLE design.program_work_comment (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    work_item_id uuid NOT NULL REFERENCES design.program_work_item(id) ON DELETE RESTRICT,
    body text NOT NULL CHECK (btrim(body) <> ''),
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by_kind app.actor_kind NOT NULL,
    created_by_id text NOT NULL,
    edited_at timestamptz,
    CHECK (edited_at IS NULL OR edited_at >= created_at)
);

CREATE TABLE design.program_work_attachment (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    work_item_id uuid NOT NULL REFERENCES design.program_work_item(id) ON DELETE RESTRICT,
    artifact_id uuid NOT NULL REFERENCES app.artifact(id) ON DELETE RESTRICT,
    role text NOT NULL CHECK (btrim(role) <> ''),
    attached_at timestamptz NOT NULL DEFAULT now(),
    attached_by_kind app.actor_kind NOT NULL,
    attached_by_id text NOT NULL,
    UNIQUE (work_item_id,artifact_id,role)
);

CREATE TABLE design.gate_criterion_assessment (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    stage_gate_id uuid NOT NULL REFERENCES design.program_stage_gate(id) ON DELETE RESTRICT,
    criterion_key text NOT NULL CHECK (btrim(criterion_key) <> ''),
    status text NOT NULL CHECK (status IN ('met','not_met','waived','unknown')),
    evidence_kind app.object_kind,
    evidence_id text,
    explanation text NOT NULL CHECK (btrim(explanation) <> ''),
    assessed_at timestamptz NOT NULL DEFAULT now(),
    assessed_by_kind app.actor_kind NOT NULL,
    assessed_by_id text NOT NULL,
    UNIQUE (stage_gate_id,criterion_key),
    FOREIGN KEY (evidence_kind,evidence_id) REFERENCES app.entity(kind,id) ON DELETE RESTRICT,
    CHECK ((evidence_kind IS NULL) = (evidence_id IS NULL)),
    CHECK (status <> 'met' OR evidence_id IS NOT NULL),
    CHECK (status <> 'waived' OR length(explanation) >= 8)
);

-- ── Phase C: immutable protocols, experiments and datasets ───────────────

CREATE TABLE bio.protocol_version (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    protocol_key citext NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    title text NOT NULL CHECK (btrim(title) <> ''),
    assay_id uuid REFERENCES bio.assay(id) ON DELETE RESTRICT,
    specification jsonb NOT NULL CHECK (jsonb_typeof(specification) = 'object'),
    digest bytea NOT NULL CHECK (octet_length(digest) = 32),
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('draft','active','retired','superseded')),
    supersedes_id uuid REFERENCES bio.protocol_version(id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by_kind app.actor_kind NOT NULL,
    created_by_id text NOT NULL,
    UNIQUE (protocol_key,revision),
    UNIQUE (protocol_key,digest),
    CHECK (supersedes_id IS NULL OR supersedes_id <> id)
);
CREATE UNIQUE INDEX protocol_one_active ON bio.protocol_version(protocol_key)
    WHERE status='active';

CREATE TABLE bio.experiment (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_key citext UNIQUE NOT NULL,
    program_id uuid NOT NULL REFERENCES design.project(id) ON DELETE RESTRICT,
    work_item_id uuid NOT NULL REFERENCES design.program_work_item(id) ON DELETE RESTRICT,
    protocol_version_id uuid NOT NULL REFERENCES bio.protocol_version(id) ON DELETE RESTRICT,
    title text NOT NULL CHECK (btrim(title) <> ''),
    status text NOT NULL DEFAULT 'planned'
        CHECK (status IN ('planned','running','completed','failed','cancelled')),
    started_at timestamptz,
    completed_at timestamptz,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    recorded_by_kind app.actor_kind NOT NULL,
    recorded_by_id text NOT NULL,
    CHECK (completed_at IS NULL OR started_at IS NOT NULL),
    CHECK (completed_at IS NULL OR completed_at >= started_at),
    CHECK (status NOT IN ('completed','failed','cancelled') OR completed_at IS NOT NULL)
);

CREATE TABLE bio.experiment_sample (
    experiment_id uuid NOT NULL REFERENCES bio.experiment(id) ON DELETE RESTRICT,
    sample_id uuid NOT NULL REFERENCES chem.sample(id) ON DELETE RESTRICT,
    role text NOT NULL CHECK (role IN ('test','control','reference','matrix','reagent')),
    amount_value numeric(16,6) CHECK (amount_value > 0),
    amount_unit meta.unit,
    PRIMARY KEY (experiment_id,sample_id,role),
    CHECK ((amount_value IS NULL) = (amount_unit IS NULL))
);

CREATE TABLE app.dataset_version (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_key citext NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    program_id uuid REFERENCES design.project(id) ON DELETE RESTRICT,
    experiment_id uuid REFERENCES bio.experiment(id) ON DELETE RESTRICT,
    manifest_artifact_id uuid NOT NULL REFERENCES app.artifact(id) ON DELETE RESTRICT,
    manifest jsonb NOT NULL CHECK (jsonb_typeof(manifest) = 'object'),
    schema_version text NOT NULL,
    access_scope text NOT NULL DEFAULT 'internal'
        CHECK (access_scope IN ('public','program','internal','partner_confidential','restricted','regulated')),
    status text NOT NULL DEFAULT 'committed' CHECK (status IN ('committed','superseded','retracted')),
    digest bytea NOT NULL CHECK (octet_length(digest) = 32),
    supersedes_id uuid REFERENCES app.dataset_version(id) ON DELETE RESTRICT,
    committed_at timestamptz NOT NULL DEFAULT now(),
    committed_by_kind app.actor_kind NOT NULL,
    committed_by_id text NOT NULL,
    UNIQUE (dataset_key,version),
    UNIQUE (dataset_key,digest),
    CHECK (supersedes_id IS NULL OR supersedes_id <> id)
);
CREATE UNIQUE INDEX dataset_version_one_current ON app.dataset_version(dataset_key)
    WHERE status='committed';

CREATE TABLE app.dataset_version_parent (
    dataset_version_id uuid NOT NULL REFERENCES app.dataset_version(id) ON DELETE RESTRICT,
    parent_dataset_version_id uuid NOT NULL REFERENCES app.dataset_version(id) ON DELETE RESTRICT,
    producer_job_id uuid REFERENCES app.job(id) ON DELETE RESTRICT,
    derivation text NOT NULL CHECK (btrim(derivation) <> ''),
    PRIMARY KEY (dataset_version_id,parent_dataset_version_id),
    CHECK (dataset_version_id <> parent_dataset_version_id)
);

ALTER TABLE bio.measurement_v2
    ADD COLUMN sample_id uuid REFERENCES chem.sample(id) ON DELETE RESTRICT,
    ADD COLUMN experiment_id uuid REFERENCES bio.experiment(id) ON DELETE RESTRICT,
    ADD COLUMN protocol_version_id uuid REFERENCES bio.protocol_version(id) ON DELETE RESTRICT;

-- ── Phase D: experimental observations and collaborative snapshots ───────

CREATE TABLE bio.structure_observation (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    observation_key citext UNIQUE NOT NULL,
    program_id uuid NOT NULL REFERENCES design.project(id) ON DELETE RESTRICT,
    structure_id uuid NOT NULL REFERENCES bio.structure(id) ON DELETE RESTRICT,
    compound_id uuid REFERENCES chem.compound(id) ON DELETE RESTRICT,
    experiment_id uuid REFERENCES bio.experiment(id) ON DELETE RESTRICT,
    source_dataset_version_id uuid NOT NULL REFERENCES app.dataset_version(id) ON DELETE RESTRICT,
    canonical_site text,
    quality_status text NOT NULL DEFAULT 'unreviewed'
        CHECK (quality_status IN ('unreviewed','accepted','questionable','rejected')),
    observed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by_kind app.actor_kind NOT NULL,
    created_by_id text NOT NULL
);

CREATE TABLE design.scientific_annotation (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    program_id uuid NOT NULL REFERENCES design.project(id) ON DELETE RESTRICT,
    subject_kind app.object_kind NOT NULL,
    subject_id text NOT NULL,
    annotation_kind text NOT NULL CHECK (annotation_kind IN ('tag','site','merge_hypothesis','note','quality')),
    label text NOT NULL CHECK (btrim(label) <> ''),
    value jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(value) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by_kind app.actor_kind NOT NULL,
    created_by_id text NOT NULL,
    retired_at timestamptz,
    FOREIGN KEY (subject_kind,subject_id) REFERENCES app.entity(kind,id) ON DELETE RESTRICT
);

CREATE TABLE design.scientific_review (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    program_id uuid NOT NULL REFERENCES design.project(id) ON DELETE RESTRICT,
    subject_kind app.object_kind NOT NULL,
    subject_id text NOT NULL,
    review_role text NOT NULL CHECK (review_role IN ('main','peer')),
    status text NOT NULL CHECK (status IN ('accepted','questionable','rejected')),
    comment text NOT NULL CHECK (btrim(comment) <> ''),
    reviewed_at timestamptz NOT NULL DEFAULT now(),
    reviewed_by_kind app.actor_kind NOT NULL,
    reviewed_by_id text NOT NULL,
    FOREIGN KEY (subject_kind,subject_id) REFERENCES app.entity(kind,id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX scientific_review_one_main
    ON design.scientific_review(program_id,subject_kind,subject_id)
    WHERE review_role='main';

CREATE TABLE design.analysis_snapshot (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    program_id uuid NOT NULL REFERENCES design.project(id) ON DELETE RESTRICT,
    work_item_id uuid REFERENCES design.program_work_item(id) ON DELETE RESTRICT,
    title text NOT NULL CHECK (btrim(title) <> ''),
    snapshot_mode text NOT NULL CHECK (snapshot_mode IN ('live','preserved')),
    release_channel text,
    dataset_version_ids uuid[] NOT NULL DEFAULT '{}',
    state jsonb NOT NULL CHECK (jsonb_typeof(state) = 'object'),
    digest bytea NOT NULL CHECK (octet_length(digest) = 32),
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by_kind app.actor_kind NOT NULL,
    created_by_id text NOT NULL,
    UNIQUE (program_id,digest),
    CHECK ((snapshot_mode='live') = (release_channel IS NOT NULL)),
    CHECK (snapshot_mode <> 'preserved' OR cardinality(dataset_version_ids) > 0)
);

-- ── Phase E: release-pinned external evidence graph ──────────────────────

CREATE TABLE bio.external_evidence_release (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_name text NOT NULL CHECK (btrim(source_name) <> ''),
    release_name text NOT NULL CHECK (btrim(release_name) <> ''),
    source_url text,
    retrieved_at timestamptz NOT NULL,
    payload_artifact_id uuid NOT NULL REFERENCES app.artifact(id) ON DELETE RESTRICT,
    digest bytea NOT NULL CHECK (octet_length(digest) = 32),
    imported_at timestamptz NOT NULL DEFAULT now(),
    imported_by_kind app.actor_kind NOT NULL,
    imported_by_id text NOT NULL,
    UNIQUE (source_name,release_name),
    UNIQUE (source_name,digest)
);

CREATE TABLE bio.external_evidence_record (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    release_id uuid NOT NULL REFERENCES bio.external_evidence_release(id) ON DELETE RESTRICT,
    source_record_id text NOT NULL CHECK (btrim(source_record_id) <> ''),
    target_id uuid NOT NULL REFERENCES bio.target(id) ON DELETE RESTRICT,
    disease_id uuid NOT NULL REFERENCES bio.disease(id) ON DELETE RESTRICT,
    data_type text NOT NULL CHECK (btrim(data_type) <> ''),
    evidence_source text NOT NULL CHECK (btrim(evidence_source) <> ''),
    score numeric CHECK (score BETWEEN 0 AND 1),
    is_direct boolean NOT NULL DEFAULT true,
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    digest bytea NOT NULL CHECK (octet_length(digest) = 32),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (release_id,source_record_id),
    UNIQUE (release_id,digest)
);
CREATE INDEX external_evidence_pair
    ON bio.external_evidence_record(target_id,disease_id,release_id,data_type);

CREATE VIEW bio.v_target_disease_association AS
SELECT release_id,target_id,disease_id,
       count(*) AS evidence_count,
       count(DISTINCT evidence_source) AS source_count,
       count(*) FILTER (WHERE is_direct) AS direct_evidence_count,
       min(score) FILTER (WHERE score IS NOT NULL) AS score_min,
       avg(score) FILTER (WHERE score IS NOT NULL) AS score_mean,
       max(score) FILTER (WHERE score IS NOT NULL) AS score_max,
       array_agg(DISTINCT data_type ORDER BY data_type) AS data_types
  FROM bio.external_evidence_record
 GROUP BY release_id,target_id,disease_id;

COMMENT ON VIEW bio.v_target_disease_association IS
    'Explainable release-scoped summary. It deliberately exposes score distribution '
    'instead of inventing an opaque cross-source association score.';

-- ── Canonical identity registration ──────────────────────────────────────

SELECT app.register_entity('disease',id::text,disease_key::text,name,'bio','disease') FROM bio.disease;
SELECT app.register_entity('sample',id::text,sample_code::text,sample_code::text,'chem','sample') FROM chem.sample;

CREATE TRIGGER entity_disease AFTER INSERT OR UPDATE ON bio.disease
    FOR EACH ROW EXECUTE FUNCTION app.sync_entity_trigger('disease','id','disease_key','name');
CREATE TRIGGER entity_sample AFTER INSERT OR UPDATE ON chem.sample
    FOR EACH ROW EXECUTE FUNCTION app.sync_entity_trigger('sample','id','sample_code','sample_code');
CREATE TRIGGER entity_substance_registration AFTER INSERT OR UPDATE ON chem.substance_registration
    FOR EACH ROW EXECUTE FUNCTION app.sync_entity_trigger('substance_registration','id','id','status');
CREATE TRIGGER entity_protocol_version AFTER INSERT OR UPDATE ON bio.protocol_version
    FOR EACH ROW EXECUTE FUNCTION app.sync_entity_trigger('protocol_version','id','id','title');
CREATE TRIGGER entity_experiment AFTER INSERT OR UPDATE ON bio.experiment
    FOR EACH ROW EXECUTE FUNCTION app.sync_entity_trigger('experiment','id','experiment_key','title');
CREATE TRIGGER entity_dataset_version AFTER INSERT OR UPDATE ON app.dataset_version
    FOR EACH ROW EXECUTE FUNCTION app.sync_entity_trigger('dataset_version','id','id','dataset_key');
CREATE TRIGGER entity_structure_observation AFTER INSERT OR UPDATE ON bio.structure_observation
    FOR EACH ROW EXECUTE FUNCTION app.sync_entity_trigger('structure_observation','id','observation_key','observation_key');
CREATE TRIGGER entity_annotation AFTER INSERT OR UPDATE ON design.scientific_annotation
    FOR EACH ROW EXECUTE FUNCTION app.sync_entity_trigger('annotation','id','id','label');
CREATE TRIGGER entity_review AFTER INSERT OR UPDATE ON design.scientific_review
    FOR EACH ROW EXECUTE FUNCTION app.sync_entity_trigger('review','id','id','status');
CREATE TRIGGER entity_analysis_snapshot AFTER INSERT OR UPDATE ON design.analysis_snapshot
    FOR EACH ROW EXECUTE FUNCTION app.sync_entity_trigger('analysis_snapshot','id','id','title');
CREATE TRIGGER entity_external_evidence_release AFTER INSERT OR UPDATE ON bio.external_evidence_release
    FOR EACH ROW EXECUTE FUNCTION app.sync_entity_trigger('external_evidence_release','id','id','release_name');
CREATE TRIGGER entity_external_evidence AFTER INSERT OR UPDATE ON bio.external_evidence_record
    FOR EACH ROW EXECUTE FUNCTION app.sync_entity_trigger('external_evidence_record','id','id','source_record_id');

ALTER TABLE design.program_event DROP CONSTRAINT program_event_event_kind_check;
ALTER TABLE design.program_event ADD CONSTRAINT program_event_event_kind_check CHECK (event_kind IN (
    'program.created','program.updated','objective.recorded','hypothesis.recorded',
    'decision.recorded','milestone.recorded','object.linked','snapshot.created',
    'portfolio.assigned','member.assigned','stage_gate.recorded','work_package.recorded',
    'work_item.transitioned','work_execution.linked','evidence.attached','lineage.recorded',
    'target_disease.linked','substance_registration.recorded','sample.created','sample.transferred',
    'work_comment.recorded','work_attachment.recorded','gate_criterion.assessed',
    'protocol.recorded','dataset_version.committed','experiment.recorded',
    'structure_observation.recorded','annotation.recorded','review.recorded',
    'analysis_snapshot.created','external_evidence_release.imported','external_evidence.recorded'
));

INSERT INTO meta.migration (filename,sha256,content_sha256,hash_source)
VALUES ('030_reference_job_foundation.sql','\xa5dba34982e4387a0aa875f16ed5a6a6a1a60f618edaa0e613bfb51fcbf943ef'::bytea,
        '\xa5dba34982e4387a0aa875f16ed5a6a6a1a60f618edaa0e613bfb51fcbf943ef'::bytea,'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
