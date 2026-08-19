-- 049 · durable AI-guided FEP evidence-acquisition loop.
--
-- The model is confined to producing a bounded research.proposal Artifact.
-- This migration persists the deterministic controller checkpoint, append-only
-- audit events, human decisions, and explicit Artifact ownership needed to
-- resume the loop after process or host failure.
BEGIN;

ALTER TYPE app.job_error
    ADD VALUE IF NOT EXISTS 'PROVIDER_UNAVAILABLE' BEFORE 'INTERNAL';
ALTER TYPE app.job_error
    ADD VALUE IF NOT EXISTS 'MODEL_OUTPUT_INVALID' BEFORE 'INTERNAL';

CREATE TABLE app.research_loop_state (
    run_id uuid PRIMARY KEY REFERENCES app.run(id) ON DELETE CASCADE,

    request_key text NOT NULL,
    program_id uuid NOT NULL REFERENCES design.project(id) ON DELETE RESTRICT,
    campaign_id uuid NOT NULL REFERENCES design.campaign(id) ON DELETE RESTRICT,

    actor_kind app.actor_kind NOT NULL,
    actor_id text NOT NULL,

    state text NOT NULL
        CHECK (state IN (
            'active',
            'waiting_approval',
            'blocked',
            'paused',
            'completed',
            'cancelled',
            'failed'
        )),

    stage text NOT NULL
        CHECK (stage IN (
            'bootstrap',
            'snapshot_context',
            'reason',
            'validate_proposal',
            'select_action',
            'prepare_action',
            'await_approval',
            'dispatch',
            'wait_job',
            'observe',
            'refresh',
            'guard',
            'completed'
        )),

    iteration integer NOT NULL DEFAULT 0 CHECK (iteration >= 0),
    version bigint NOT NULL DEFAULT 1 CHECK (version > 0),

    intent text NOT NULL CHECK (length(trim(intent)) > 0),
    autonomy_class text NOT NULL
        CHECK (autonomy_class IN ('A0','A1','A2','A3')),

    provider_profile_id text NOT NULL,
    provider_profile_digest bytea NOT NULL
        CHECK (octet_length(provider_profile_digest) = 32),

    prompt_release_id text NOT NULL,
    prompt_release_digest bytea NOT NULL
        CHECK (octet_length(prompt_release_digest) = 32),

    action_catalog_digest bytea NOT NULL
        CHECK (octet_length(action_catalog_digest) = 32),

    data_classification text NOT NULL
        CHECK (data_classification IN (
            'public',
            'internal',
            'partner_confidential',
            'restricted',
            'regulated'
        )),

    policy jsonb NOT NULL,
    budget_remaining jsonb NOT NULL,
    budget_spent jsonb NOT NULL DEFAULT '{}'::jsonb,

    context_artifact_id uuid REFERENCES app.artifact(id),
    context_digest bytea
        CHECK (context_digest IS NULL OR octet_length(context_digest) = 32),

    proposal_artifact_id uuid REFERENCES app.artifact(id),
    proposal_context_digest bytea
        CHECK (
            proposal_context_digest IS NULL
            OR octet_length(proposal_context_digest) = 32
        ),

    pending_action jsonb,
    stage_jobs jsonb NOT NULL DEFAULT '{}'::jsonb,
    stage_attempts jsonb NOT NULL DEFAULT '{}'::jsonb,
    outputs jsonb NOT NULL DEFAULT '{}'::jsonb,
    attention jsonb NOT NULL DEFAULT '{}'::jsonb,

    next_wake_at timestamptz NOT NULL DEFAULT now(),
    lease_owner text,
    lease_expires_at timestamptz,

    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,

    UNIQUE (actor_kind, actor_id, request_key),

    CHECK ((lease_owner IS NULL) = (lease_expires_at IS NULL)),
    CHECK (finished_at IS NULL OR finished_at >= created_at),
    CHECK (
        state NOT IN ('completed','cancelled','failed')
        OR finished_at IS NOT NULL
    )
);

CREATE UNIQUE INDEX research_loop_one_open_per_campaign
    ON app.research_loop_state (campaign_id)
    WHERE state IN (
        'active',
        'waiting_approval',
        'blocked',
        'paused'
    );

CREATE INDEX research_loop_runnable_idx
    ON app.research_loop_state (next_wake_at, created_at)
    WHERE state = 'active';

CREATE INDEX research_loop_lease_idx
    ON app.research_loop_state (lease_expires_at)
    WHERE state = 'active' AND lease_owner IS NOT NULL;

CREATE TABLE app.research_loop_event (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id uuid NOT NULL
        REFERENCES app.research_loop_state(run_id) ON DELETE CASCADE,

    sequence integer NOT NULL CHECK (sequence >= 0),
    event_type text NOT NULL,
    stage text NOT NULL,

    actor_kind app.actor_kind NOT NULL,
    actor_id text NOT NULL,
    automation_actor jsonb,

    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    artifact_id uuid REFERENCES app.artifact(id),

    occurred_at timestamptz NOT NULL DEFAULT now(),

    UNIQUE (run_id, sequence)
);

CREATE INDEX research_loop_event_run_idx
    ON app.research_loop_event (run_id, sequence);

CREATE TABLE app.research_loop_approval (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL
        REFERENCES app.research_loop_state(run_id) ON DELETE CASCADE,

    loop_version bigint NOT NULL CHECK (loop_version > 0),
    action_fingerprint bytea NOT NULL
        CHECK (octet_length(action_fingerprint) = 32),

    preview_artifact_id uuid NOT NULL REFERENCES app.artifact(id),
    command_input_digest bytea NOT NULL
        CHECK (octet_length(command_input_digest) = 32),

    source_versions jsonb NOT NULL,

    decision text NOT NULL
        CHECK (decision IN ('approved','rejected')),

    actor_kind app.actor_kind NOT NULL,
    actor_id text NOT NULL,
    rationale text NOT NULL CHECK (length(trim(rationale)) > 0),

    created_at timestamptz NOT NULL DEFAULT now(),

    UNIQUE (run_id, action_fingerprint)
);

CREATE TABLE app.research_loop_artifact (
    run_id uuid NOT NULL
        REFERENCES app.research_loop_state(run_id) ON DELETE CASCADE,
    artifact_id uuid NOT NULL REFERENCES app.artifact(id),
    role text NOT NULL
        CHECK (role IN (
            'research.context_snapshot',
            'research.proposal',
            'research.action_preview',
            'research.action_receipt',
            'research.loop_summary',
            'research.followup_draft'
        )),
    data_classification text NOT NULL
        CHECK (data_classification IN (
            'public',
            'internal',
            'partner_confidential',
            'restricted',
            'regulated'
        )),
    created_at timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (run_id, artifact_id)
);

INSERT INTO meta.migration (filename,sha256,content_sha256,hash_source)
VALUES ('049_research_loop.sql','\xc7ea0787bd9fab276c6ecb161325afb312f90c4a7f9e88e2bb16ee65476f3199'::bytea,
        '\xc7ea0787bd9fab276c6ecb161325afb312f90c4a7f9e88e2bb16ee65476f3199'::bytea,'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
