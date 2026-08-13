-- 035 · Make blocked Motif closed loops explicitly and durably retryable.

BEGIN;

ALTER TABLE design.motif_closed_loop_run
    ADD COLUMN stage_attempts jsonb NOT NULL DEFAULT '{}'::jsonb;

INSERT INTO meta.migration (filename,sha256,content_sha256,hash_source)
VALUES ('035_motif_closed_loop_retry.sql','\x01da2d20120ea2601c05090bad6d2371a520aca71d8beca06eaa9bbb422e76a0'::bytea,
        '\x01da2d20120ea2601c05090bad6d2371a520aca71d8beca06eaa9bbb422e76a0'::bytea,'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
