-- 022 · first-class Motif dataset, endpoint, model, policy, objective and cycle releases.
--
-- References remain conservative where the current schema does not expose a stable
-- physical table for a canonical ObjectKind. Object relations remain the cross-domain
-- lineage mechanism.
BEGIN;

CREATE TYPE meta.release_lifecycle AS ENUM ('candidate','validated','production','retired');
CREATE TYPE design.motif_cycle_state AS ENUM (
    'planned','running','waiting_review','completed','failed','cancelled');

CREATE TABLE design.endpoint_definition (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    endpoint_key text NOT NULL,
    version text NOT NULL,
    assay_id uuid NOT NULL REFERENCES bio.assay(id),
    protocol_ref jsonb NOT NULL,
    target_ref jsonb,
    species text,
    biological_system text NOT NULL,
    readout text NOT NULL,
    measurement_type text NOT NULL,
    direction text NOT NULL CHECK (direction IN ('maximize','minimize','target_interval','avoid')),
    canonical_unit text NOT NULL,
    quantity_dimension text NOT NULL,
    label_transform jsonb NOT NULL DEFAULT '{}'::jsonb,
    censoring_policy jsonb NOT NULL DEFAULT '{}'::jsonb,
    replicate_policy jsonb NOT NULL DEFAULT '{}'::jsonb,
    qc_policy jsonb NOT NULL DEFAULT '{}'::jsonb,
    intended_domain jsonb NOT NULL DEFAULT '{}'::jsonb,
    digest bytea NOT NULL CHECK (octet_length(digest) = 32),
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by_kind app.actor_kind NOT NULL,
    created_by_id text NOT NULL,
    UNIQUE (endpoint_key, version),
    UNIQUE (digest),
    CHECK (protocol_ref->>'kind' = 'protocol' AND coalesce(protocol_ref->>'id','') <> ''),
    CHECK (target_ref IS NULL OR (target_ref->>'kind' = 'target' AND coalesce(target_ref->>'id','') <> ''))
);

CREATE TABLE app.dataset_snapshot (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    program_id uuid REFERENCES design.project(id),
    campaign_id uuid REFERENCES design.campaign(id),
    schema_version text NOT NULL,
    selection_query text NOT NULL,
    selection_query_digest bytea NOT NULL CHECK (octet_length(selection_query_digest) = 32),
    identity_policy_release_id uuid,
    manifest_artifact_id uuid NOT NULL REFERENCES app.artifact(id),
    data_artifact_id uuid NOT NULL REFERENCES app.artifact(id),
    split_manifest_artifact_id uuid NOT NULL REFERENCES app.artifact(id),
    leakage_report_artifact_id uuid REFERENCES app.artifact(id),
    row_count bigint NOT NULL CHECK (row_count >= 0),
    status text NOT NULL DEFAULT 'candidate'
        CHECK (status IN ('candidate','valid','invalid','retired')),
    data_classification text NOT NULL DEFAULT 'internal'
        CHECK (data_classification IN ('public','internal','partner_confidential','restricted','regulated')),
    digest bytea NOT NULL CHECK (octet_length(digest) = 32),
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by_kind app.actor_kind NOT NULL,
    created_by_id text NOT NULL,
    UNIQUE (digest)
);
CREATE INDEX dataset_snapshot_program_idx ON app.dataset_snapshot (program_id, created_at DESC);

CREATE TABLE app.dataset_snapshot_endpoint (
    dataset_snapshot_id uuid NOT NULL REFERENCES app.dataset_snapshot(id) ON DELETE CASCADE,
    endpoint_definition_id uuid NOT NULL REFERENCES design.endpoint_definition(id),
    row_count bigint NOT NULL CHECK (row_count >= 0),
    summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (dataset_snapshot_id, endpoint_definition_id)
);

