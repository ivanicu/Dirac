-- 018 · node-keyed command observations for the architecture Digital Twin.
--
-- Jobs observe long compute, but eleven of Dirac's semantic commands are queries or
-- controls and may never mint a Job. This ledger records the command boundary itself,
-- then joins a linked Job's eventual outcome so a successful submission that later
-- fails is not misreported as a successful workflow.
BEGIN;

CREATE TABLE app.command_trace (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    command_id citext NOT NULL CHECK (btrim(command_id::text) <> ''),
    command_version integer NOT NULL CHECK (command_version > 0),
    actor_kind app.actor_kind NOT NULL,
    actor_id text NOT NULL CHECK (btrim(actor_id) <> ''),
    request_id text CHECK (request_id IS NULL OR btrim(request_id) <> ''),
    method_id citext,
    method_version citext,
    job_id uuid REFERENCES app.job(id),
    dispatch_outcome app.job_outcome_class NOT NULL,
    cache_source text,
    error_code text,
    duration_seconds numeric(12,6) NOT NULL CHECK (duration_seconds >= 0),
    started_at timestamptz NOT NULL,
    finished_at timestamptz NOT NULL,
    meta jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK (finished_at >= started_at),
    CHECK ((dispatch_outcome = 'success') = (error_code IS NULL))
);

CREATE INDEX command_trace_command_time
    ON app.command_trace (command_id, finished_at DESC);
CREATE INDEX command_trace_job
    ON app.command_trace (job_id) WHERE job_id IS NOT NULL;
CREATE INDEX command_trace_outcome_time
    ON app.command_trace (dispatch_outcome, finished_at DESC);

CREATE VIEW app.v_command_trace AS
SELECT t.id,
       t.command_id,
       t.command_version,
       t.actor_kind,
       t.actor_id,
       t.request_id,
       t.method_id,
       t.method_version,
       t.job_id,
       j.state AS job_state,
       coalesce(j.outcome_class, t.dispatch_outcome) AS outcome_class,
       t.dispatch_outcome,
       t.cache_source,
       coalesce(j.error_code::text, t.error_code) AS error_code,
       t.duration_seconds,
       t.started_at,
       t.finished_at,
       t.meta
  FROM app.command_trace t
  LEFT JOIN app.job j ON j.id = t.job_id;

CREATE VIEW app.v_command_observation AS
SELECT command_id,
       command_version,
       count(*) AS invocation_count,
       count(*) FILTER (WHERE outcome_class = 'success') AS success_count,
       count(*) FILTER (WHERE outcome_class = 'expected_refusal') AS expected_refusal_count,
       count(*) FILTER (WHERE outcome_class = 'scientific_failure') AS scientific_failure_count,
       count(*) FILTER (WHERE outcome_class = 'operational_failure') AS operational_failure_count,
       count(*) FILTER (WHERE job_id IS NOT NULL) AS job_count,
       count(*) FILTER (WHERE cache_source = 'db') AS cache_hit_count,
       round(avg(duration_seconds), 6) AS mean_dispatch_seconds,
       round(percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_seconds)::numeric, 6)
           AS p95_dispatch_seconds,
       min(started_at) AS first_observed_at,
       max(finished_at) AS last_observed_at
  FROM app.v_command_trace
 GROUP BY command_id, command_version;

COMMENT ON TABLE app.command_trace IS
    'One durable observation per semantic command dispatch. Failure to write this '
    'observability record never changes the command result.';
COMMENT ON VIEW app.v_command_trace IS
    'Trace rows enriched with the linked Job terminal outcome when one exists.';
COMMENT ON VIEW app.v_command_observation IS
    'Command-node runtime aggregates consumed by the auto-generated architecture twin.';

INSERT INTO meta.migration (filename, sha256, content_sha256, hash_source)
VALUES ('018_command_observations.sql', '\x17526b8036f7f039bd38fea3ef57815137472e9424ca133d2b1cb6b2f3fa671a'::bytea,
        '\x17526b8036f7f039bd38fea3ef57815137472e9424ca133d2b1cb6b2f3fa671a'::bytea, 'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
