-- 033 · Give canonical Program Work Items an explicit, enforceable schedule.
-- A Gantt view must read planned dates; it must not infer them from creation time.

BEGIN;

ALTER TABLE design.program_work_package
    ADD COLUMN start_on date,
    ADD CONSTRAINT program_work_package_schedule_order
        CHECK (start_on IS NULL OR due_on IS NULL OR start_on <= due_on);

INSERT INTO meta.migration (filename,sha256,content_sha256,hash_source)
VALUES ('033_program_work_schedule.sql','\xcb01e7590b5abf64824767f8b5cc1f0bc0cb7bf174a91785dc2421d421c852cd'::bytea,
        '\xcb01e7590b5abf64824767f8b5cc1f0bc0cb7bf174a91785dc2421d421c852cd'::bytea,'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
