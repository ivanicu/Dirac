-- 041 · A cancellation request is not evidence that running physics stopped.
BEGIN;

ALTER TABLE app.rbfe_run_set
    ADD COLUMN cancellation_requested_at timestamptz;

ALTER TABLE app.rbfe_run_set
    DROP CONSTRAINT rbfe_run_set_state_check;
ALTER TABLE app.rbfe_run_set
    ADD CONSTRAINT rbfe_run_set_state_check
    CHECK (state IN ('pending','running','blocked','aggregating',
                     'cancel_requested','completed','cancelled'));

DROP INDEX app.rbfe_run_set_active_idx;
CREATE INDEX rbfe_run_set_active_idx ON app.rbfe_run_set(state,updated_at)
    WHERE state IN ('pending','running','aggregating','cancel_requested');

COMMENT ON COLUMN app.rbfe_run_set.cancellation_requested_at IS
    'The user requested cancellation. state=cancelled is set only after every active child Job is observed terminal.';

INSERT INTO meta.migration (filename,sha256,content_sha256,hash_source)
VALUES ('041_rbfe_runset_cancellation.sql','\x842b3cdaf91f1056f157099ae32567e8058a3c29ac8d95321538ebe2f41fc9e3'::bytea,
        '\x842b3cdaf91f1056f157099ae32567e8058a3c29ac8d95321538ebe2f41fc9e3'::bytea,'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
