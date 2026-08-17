-- 046 · A client command request key owns one durable Job for all time.
--
-- In-flight request-digest deduplication cannot recover an ACK-lost Job after it
-- reaches done/failed/cancelled, and it cannot distinguish replay from reusing one
-- key for changed bytes.  The command identity is therefore durable and unique
-- across every state; input_sha256 is the canonical payload digest compared by the
-- JobStore before an existing Job may be returned.
BEGIN;

ALTER TABLE app.job
    ADD COLUMN request_key text,
    ADD CONSTRAINT job_request_key_nonempty CHECK (
        request_key IS NULL OR request_key ~ (
            '[^[:space:]' || U&'\00A0\2007\202F\FEFF' || ']')),
    ADD CONSTRAINT job_request_key_length CHECK (
        request_key IS NULL OR length(request_key) <= 256),
    ADD CONSTRAINT job_request_key_has_command CHECK (
        request_key IS NULL OR command_id IS NOT NULL);

CREATE UNIQUE INDEX job_command_request_key_once
    ON app.job (actor_kind, actor_id, command_id, request_key)
 WHERE request_key IS NOT NULL;

-- A key-owned command uses the all-state index above.  Keeping it inside the
-- legacy scientific in-flight index would let another command/key collide before
-- its own durable identity was recorded, so exact-key Jobs are excluded here.
DROP INDEX app.job_one_inflight;
CREATE UNIQUE INDEX job_one_inflight
    ON app.job (actor_kind, actor_id, method_row_id, request_digest)
 WHERE request_key IS NULL AND state IN ('queued', 'running');

COMMENT ON COLUMN app.job.request_key IS
    'Client-owned command idempotency key. Unique with actor_kind, actor_id and '
    'command_id across queued, running and every terminal state.';
COMMENT ON COLUMN app.job.input_sha256 IS
    'For Invocation Jobs, SHA-256 of finite canonical JSON payload bytes. It is '
    'the immutable payload witness checked before a request_key replay returns '
    'the existing Job.';
COMMENT ON INDEX app.job_command_request_key_once IS
    'Exactly one durable Job per authenticated actor, command and client request '
    'key, including after success, refusal, failure or cancellation.';
COMMENT ON INDEX app.job_one_inflight IS
    'At most one identical non-keyed invocation is in flight per actor. '
    'Key-owned command Jobs are excluded because job_command_request_key_once '
    'owns their identity across all states.';

INSERT INTO meta.migration (filename,sha256,content_sha256,hash_source)
VALUES ('046_job_command_request_key.sql','\x9c07c7f892b58457410eceb5c939fb609998982fbcf7a0dff1c562e82c8247b7'::bytea,
        '\x9c07c7f892b58457410eceb5c939fb609998982fbcf7a0dff1c562e82c8247b7'::bytea,'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
