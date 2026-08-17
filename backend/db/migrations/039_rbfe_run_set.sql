-- 039 · Server-owned, reconnectable orchestration for one governed RBFE edge.
BEGIN;

CREATE TABLE app.rbfe_run_set (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    request_key text NOT NULL UNIQUE,
    specification_digest bytea NOT NULL CHECK (octet_length(specification_digest)=32),
    specification jsonb NOT NULL,
    state text NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending','running','blocked','aggregating','completed','cancelled')),
    leg_jobs jsonb NOT NULL DEFAULT '{}'::jsonb,
    aggregate_job_id uuid REFERENCES app.job(id) ON DELETE RESTRICT,
    aggregate_output jsonb NOT NULL DEFAULT '{}'::jsonb,
    attention jsonb NOT NULL DEFAULT '{}'::jsonb,
    actor_kind app.actor_kind NOT NULL,
    actor_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    CHECK (finished_at IS NULL OR finished_at >= created_at)
);

CREATE INDEX rbfe_run_set_active_idx ON app.rbfe_run_set(state,updated_at)
    WHERE state IN ('pending','running','aggregating');

COMMENT ON TABLE app.rbfe_run_set IS
    'Durable orchestration only: six ordinary OpenFE Jobs plus one evidence aggregation Job. '
    'Scientific evidence remains in immutable artifacts and each Job retains its own execution identity.';

INSERT INTO meta.migration (filename,sha256,content_sha256,hash_source)
VALUES ('039_rbfe_run_set.sql','\x6dbce24001df70bb7cd0f85bcab8c8011d57d910ec5c6e3538812cced8ec3a42'::bytea,
        '\x6dbce24001df70bb7cd0f85bcab8c8011d57d910ec5c6e3538812cced8ec3a42'::bytea,'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
