-- 021 · durable Run DAG, Allocation, Shard, Attempt, lease/fencing and outbox.
--
-- This migration is additive. Existing app.job and app.run_job remain compatible
-- while InvocationService is migrated behind a facade.
BEGIN;

CREATE TYPE app.run_step_state AS ENUM (
    'planned','ready','queued','running','waiting_approval',
    'completed','failed','cancelled','skipped');
CREATE TYPE app.run_step_edge_kind AS ENUM (
    'on_success','on_failure','always','if_metric','if_artifact_present',
    'if_approval','fan_out','fan_in');
CREATE TYPE app.job_attempt_state AS ENUM (
    'created','admitted','submitted','starting','running','checkpointing',
    'succeeded','failed','cancel_requested','cancelled','preempted','lost','superseded');
CREATE TYPE app.execution_backend AS ENUM (
    'inline','local_cpu','local_gpu','slurm','kubernetes','hpc_relay');
CREATE TYPE app.allocation_state AS ENUM (
    'created','submitted','pending','running','succeeded','failed','cancelled','lost');
CREATE TYPE app.job_shard_state AS ENUM (
    'planned','ready','running','succeeded','failed','cancelled','skipped');

CREATE TABLE app.run_step (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL REFERENCES app.run(id) ON DELETE CASCADE,
    step_key text NOT NULL CHECK (step_key ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$'),
    kind text NOT NULL DEFAULT 'compute'
        CHECK (kind IN ('compute','approval','barrier','projection','cleanup')),
    method_row_id uuid REFERENCES meta.method(id),
    state app.run_step_state NOT NULL DEFAULT 'planned',
    ordinal_hint integer NOT NULL DEFAULT 0 CHECK (ordinal_hint >= 0),
    required boolean NOT NULL DEFAULT true,
    input_manifest_artifact_id uuid REFERENCES app.artifact(id),
    policy_release_id uuid,
    condition_context jsonb NOT NULL DEFAULT '{}'::jsonb,
    progress_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    approval_gate text,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    UNIQUE (run_id, step_key),
    CHECK (finished_at IS NULL OR started_at IS NOT NULL),
    CHECK (finished_at IS NULL OR finished_at >= started_at),
    CHECK ((kind = 'compute') = (method_row_id IS NOT NULL) OR kind <> 'compute')
);

CREATE INDEX run_step_state_idx ON app.run_step (run_id, state, ordinal_hint);

CREATE TABLE app.run_step_edge (
    source_step_id uuid NOT NULL REFERENCES app.run_step(id) ON DELETE CASCADE,
    target_step_id uuid NOT NULL REFERENCES app.run_step(id) ON DELETE CASCADE,
    kind app.run_step_edge_kind NOT NULL,
    condition jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_step_id, target_step_id, kind),
    CHECK (source_step_id <> target_step_id)
);
CREATE INDEX run_step_edge_target_idx ON app.run_step_edge (target_step_id);

CREATE TABLE app.run_step_job (
    step_id uuid NOT NULL REFERENCES app.run_step(id) ON DELETE CASCADE,
    job_id uuid NOT NULL REFERENCES app.job(id),
    ordinal integer NOT NULL DEFAULT 0 CHECK (ordinal >= 0),
    PRIMARY KEY (step_id, job_id),
    UNIQUE (step_id, ordinal)
);

CREATE TABLE app.job_attempt (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id uuid NOT NULL REFERENCES app.job(id) ON DELETE CASCADE,
    attempt_no integer NOT NULL CHECK (attempt_no > 0),
    state app.job_attempt_state NOT NULL DEFAULT 'created',
    execution_digest bytea NOT NULL CHECK (octet_length(execution_digest) = 32),
    fencing_token bigint NOT NULL CHECK (fencing_token > 0),
    lease_owner text,
    lease_expires_at timestamptz,
    heartbeat_at timestamptz,
    cancellation_requested_at timestamptz,
    checkpoint_artifact_id uuid REFERENCES app.artifact(id),
    retry_class text,
    failure_code text,
    failure_detail jsonb,
    progress_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    started_at timestamptz,
    finished_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (job_id, attempt_no),
    UNIQUE (job_id, fencing_token),
    CHECK ((lease_owner IS NULL) = (lease_expires_at IS NULL)),
    CHECK (heartbeat_at IS NULL OR lease_owner IS NOT NULL),
    CHECK (finished_at IS NULL OR started_at IS NOT NULL),
    CHECK (finished_at IS NULL OR finished_at >= started_at)
);

CREATE UNIQUE INDEX job_attempt_one_live
    ON app.job_attempt (job_id)
 WHERE state IN ('admitted','submitted','starting','running','checkpointing','cancel_requested');
CREATE INDEX job_attempt_lease_idx
    ON app.job_attempt (lease_expires_at)
 WHERE state IN ('starting','running','checkpointing','cancel_requested');

