-- Durable completion belongs to the versioned Work Package, not to a browser-only Gantt bar.
BEGIN;

ALTER TABLE design.program_work_package
    ADD COLUMN IF NOT EXISTS progress_percent smallint NOT NULL DEFAULT 0
        CHECK (progress_percent BETWEEN 0 AND 100);

UPDATE design.program_work_package
SET progress_percent = 100
WHERE status = 'done' AND progress_percent = 0;

INSERT INTO meta.migration (filename,sha256,content_sha256,hash_source)
VALUES ('038_program_work_progress.sql','\x959ad41614dc53d910a9739cb803b00832e94870d043e1675f0c074886982483'::bytea,
        '\x959ad41614dc53d910a9739cb803b00832e94870d043e1675f0c074886982483'::bytea,'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
