-- 034 · Durable experimental-result trigger and stage ledger for Motif DMTA loops.

BEGIN;

CREATE TABLE design.motif_closed_loop_run (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    request_key text NOT NULL UNIQUE,
    specification_digest bytea NOT NULL UNIQUE CHECK (octet_length(specification_digest)=32),
    program_id uuid NOT NULL REFERENCES design.project(id) ON DELETE RESTRICT,
    campaign_id uuid NOT NULL REFERENCES design.campaign(id) ON DELETE RESTRICT,
    endpoint_key text NOT NULL,
    measurement_ids uuid[] NOT NULL CHECK (cardinality(measurement_ids) > 0),
    specification jsonb NOT NULL,
    state text NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending','running','blocked','completed','cancelled')),
    stage text NOT NULL DEFAULT 'snapshot'
        CHECK (stage IN ('snapshot','train','predict','acquire','completed')),
    stage_jobs jsonb NOT NULL DEFAULT '{}'::jsonb,
    outputs jsonb NOT NULL DEFAULT '{}'::jsonb,
    attention jsonb NOT NULL DEFAULT '{}'::jsonb,
    actor_kind app.actor_kind NOT NULL,
    actor_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    CHECK (finished_at IS NULL OR finished_at >= created_at)
);

CREATE INDEX motif_closed_loop_claim_idx
    ON design.motif_closed_loop_run(state,updated_at)
    WHERE state IN ('pending','running');
CREATE INDEX motif_closed_loop_campaign_idx
    ON design.motif_closed_loop_run(campaign_id,created_at DESC);

COMMENT ON TABLE design.motif_closed_loop_run IS
    'Durable, replayable result->snapshot->train->predict->acquire controller state. '
    'Each scientific stage remains an ordinary Dirac Job; this table owns only orchestration.';

INSERT INTO meta.migration (filename,sha256,content_sha256,hash_source)
VALUES ('034_motif_closed_loop_controller.sql','\x17feb7ca24780b808b1bea2d34bd1daf6c8bd5fe9a64d9cf02b562990916cfa5'::bytea,
        '\x17feb7ca24780b808b1bea2d34bd1daf6c8bd5fe9a64d9cf02b562990916cfa5'::bytea,'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
