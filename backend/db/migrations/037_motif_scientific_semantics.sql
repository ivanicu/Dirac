-- 037 · Motif scientific semantics, evidence assembly, action planning and resource leases.
--
-- Execution facts, scientific interpretation and portfolio disposition are intentionally
-- orthogonal.  The scientific object spine gives every prepared state/hypothesis a
-- content identity and explicit dependencies so invalidation is a graph operation.
BEGIN;

CREATE TYPE design.motif_scientific_object_kind AS ENUM (
    'submitted_compound_record','chemical_entity','chemical_state_ensemble',
    'chemical_microstate','conformer_ensemble','conformer_hypothesis',
    'protein_structure_source','prepared_receptor_state','binding_site_hypothesis',
    'pose_hypothesis','pose_ensemble','complex_hypothesis','parameterized_system',
    'simulation_run','free_energy_transformation','measurement_observation',
    'prediction_evidence');
CREATE TYPE design.motif_applicability_state AS ENUM (
    'unknown','applicable','not_applicable','unsupported','outside_validated_domain');
CREATE TYPE design.motif_scientific_state AS ENUM (
    'not_assessed','accepted','provisional','rejected');
CREATE TYPE design.motif_decision_disposition AS ENUM (
    'pending','selected','reserve','deferred','rejected','refused');
CREATE TYPE design.motif_claim_eligibility AS ENUM (
    'eligible','ineligible_technical_smoke','ineligible_unvalidated_method',
    'ineligible_outside_validated_domain','ineligible_provisional_quality',
    'ineligible_stale','ineligible_conflict','ineligible_missing_dependency');
CREATE TYPE design.motif_evidence_kind AS ENUM (
    'scalar_estimate','censored_estimate','distribution','pose_ensemble','trajectory',
    'transformation','network_estimate','qualitative_gate','conflict');
CREATE TYPE design.motif_action_kind AS ENUM (
    'compute','recompute','extend','aggregate','review','stop','defer');

ALTER TABLE meta.model_release
    ADD COLUMN scientific_lifecycle text NOT NULL DEFAULT 'candidate_unvalidated'
        CHECK (scientific_lifecycle IN (
            'technical_smoke','candidate_unvalidated','scientific_candidate',
            'validated_release','promoted_release','retired'));

