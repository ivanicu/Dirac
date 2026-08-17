-- 044 · Persisted RunSet state and its timestamps are one state machine fact.
--
-- Recovery must never interpret an active row as finished, a terminal row as
-- unfinished, or a cancellation state with no cancellation event.
BEGIN;

ALTER TABLE app.rbfe_run_set
    ADD CONSTRAINT rbfe_run_set_state_timestamps_check
    CHECK (
        (cancellation_requested_at IS NULL
         OR cancellation_requested_at >= created_at)
        AND (finished_at IS NULL OR finished_at >= created_at)
        AND (cancellation_requested_at IS NULL OR finished_at IS NULL
             OR finished_at >= cancellation_requested_at)
        AND (
            (state IN ('cancel_requested','blocked','cancelled')
             AND cancellation_requested_at IS NOT NULL)
            OR
            (state NOT IN ('cancel_requested','blocked','cancelled')
             AND cancellation_requested_at IS NULL)
        )
        AND (
            (state IN ('blocked','completed','cancelled')
             AND finished_at IS NOT NULL)
            OR
            (state NOT IN ('blocked','completed','cancelled')
             AND finished_at IS NULL)
        )
    );

COMMENT ON CONSTRAINT rbfe_run_set_state_timestamps_check ON app.rbfe_run_set IS
    'Cancellation and completion timestamps are bidirectional structural witnesses for the persisted RunSet state; terminal time cannot precede cancellation.';

INSERT INTO meta.migration (filename,sha256,content_sha256,hash_source)
VALUES ('044_rbfe_runset_state_integrity.sql','\x3809ecec49b1f1d80fb49a7d0c355a064a46a98624a205fc64faf524c58a3b68'::bytea,
        '\x3809ecec49b1f1d80fb49a7d0c355a064a46a98624a205fc64faf524c58a3b68'::bytea,'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
