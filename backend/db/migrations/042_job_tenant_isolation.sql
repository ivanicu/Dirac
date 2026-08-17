-- 042 · Durable Job ownership is part of its in-flight identity.
--
-- request_digest identifies the scientific invocation, not who is allowed to
-- observe or control it.  The previous unique key therefore made actor B collide
-- with actor A's private Job, and the conflict lookup could disclose A's Job id.
-- Keep deduplication, but keep it inside the authenticated principal boundary.
BEGIN;

DROP INDEX app.job_one_inflight;
CREATE UNIQUE INDEX job_one_inflight
    ON app.job (actor_kind, actor_id, method_row_id, request_digest)
 WHERE state IN ('queued', 'running');

COMMENT ON INDEX app.job_one_inflight IS
    'At most one identical in-flight invocation per authenticated actor. '
    'Cross-actor work never joins and never discloses another actor Job id.';

COMMENT ON COLUMN app.job.request_digest IS
    'Canonical SHA-256 of the complete scientific invocation identity. '
    'In-flight deduplication additionally includes the authenticated actor pair.';

INSERT INTO meta.migration (filename, sha256, content_sha256, hash_source)
VALUES ('042_job_tenant_isolation.sql', '\xa36256448a270a8b67a4b1afacde17bc6b0467ea30543cbe31093e4baf2a9298'::bytea,
        '\xa36256448a270a8b67a4b1afacde17bc6b0467ea30543cbe31093e4baf2a9298'::bytea, 'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
