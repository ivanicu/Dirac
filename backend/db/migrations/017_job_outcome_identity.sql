-- 017 · Job outcome semantics and invocation identity.
--
-- A failed computation is not automatically an operational incident. Budget,
-- unsupported-method and parameterisation refusals are expected answers at the
-- scientific boundary; INTERNAL is a service failure; UNCONVERGED is a scientific
-- result that needs review. Keeping those meanings in a generated column makes the
-- database, API and digital twin read the same classification without three writers.
BEGIN;

CREATE TYPE app.job_outcome_class AS ENUM (
    'success',
    'expected_refusal',
    'scientific_failure',
    'operational_failure',
    'cancelled'
);

CREATE FUNCTION app.classify_job_outcome(
    p_state app.job_state,
    p_error app.job_error
) RETURNS app.job_outcome_class
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT CASE
        WHEN p_state = 'done' THEN 'success'::app.job_outcome_class
        WHEN p_state = 'cancelled' THEN 'cancelled'::app.job_outcome_class
        WHEN p_state <> 'failed' THEN NULL
        WHEN p_error = 'INTERNAL' THEN 'operational_failure'::app.job_outcome_class
        WHEN p_error = 'UNCONVERGED' THEN 'scientific_failure'::app.job_outcome_class
        ELSE 'expected_refusal'::app.job_outcome_class
    END
$$;

COMMENT ON FUNCTION app.classify_job_outcome(app.job_state, app.job_error) IS
    'Canonical terminal Job classification. Active Jobs return NULL; expected '
    'scientific/caller refusals do not become operational incidents.';

ALTER TABLE app.job
    ADD COLUMN actor_kind app.actor_kind NOT NULL DEFAULT 'service',
    ADD COLUMN actor_id text NOT NULL DEFAULT 'legacy',
    ADD COLUMN command_id citext,
    ADD COLUMN request_id text,
    ADD COLUMN outcome_class app.job_outcome_class
        GENERATED ALWAYS AS (app.classify_job_outcome(state, error_code)) STORED,
    ADD CONSTRAINT job_actor_id_nonempty CHECK (btrim(actor_id) <> ''),
    ADD CONSTRAINT job_command_id_nonempty CHECK (
        command_id IS NULL OR btrim(command_id::text) <> ''),
    ADD CONSTRAINT job_request_id_nonempty CHECK (
        request_id IS NULL OR btrim(request_id) <> '');

COMMENT ON COLUMN app.job.actor_kind IS
    'Kind of actor that initiated the invocation; legacy rows are service/legacy.';
COMMENT ON COLUMN app.job.actor_id IS
    'Stable caller identity from the command envelope, not a transport username guess.';
COMMENT ON COLUMN app.job.command_id IS
    'Semantic command that caused the Job. NULL for direct method invocations.';
COMMENT ON COLUMN app.job.request_id IS
    'Cross-transport request correlation identifier when supplied by the caller.';
COMMENT ON COLUMN app.job.outcome_class IS
    'Database-generated terminal meaning; NULL while queued or running.';

CREATE INDEX job_outcome_attention
    ON app.job (outcome_class, finished_at DESC)
    WHERE outcome_class IN ('scientific_failure', 'operational_failure');
CREATE INDEX job_actor_created
    ON app.job (actor_kind, actor_id, created_at DESC);
CREATE INDEX job_command_created
    ON app.job (command_id, created_at DESC)
    WHERE command_id IS NOT NULL;

DROP VIEW app.v_attention;
CREATE VIEW app.v_attention AS
SELECT 'job'::app.object_kind AS kind,
       j.id::text AS object_id,
       j.outcome_class::text AS reason,
       CASE j.outcome_class
           WHEN 'operational_failure' THEN 'critical'
           ELSE 'review'
       END::text AS priority,
       j.finished_at AS at,
       j.actor_kind,
       j.actor_id,
       j.command_id::text AS command_id,
       j.error_detail AS detail
  FROM app.job j
 WHERE j.outcome_class IN ('scientific_failure', 'operational_failure')
UNION ALL
SELECT 'run'::app.object_kind,
       r.id::text,
       'waiting_approval',
       'approval',
       r.started_at,
       r.actor_kind,
       r.actor_id,
       NULL,
       NULL
  FROM app.run r
 WHERE r.state = 'waiting_approval';

COMMENT ON VIEW app.v_attention IS
    'Actionable work only: operational failures, scientific convergence failures and '
    'approval waits. Expected refusals and cancellations remain queryable on app.job '
    'without polluting the intervention queue.';

INSERT INTO meta.migration (filename, sha256, content_sha256, hash_source)
VALUES ('017_job_outcome_identity.sql', '\x294dc1a68f25b8f8a4b8fc548c9861708ed4ea8a47e85084a21869450bd7ebb7'::bytea,
        '\x294dc1a68f25b8f8a4b8fc548c9861708ed4ea8a47e85084a21869450bd7ebb7'::bytea, 'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
