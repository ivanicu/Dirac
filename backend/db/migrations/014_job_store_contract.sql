-- 014 · app.job becomes the durable implementation of the public JobStore contract.
--
-- input_sha256 describes input bytes. It is not the identity of an invocation: basis,
-- spin and other parameters can change the science while the molecule bytes remain the
-- same. The old unique index compensated with md5(params::text), which is both split
-- across two columns and weaker than every other content identity in this system.
-- request_digest is the one canonical SHA-256 used for open, dedup and wait.
--
-- Cancellation is also separated from interruption. cancel_requested_at records what a
-- caller asked; state='cancelled' records what the executor actually achieved. A running
-- SCF that cannot be interrupted must never be relabelled cancelled merely because the
-- browser stopped waiting.

BEGIN;

ALTER TABLE app.job
    ADD COLUMN request_digest bytea,
    ADD COLUMN durability text NOT NULL DEFAULT 'durable'
        CHECK (durability IN ('durable', 'process')),
    ADD COLUMN cancel_requested_at timestamptz,
    ADD COLUMN result_summary jsonb;

UPDATE app.job
   SET request_digest = digest(
       method_row_id::text || ':' || encode(input_sha256, 'hex') || ':' || params::text,
       'sha256');

ALTER TABLE app.job
    ALTER COLUMN request_digest SET NOT NULL,
    ADD CONSTRAINT job_request_digest_sha256
        CHECK (octet_length(request_digest) = 32);

DROP INDEX app.job_one_inflight;
CREATE UNIQUE INDEX job_one_inflight
    ON app.job (method_row_id, request_digest)
 WHERE state IN ('queued', 'running');

CREATE OR REPLACE VIEW app.v_job_live AS
SELECT j.id, m.method_id, m.version AS method_version, j.state,
       j.compound_id, j.budget_seconds, j.est_seconds,
       round(extract(epoch FROM now() - coalesce(j.started_at, j.created_at))::numeric, 1)
           AS age_seconds,
       j.worker, j.created_at, j.started_at,
       j.request_digest, j.durability, j.cancel_requested_at
  FROM app.job j JOIN meta.method m ON m.id = j.method_row_id
 WHERE j.state IN ('queued', 'running')
 ORDER BY j.created_at;

COMMENT ON COLUMN app.job.request_digest IS
    'Canonical SHA-256 of the complete scientific invocation identity; the sole in-flight dedup key beside method_row_id.';
COMMENT ON COLUMN app.job.cancel_requested_at IS
    'A caller asked to cancel. This does not claim a running executor was interrupted.';
COMMENT ON COLUMN app.job.result_summary IS
    'Small JSON summary only; result bytes remain first-class artifacts.';

INSERT INTO meta.migration (filename, sha256, content_sha256, hash_source)
VALUES ('014_job_store_contract.sql',
        '\xcf7999ac21a5392f6ddcc12addeb654768133083ea8b8db96661ccebda0400d9'::bytea,
        '\xcf7999ac21a5392f6ddcc12addeb654768133083ea8b8db96661ccebda0400d9'::bytea, 'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