CREATE TABLE meta.model_release (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    model_object_id text NOT NULL,
    release_name text NOT NULL,
    lifecycle meta.release_lifecycle NOT NULL DEFAULT 'candidate',
    method_row_id uuid NOT NULL REFERENCES meta.method(id),
    source_commit text NOT NULL,
    container_image_digest text NOT NULL CHECK (container_image_digest ~ '^sha256:[0-9a-f]{64}$'),
    lockfile_digest bytea NOT NULL CHECK (octet_length(lockfile_digest) = 32),
    featurizer_digest bytea CHECK (featurizer_digest IS NULL OR octet_length(featurizer_digest) = 32),
    checkpoint_artifact_id uuid NOT NULL REFERENCES app.artifact(id),
    calibration_artifact_id uuid REFERENCES app.artifact(id),
    applicability_policy_artifact_id uuid REFERENCES app.artifact(id),
    validation_artifact_id uuid REFERENCES app.artifact(id),
    model_card_artifact_id uuid REFERENCES app.artifact(id),
    execution_digest bytea NOT NULL CHECK (octet_length(execution_digest) = 32),
    intended_use jsonb NOT NULL DEFAULT '{}'::jsonb,
    prohibited_use jsonb NOT NULL DEFAULT '{}'::jsonb,
    known_limitations jsonb NOT NULL DEFAULT '{}'::jsonb,
    promoted_at timestamptz,
    promoted_by_kind app.actor_kind,
    promoted_by_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by_kind app.actor_kind NOT NULL,
    created_by_id text NOT NULL,
    UNIQUE (model_object_id, release_name),
    UNIQUE (execution_digest),
    CHECK ((promoted_at IS NULL) = (promoted_by_kind IS NULL)),
    CHECK ((promoted_at IS NULL) = (promoted_by_id IS NULL)),
    CHECK (lifecycle NOT IN ('validated','production') OR validation_artifact_id IS NOT NULL),
    CHECK (lifecycle <> 'production' OR model_card_artifact_id IS NOT NULL)
);
CREATE INDEX model_release_lifecycle_idx ON meta.model_release (model_object_id, lifecycle, created_at DESC);

CREATE TABLE meta.calibration_release (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    model_release_id uuid NOT NULL REFERENCES meta.model_release(id),
    endpoint_definition_id uuid NOT NULL REFERENCES design.endpoint_definition(id),
    release_name text NOT NULL,
    lifecycle meta.release_lifecycle NOT NULL DEFAULT 'candidate',
    calibration_artifact_id uuid NOT NULL REFERENCES app.artifact(id),
    validation_artifact_id uuid NOT NULL REFERENCES app.artifact(id),
    digest bytea NOT NULL CHECK (octet_length(digest) = 32),
    intended_domain jsonb NOT NULL DEFAULT '{}'::jsonb,
    coverage_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by_kind app.actor_kind NOT NULL,
    created_by_id text NOT NULL,
    promoted_at timestamptz,
    promoted_by_kind app.actor_kind,
    promoted_by_id text,
    UNIQUE (model_release_id, endpoint_definition_id, release_name),
    UNIQUE (digest),
    CHECK ((promoted_at IS NULL) = (promoted_by_kind IS NULL)),
    CHECK ((promoted_at IS NULL) = (promoted_by_id IS NULL))
);

ALTER TABLE meta.model_release
    ADD COLUMN default_calibration_release_id uuid
        REFERENCES meta.calibration_release(id);

CREATE TABLE meta.model_release_dataset (
    model_release_id uuid NOT NULL REFERENCES meta.model_release(id) ON DELETE CASCADE,
    dataset_snapshot_id uuid NOT NULL REFERENCES app.dataset_snapshot(id),
    role text NOT NULL CHECK (role IN ('train','validation','test','calibration','external')),
    PRIMARY KEY (model_release_id, dataset_snapshot_id, role)
);

CREATE TABLE meta.policy_release (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    policy_kind text NOT NULL CHECK (policy_kind IN (
        'generation','identity_gate','synthesis_gate','fidelity','acquisition',
        'diversity','missing_evidence','retry','explanation')),
    name text NOT NULL,
    version text NOT NULL,
    lifecycle meta.release_lifecycle NOT NULL DEFAULT 'candidate',
    spec_artifact_id uuid NOT NULL REFERENCES app.artifact(id),
    validation_artifact_id uuid REFERENCES app.artifact(id),
    digest bytea NOT NULL CHECK (octet_length(digest) = 32),
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by_kind app.actor_kind NOT NULL,
    created_by_id text NOT NULL,
    promoted_at timestamptz,
    promoted_by_kind app.actor_kind,
    promoted_by_id text,
    UNIQUE (policy_kind, name, version),
    UNIQUE (digest),
    CHECK ((promoted_at IS NULL) = (promoted_by_kind IS NULL)),
    CHECK ((promoted_at IS NULL) = (promoted_by_id IS NULL)),
    CHECK (lifecycle NOT IN ('validated','production') OR validation_artifact_id IS NOT NULL)
);

