-- 050 · Admit the research-loop reasoner into the durable local dispatch fence.
--
-- 047 deliberately admitted only physics.rbfe-campaign.prepare.  The research
-- loop submits ai.research.propose under research.loop.create with the same
-- client-key/outbox/fencing protocol; without this explicit second command the
-- loop reaches its first context snapshot and then blocks before provider I/O.
BEGIN;

CREATE OR REPLACE FUNCTION app.enforce_job_dispatch_fence()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF EXISTS (
            SELECT 1 FROM app.job j
            WHERE j.id = OLD.job_id AND j.state IN ('queued','running')
        ) THEN
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                CONSTRAINT = 'job_dispatch_active_delete_forbidden',
                MESSAGE = 'an active Job dispatch may only leave through terminal cleanup';
        END IF;
        RETURN OLD;
    END IF;
    IF TG_OP = 'INSERT' AND NOT EXISTS (
        SELECT 1
        FROM app.job j
        WHERE j.id = NEW.job_id
          AND j.request_key IS NOT NULL
          AND j.command_id IN (
              'physics.rbfe-campaign.prepare',
              'research.loop.create')
          AND j.input_sha256 = NEW.payload_sha256
          AND j.request_digest = NEW.execution_digest
          AND j.state IN ('queued','running')
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            CONSTRAINT = 'job_dispatch_requires_keyed_active_job',
            MESSAGE = 'job_dispatch requires an allow-listed active client-key Job';
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.fencing_token < OLD.fencing_token THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            CONSTRAINT = 'job_dispatch_token_monotone',
            MESSAGE = 'job_dispatch fencing_token may not decrease';
    END IF;
    IF TG_OP = 'UPDATE' AND (
        NEW.job_id IS DISTINCT FROM OLD.job_id
        OR NEW.payload IS DISTINCT FROM OLD.payload
        OR NEW.payload_sha256 IS DISTINCT FROM OLD.payload_sha256
        OR NEW.execution_digest IS DISTINCT FROM OLD.execution_digest
        OR NEW.execution_adapter IS DISTINCT FROM OLD.execution_adapter
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            CONSTRAINT = 'job_dispatch_witness_immutable',
            MESSAGE = 'job_dispatch admitted witness columns are immutable';
    END IF;
    IF TG_OP = 'UPDATE' AND (
        NEW.lease_owner IS DISTINCT FROM OLD.lease_owner
        OR NEW.claimed_at IS DISTINCT FROM OLD.claimed_at
        OR (OLD.state = 'running' AND NEW.state = 'claimed')
    ) AND NEW.fencing_token <= OLD.fencing_token THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            CONSTRAINT = 'job_dispatch_reclaim_requires_new_token',
            MESSAGE = 'dispatch ownership/reclaim changes require a newer token';
    END IF;
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION app.enforce_job_dispatch_fence() IS
    'Rejects non-allow-listed/non-keyed witnesses, mutation, active deletion, and token rollback.';

INSERT INTO meta.migration (filename,sha256,content_sha256,hash_source)
VALUES ('050_research_loop_dispatch.sql','\x5a923ab42cbd8a64f9117319e2823bdf38bd7ab2d305c544ea8ab90cb637c0b2'::bytea,
        '\x5a923ab42cbd8a64f9117319e2823bdf38bd7ab2d305c544ea8ab90cb637c0b2'::bytea,'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