CREATE TABLE app.execution_allocation (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    attempt_id uuid NOT NULL REFERENCES app.job_attempt(id) ON DELETE CASCADE,
    backend app.execution_backend NOT NULL,
    site text,
    scheduler_identifier text,
    state app.allocation_state NOT NULL DEFAULT 'created',
    resource_request jsonb NOT NULL,
    resource_grant jsonb,
    placement jsonb NOT NULL DEFAULT '{}'::jsonb,
    scheduler_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    submitted_at timestamptz,
    started_at timestamptz,
    finished_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (backend, site, scheduler_identifier),
    CHECK (scheduler_identifier IS NOT NULL OR state = 'created'),
    CHECK (finished_at IS NULL OR started_at IS NOT NULL),
    CHECK (finished_at IS NULL OR finished_at >= started_at)
);
CREATE INDEX execution_allocation_attempt_idx ON app.execution_allocation (attempt_id, created_at DESC);
CREATE INDEX execution_allocation_state_idx ON app.execution_allocation (backend, state, created_at);

CREATE TABLE app.job_shard (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id uuid NOT NULL REFERENCES app.job(id) ON DELETE CASCADE,
    step_id uuid REFERENCES app.run_step(id) ON DELETE CASCADE,
    shard_index integer NOT NULL CHECK (shard_index >= 0),
    shard_key text NOT NULL,
    partition_digest bytea NOT NULL CHECK (octet_length(partition_digest) = 32),
    input_manifest_artifact_id uuid NOT NULL REFERENCES app.artifact(id),
    output_manifest_artifact_id uuid REFERENCES app.artifact(id),
    item_count integer NOT NULL CHECK (item_count >= 0),
    state app.job_shard_state NOT NULL DEFAULT 'planned',
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    UNIQUE (job_id, shard_index),
    UNIQUE (job_id, shard_key),
    CHECK (finished_at IS NULL OR started_at IS NOT NULL),
    CHECK (finished_at IS NULL OR finished_at >= started_at)
);
CREATE INDEX job_shard_state_idx ON app.job_shard (job_id, state, shard_index);

-- app.blob remains the content identity. Locations allow large bytes to live outside
-- PostgreSQL without changing Artifact identity.
CREATE TABLE app.blob_location (
    blob_sha256 bytea NOT NULL REFERENCES app.blob(sha256) ON DELETE CASCADE,
    backend text NOT NULL CHECK (backend IN ('postgres_inline','local_cas','s3','shared_fs')),
    locator text NOT NULL,
    stored_size_bytes bigint NOT NULL CHECK (stored_size_bytes >= 0),
    checksum_verified_at timestamptz,
    storage_class text NOT NULL DEFAULT 'standard',
    encryption_key_ref text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (blob_sha256, backend, locator)
);
CREATE INDEX blob_location_backend_idx ON app.blob_location (backend, created_at);

CREATE TABLE app.outbox_event (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_key text NOT NULL UNIQUE,
    aggregate_kind text NOT NULL,
    aggregate_id text NOT NULL,
    event_type text NOT NULL,
    event_version integer NOT NULL DEFAULT 1 CHECK (event_version > 0),
    payload jsonb NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    publish_attempts integer NOT NULL DEFAULT 0 CHECK (publish_attempts >= 0),
    last_error text
);
CREATE INDEX outbox_unpublished_idx
    ON app.outbox_event (occurred_at, id)
 WHERE published_at IS NULL;

CREATE TABLE app.projector_cursor (
    projector text PRIMARY KEY,
    last_event_id bigint NOT NULL DEFAULT 0 CHECK (last_event_id >= 0),
    lease_owner text,
    lease_expires_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    last_error text,
    CHECK ((lease_owner IS NULL) = (lease_expires_at IS NULL))
);

COMMENT ON TABLE app.job_attempt IS
    'One durable execution attempt for a semantic Job. fencing_token rejects late stale results.';
COMMENT ON TABLE app.execution_allocation IS
    'Scheduler allocation metadata. Slurm/Kubernetes IDs are implementation details, not Job identity.';
COMMENT ON TABLE app.job_shard IS
    'Coarse deterministic partitions. Per-item outputs live in immutable Artifacts, not one Job row per item.';
COMMENT ON TABLE app.outbox_event IS
    'Transactional projection boundary for read models, notifications and telemetry mirrors.';

INSERT INTO meta.migration (filename, sha256, content_sha256, hash_source)
VALUES ('021_execution_control_plane.sql', '\xec864b4d321cbd1f29ac964bbdd0471491002c086a45093dd437574386853c01'::bytea,
        '\xec864b4d321cbd1f29ac964bbdd0471491002c086a45093dd437574386853c01'::bytea, 'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