CREATE TABLE meta.method_manifest (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    method_row_id uuid NOT NULL REFERENCES meta.method(id),
    release_name text NOT NULL,
    schema_version text NOT NULL,
    manifest_artifact_id uuid NOT NULL REFERENCES app.artifact(id),
    manifest_digest bytea NOT NULL CHECK (octet_length(manifest_digest) = 32),
    input_schema_digest bytea NOT NULL CHECK (octet_length(input_schema_digest) = 32),
    output_schema_digest bytea NOT NULL CHECK (octet_length(output_schema_digest) = 32),
    parameter_schema_digest bytea NOT NULL CHECK (octet_length(parameter_schema_digest) = 32),
    container_image_digest text CHECK (
        container_image_digest IS NULL OR container_image_digest ~ '^sha256:[0-9a-f]{64}$'),
    runtime_artifact_id uuid REFERENCES app.artifact(id),
    external_binary_manifest jsonb NOT NULL DEFAULT '{}'::jsonb,
    numeric_contract jsonb NOT NULL,
    determinism_contract jsonb NOT NULL,
    checkpoint_contract jsonb NOT NULL,
    capability_contract jsonb NOT NULL,
    lifecycle text NOT NULL CHECK (lifecycle IN (
        'technical_smoke','candidate_unvalidated','scientific_candidate',
        'validated_release','promoted_release','retired')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (method_row_id, release_name),
    UNIQUE (manifest_digest),
    CHECK ((container_image_digest IS NOT NULL) <> (runtime_artifact_id IS NOT NULL))
);

CREATE TABLE design.motif_scientific_object (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    object_kind design.motif_scientific_object_kind NOT NULL,
    semantic_digest bytea NOT NULL CHECK (octet_length(semantic_digest) = 32),
    document_artifact_id uuid NOT NULL REFERENCES app.artifact(id),
    method_manifest_id uuid REFERENCES meta.method_manifest(id),
    condition_ref jsonb,
    applicability design.motif_applicability_state NOT NULL DEFAULT 'unknown',
    scientific_state design.motif_scientific_state NOT NULL DEFAULT 'not_assessed',
    disposition design.motif_decision_disposition NOT NULL DEFAULT 'pending',
    claim_eligibility design.motif_claim_eligibility NOT NULL
        DEFAULT 'ineligible_unvalidated_method',
    reason_codes text[] NOT NULL DEFAULT '{}',
    supersedes_id uuid REFERENCES design.motif_scientific_object(id),
    invalidated_at timestamptz,
    invalidation_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (object_kind, semantic_digest),
    CHECK (condition_ref IS NULL OR (
        coalesce(condition_ref->>'kind','') <> '' AND coalesce(condition_ref->>'id','') <> '')),
    CHECK (supersedes_id IS NULL OR supersedes_id <> id),
    CHECK ((invalidated_at IS NULL) = (invalidation_code IS NULL)),
    CHECK (disposition <> 'refused' OR cardinality(reason_codes) > 0),
    CHECK (NOT (applicability IN ('not_applicable','unsupported')
               AND claim_eligibility = 'eligible'))
);
CREATE INDEX motif_scientific_object_kind_idx
    ON design.motif_scientific_object (object_kind, created_at DESC);
CREATE INDEX motif_scientific_object_live_idx
    ON design.motif_scientific_object (object_kind, semantic_digest)
    WHERE invalidated_at IS NULL;

CREATE TABLE design.motif_scientific_dependency (
    object_id uuid NOT NULL REFERENCES design.motif_scientific_object(id) ON DELETE CASCADE,
    dependency_id uuid NOT NULL REFERENCES design.motif_scientific_object(id),
    dependency_role text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (object_id, dependency_id, dependency_role),
    CHECK (object_id <> dependency_id)
);
CREATE INDEX motif_scientific_dependency_reverse_idx
    ON design.motif_scientific_dependency (dependency_id, object_id);

CREATE TABLE design.motif_method_outcome (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    attempt_id uuid NOT NULL REFERENCES app.job_attempt(id),
    execution_state app.job_attempt_state NOT NULL,
    manifest_artifact_id uuid REFERENCES app.artifact(id),
    telemetry_artifact_id uuid REFERENCES app.artifact(id),
    error_document jsonb,
    committed_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (attempt_id),
    CHECK (execution_state IN ('succeeded','failed','cancelled','lost')),
    CHECK ((execution_state = 'succeeded') = (manifest_artifact_id IS NOT NULL)),
    CHECK (execution_state <> 'succeeded' OR error_document IS NULL)
);

CREATE TABLE design.motif_quality_assessment (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    method_outcome_id uuid NOT NULL REFERENCES design.motif_method_outcome(id),
    assessment_release_id uuid NOT NULL REFERENCES meta.policy_release(id),
    applicability design.motif_applicability_state NOT NULL,
    scientific_state design.motif_scientific_state NOT NULL,
    claim_eligibility design.motif_claim_eligibility NOT NULL,
    reason_codes text[] NOT NULL DEFAULT '{}',
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    report_artifact_id uuid NOT NULL REFERENCES app.artifact(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (method_outcome_id, assessment_release_id)
);

CREATE TABLE design.motif_evidence_item (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    evidence_kind design.motif_evidence_kind NOT NULL,
    subject_object_id uuid NOT NULL REFERENCES design.motif_scientific_object(id),
    condition_ref jsonb NOT NULL,
    method_manifest_id uuid NOT NULL REFERENCES meta.method_manifest(id),
    method_outcome_id uuid NOT NULL REFERENCES design.motif_method_outcome(id),
    quality_assessment_id uuid NOT NULL REFERENCES design.motif_quality_assessment(id),
    payload_schema_uri text NOT NULL,
    payload_artifact_id uuid NOT NULL REFERENCES app.artifact(id),
    applicability design.motif_applicability_state NOT NULL,
    scientific_state design.motif_scientific_state NOT NULL,
    claim_eligibility design.motif_claim_eligibility NOT NULL,
    stale_at timestamptz,
    stale_reason_code text,
    supersedes_id uuid REFERENCES design.motif_evidence_item(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (coalesce(condition_ref->>'kind','') <> '' AND coalesce(condition_ref->>'id','') <> ''),
    CHECK ((stale_at IS NULL) = (stale_reason_code IS NULL)),
    CHECK (stale_at IS NULL OR claim_eligibility <> 'eligible'),
    CHECK (supersedes_id IS NULL OR supersedes_id <> id)
);
CREATE INDEX motif_evidence_subject_idx
    ON design.motif_evidence_item (subject_object_id, evidence_kind, created_at DESC);

CREATE TABLE design.motif_evidence_dependency (
    evidence_id uuid NOT NULL REFERENCES design.motif_evidence_item(id) ON DELETE CASCADE,
    dependency_ref jsonb NOT NULL,
    dependency_kind text NOT NULL CHECK (dependency_kind IN (
        'input','shared_assumption','calibration','parameterization','structure','supersedes')),
    PRIMARY KEY (evidence_id, dependency_kind, dependency_ref),
    CHECK (coalesce(dependency_ref->>'kind','') <> '' AND coalesce(dependency_ref->>'id','') <> '')
);

CREATE TABLE design.motif_evidence_snapshot (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    program_id uuid NOT NULL REFERENCES design.project(id),
    condition_ref jsonb NOT NULL,
    assembly_policy_id uuid NOT NULL REFERENCES meta.policy_release(id),
    manifest_artifact_id uuid NOT NULL REFERENCES app.artifact(id),
    digest bytea NOT NULL CHECK (octet_length(digest) = 32),
    dependency_summary jsonb NOT NULL,
    exclusion_summary jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (digest)
);
CREATE TABLE design.motif_evidence_snapshot_item (
    evidence_snapshot_id uuid NOT NULL REFERENCES design.motif_evidence_snapshot(id) ON DELETE CASCADE,
    evidence_item_id uuid NOT NULL REFERENCES design.motif_evidence_item(id),
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    PRIMARY KEY (evidence_snapshot_id, evidence_item_id),
    UNIQUE (evidence_snapshot_id, ordinal)
);

CREATE TABLE design.motif_decision_snapshot (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    evidence_snapshot_id uuid NOT NULL REFERENCES design.motif_evidence_snapshot(id),
    objective_spec_id uuid NOT NULL REFERENCES design.objective_spec(id),
    utility_contract_artifact_id uuid NOT NULL REFERENCES app.artifact(id),
    decision_artifact_id uuid NOT NULL REFERENCES app.artifact(id),
    disposition design.motif_decision_disposition NOT NULL,
    reason_codes text[] NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (evidence_snapshot_id, objective_spec_id, utility_contract_artifact_id),
    CHECK (disposition <> 'refused' OR cardinality(reason_codes) > 0)
);

CREATE TABLE app.resource_lease (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_kind text NOT NULL CHECK (owner_kind IN ('job','attempt','run','campaign')),
    owner_id uuid NOT NULL,
    campaign_id uuid REFERENCES design.campaign(id),
    backend app.execution_backend NOT NULL,
    request jsonb NOT NULL,
    estimated_cost jsonb NOT NULL DEFAULT '{}'::jsonb,
    actual_usage jsonb NOT NULL DEFAULT '{}'::jsonb,
    fencing_token bigint NOT NULL CHECK (fencing_token > 0),
    state text NOT NULL DEFAULT 'active' CHECK (state IN ('active','released','expired','revoked')),
    lease_owner text NOT NULL,
    expires_at timestamptz NOT NULL,
    heartbeat_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    released_at timestamptz,
    UNIQUE (owner_kind, owner_id, fencing_token),
    CHECK (expires_at > created_at),
    CHECK ((state = 'active') = (released_at IS NULL))
);
CREATE UNIQUE INDEX resource_lease_one_active_owner
    ON app.resource_lease (owner_kind, owner_id) WHERE state = 'active';
CREATE INDEX resource_lease_expiry_idx ON app.resource_lease (expires_at) WHERE state = 'active';

CREATE TABLE design.motif_routing_action (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    evidence_snapshot_id uuid NOT NULL REFERENCES design.motif_evidence_snapshot(id),
    decision_snapshot_id uuid REFERENCES design.motif_decision_snapshot(id),
    action_kind design.motif_action_kind NOT NULL,
    fidelity_label text CHECK (fidelity_label IN ('F0','F1','F2','F3','F4','F5','not_applicable')),
    subject_ref jsonb NOT NULL,
    scientific_question text NOT NULL,
    required_input_refs jsonb NOT NULL,
    outcome_model_release_id uuid REFERENCES meta.model_release(id),
    expected_utility_delta double precision,
    priced_resource_cost double precision,
    expected_net_value double precision,
    p_decision_change double precision CHECK (
        p_decision_change IS NULL OR p_decision_change BETWEEN 0 AND 1),
    resource_estimate jsonb NOT NULL,
    budget_lease_id uuid REFERENCES app.resource_lease(id),
    policy_release_id uuid NOT NULL REFERENCES meta.policy_release(id),
    action_fingerprint bytea NOT NULL CHECK (octet_length(action_fingerprint) = 32),
    reason_codes text[] NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (evidence_snapshot_id, action_fingerprint),
    CHECK (coalesce(subject_ref->>'kind','') <> '' AND coalesce(subject_ref->>'id','') <> ''),
    CHECK (scientific_question <> ''),
    CHECK (cardinality(reason_codes) > 0)
);

CREATE TABLE app.artifact_commit (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    logical_job_id uuid NOT NULL REFERENCES app.job(id),
    attempt_id uuid NOT NULL REFERENCES app.job_attempt(id),
    fencing_token bigint NOT NULL CHECK (fencing_token > 0),
    manifest_artifact_id uuid NOT NULL REFERENCES app.artifact(id),
    terminal_event_key text NOT NULL,
    committed_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (logical_job_id),
    UNIQUE (attempt_id),
    UNIQUE (terminal_event_key)
);

CREATE TABLE meta.motif_capability_support (
    method_manifest_id uuid NOT NULL REFERENCES meta.method_manifest(id) ON DELETE CASCADE,
    system_type text NOT NULL CHECK (system_type IN (
        'neutral','charged','charge_change','metal','covalent','macrocycle','peptide',
        'noncanonical_residue','membrane')),
    applicability design.motif_applicability_state NOT NULL,
    validation_artifact_id uuid REFERENCES app.artifact(id),
    reason_codes text[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (method_manifest_id, system_type),
    CHECK (applicability <> 'applicable' OR validation_artifact_id IS NOT NULL)
);

COMMENT ON TABLE design.motif_scientific_object IS
    'Content-addressed chemical/state/structure/simulation object graph; no CandidateState union.';
COMMENT ON TABLE design.motif_method_outcome IS
    'Operational result only. Scientific values appear only after QualityAssessment as EvidenceItem.';
COMMENT ON TABLE design.motif_evidence_item IS
    'Typed, condition-scoped scientific evidence with explicit dependency and staleness semantics.';
COMMENT ON TABLE design.motif_routing_action IS
    'Scientific Action Planner output. F0-F5 is a reporting label, never a transition state.';
COMMENT ON TABLE app.resource_lease IS
    'Atomic host/campaign reservation independent of Kueue admission, with expiry and fencing.';
COMMENT ON TABLE app.artifact_commit IS
    'Exactly-once terminal scientific manifest commit on top of at-least-once execution attempts.';

INSERT INTO meta.migration (filename,sha256,content_sha256,hash_source)
VALUES ('037_motif_scientific_semantics.sql',
        '\x9513d8c0561721da8670140115f472e918d88aa405a8d3f7f317e1107664ad90'::bytea,
        '\x9513d8c0561721da8670140115f472e918d88aa405a8d3f7f317e1107664ad90'::bytea,
        'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
