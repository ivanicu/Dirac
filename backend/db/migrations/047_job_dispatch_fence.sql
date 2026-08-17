-- 047 · A durable client-key Job owns a recoverable, fenced local dispatch.
--
-- The Job row and this outbox row are committed together.  A process crash after
-- Job admission but before ThreadExecutor.submit therefore leaves durable work
-- that a replay or startup drain can claim.  Every claim increments a fencing
-- token; stale workers cannot start, publish campaign science, or commit terminal
-- Job state.  The payload is deleted with the dispatch row at terminal state so
-- multi-megabyte receptor inputs do not accumulate forever.
BEGIN;

CREATE TYPE app.job_dispatch_state AS ENUM (
    'pending', 'claimed', 'running');

CREATE TABLE app.job_dispatch (
    job_id uuid PRIMARY KEY REFERENCES app.job(id) ON DELETE CASCADE,
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    payload_sha256 bytea NOT NULL CHECK (octet_length(payload_sha256) = 32),
    execution_digest bytea NOT NULL CHECK (octet_length(execution_digest) = 32),
    execution_adapter app.execution_backend NOT NULL,
    state app.job_dispatch_state NOT NULL DEFAULT 'pending',
    fencing_token bigint NOT NULL DEFAULT 0 CHECK (fencing_token >= 0),
    lease_owner text CHECK (
        lease_owner IS NULL OR lease_owner ~
        ('[^[:space:]' || U&'\00A0\2007\202F\FEFF' || ']')),
    claimed_at timestamptz,
    lease_expires_at timestamptz,
    heartbeat_at timestamptz,
    submitted_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT job_dispatch_local_recovery CHECK (
        execution_adapter = 'local_cpu'),
    CONSTRAINT job_dispatch_claim_shape CHECK (
        (state = 'pending' AND fencing_token = 0
         AND lease_owner IS NULL AND claimed_at IS NULL
         AND lease_expires_at IS NULL AND heartbeat_at IS NULL
         AND submitted_at IS NULL)
        OR
        (state IN ('claimed','running') AND fencing_token > 0
         AND lease_owner IS NOT NULL AND claimed_at IS NOT NULL
         AND lease_expires_at IS NOT NULL AND heartbeat_at IS NOT NULL
         AND (state <> 'running' OR submitted_at IS NOT NULL))),
    CONSTRAINT job_dispatch_lease_order CHECK (
        lease_expires_at IS NULL OR (
            created_at <= claimed_at
            AND claimed_at <= heartbeat_at
            AND claimed_at < lease_expires_at
            AND heartbeat_at <= lease_expires_at)),
    CONSTRAINT job_dispatch_submitted_claimed CHECK (
        submitted_at IS NULL OR (
            claimed_at <= submitted_at
            AND submitted_at <= lease_expires_at))
);

CREATE FUNCTION app.enforce_job_dispatch_fence()
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
          AND j.command_id = 'physics.rbfe-campaign.prepare'
          AND j.input_sha256 = NEW.payload_sha256
          AND j.request_digest = NEW.execution_digest
          AND j.state IN ('queued','running')
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            CONSTRAINT = 'job_dispatch_requires_keyed_active_job',
            MESSAGE = 'job_dispatch requires an active client-key Job';
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

CREATE TRIGGER job_dispatch_fence_guard
BEFORE INSERT OR UPDATE OR DELETE ON app.job_dispatch
FOR EACH ROW EXECUTE FUNCTION app.enforce_job_dispatch_fence();

CREATE FUNCTION app.enforce_job_dispatch_source_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF (NEW.request_key IS DISTINCT FROM OLD.request_key
        OR NEW.command_id IS DISTINCT FROM OLD.command_id
        OR NEW.input_sha256 IS DISTINCT FROM OLD.input_sha256
        OR NEW.request_digest IS DISTINCT FROM OLD.request_digest)
       AND EXISTS (SELECT 1 FROM app.job_dispatch d WHERE d.job_id = OLD.id) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            CONSTRAINT = 'job_dispatch_source_immutable',
            MESSAGE = 'a Job with an active dispatch may not change its admitted witness';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER job_dispatch_source_guard
BEFORE UPDATE OF request_key,command_id,input_sha256,request_digest ON app.job
FOR EACH ROW EXECUTE FUNCTION app.enforce_job_dispatch_source_immutable();

CREATE FUNCTION app.cleanup_terminal_job_dispatch()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.state IN ('done','failed','cancelled')
       AND OLD.state IS DISTINCT FROM NEW.state THEN
        DELETE FROM app.job_dispatch WHERE job_id = NEW.id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER job_dispatch_terminal_cleanup
AFTER UPDATE OF state ON app.job
FOR EACH ROW EXECUTE FUNCTION app.cleanup_terminal_job_dispatch();

CREATE INDEX job_dispatch_recovery_idx
    ON app.job_dispatch (state, lease_expires_at, created_at);

COMMENT ON TABLE app.job_dispatch IS
    'Transactional dispatch outbox for client-key local CPU Jobs. Payload bytes '
    'exist only until terminal completion; fencing_token rejects stale workers.';
COMMENT ON COLUMN app.job_dispatch.execution_digest IS
    'The immutable admitted ExecutionIdentity cache digest; recovery must match it.';
COMMENT ON COLUMN app.job_dispatch.fencing_token IS
    'Monotone per Job. Scientific publication and terminal Job writes require the '
    'current live token and owner.';
COMMENT ON FUNCTION app.enforce_job_dispatch_fence() IS
    'Rejects non-prepare/non-keyed witnesses, mutation, active deletion, and token rollback.';
COMMENT ON FUNCTION app.enforce_job_dispatch_source_immutable() IS
    'Prevents mutation of the owning Job witness while its recoverable dispatch is active.';
COMMENT ON FUNCTION app.cleanup_terminal_job_dispatch() IS
    'Deletes recoverable payload bytes in the same transaction that makes a Job terminal.';

INSERT INTO meta.migration (filename,sha256,content_sha256,hash_source)
VALUES ('047_job_dispatch_fence.sql','\x839ac9b175d5b57b4ae0273a14a6cf691c1c45dcc028a6c59136083b6cd47563'::bytea,
        '\x839ac9b175d5b57b4ae0273a14a6cf691c1c45dcc028a6c59136083b6cd47563'::bytea,'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