ALTER TABLE app.run_step
    ADD CONSTRAINT run_step_policy_release_fk
    FOREIGN KEY (policy_release_id) REFERENCES meta.policy_release(id);

CREATE TABLE design.objective_spec (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    schema_version text NOT NULL,
    program_id uuid NOT NULL REFERENCES design.project(id),
    campaign_id uuid NOT NULL REFERENCES design.campaign(id),
    target_ref jsonb NOT NULL,
    spec_artifact_id uuid NOT NULL REFERENCES app.artifact(id),
    digest bytea NOT NULL CHECK (octet_length(digest) = 32),
    supersedes_id uuid REFERENCES design.objective_spec(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by_kind app.actor_kind NOT NULL,
    created_by_id text NOT NULL,
    UNIQUE (digest),
    CHECK (target_ref->>'kind' = 'target' AND coalesce(target_ref->>'id','') <> ''),
    CHECK (supersedes_id IS NULL OR supersedes_id <> id)
);
CREATE INDEX objective_spec_campaign_idx ON design.objective_spec (campaign_id, created_at DESC);

CREATE TABLE design.motif_cycle (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    mission_id uuid NOT NULL REFERENCES app.mission(id),
    run_id uuid NOT NULL REFERENCES app.run(id),
    objective_spec_id uuid NOT NULL REFERENCES design.objective_spec(id),
    program_snapshot_artifact_id uuid NOT NULL REFERENCES app.artifact(id),
    run_plan_artifact_id uuid NOT NULL REFERENCES app.artifact(id),
    proposal_artifact_id uuid REFERENCES app.artifact(id),
    evaluation_artifact_id uuid REFERENCES app.artifact(id),
    portfolio_artifact_id uuid REFERENCES app.artifact(id),
    cycle_report_artifact_id uuid REFERENCES app.artifact(id),
    state design.motif_cycle_state NOT NULL DEFAULT 'planned',
    root_seed bigint NOT NULL CHECK (root_seed >= 0),
    approval_state jsonb NOT NULL DEFAULT '{}'::jsonb,
    outcome_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    UNIQUE (run_id),
    CHECK (finished_at IS NULL OR finished_at >= created_at)
);
CREATE INDEX motif_cycle_campaign_idx
    ON design.motif_cycle (objective_spec_id, created_at DESC);

-- Additive proposal metadata. Detailed edit/reaction traces remain Artifacts.
ALTER TABLE IF EXISTS design.idea
    ADD COLUMN IF NOT EXISTS proposal_strategy text,
    ADD COLUMN IF NOT EXISTS generator_release_id uuid REFERENCES meta.model_release(id),
    ADD COLUMN IF NOT EXISTS route_status text,
    ADD COLUMN IF NOT EXISTS review_status text,
    ADD COLUMN IF NOT EXISTS disposition text,
    ADD COLUMN IF NOT EXISTS detail_artifact_id uuid REFERENCES app.artifact(id);

COMMENT ON TABLE app.dataset_snapshot IS
    'Immutable assay/protocol/identity-resolved dataset release; CSV folders are not authority.';
COMMENT ON TABLE meta.model_release IS
    'Governed model execution identity, checkpoint, data, calibration, validation and lifecycle.';
COMMENT ON TABLE meta.calibration_release IS
    'Immutable endpoint- and domain-scoped calibration evidence for a model release.';
COMMENT ON TABLE meta.policy_release IS
    'Versioned policies that decide what is generated, computed and selected.';
COMMENT ON TABLE design.objective_spec IS
    'Immutable executable Design Brief. Updates create a superseding version.';
COMMENT ON TABLE design.motif_cycle IS
    'One frozen closed-loop Motif attempt linked to Mission, Run, releases and output Artifacts.';

INSERT INTO meta.migration (filename, sha256, content_sha256, hash_source)
VALUES ('022_motif_foundation.sql', '\x99fe2f898075d0060b492fb0b13c926ad14ecd237e3427e530db20252c40a3bc'::bytea,
        '\x99fe2f898075d0060b492fb0b13c926ad14ecd237e3427e530db20252c40a3bc'::bytea, 'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
