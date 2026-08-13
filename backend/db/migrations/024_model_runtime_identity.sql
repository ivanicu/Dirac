-- 024 · truthful model runtime identity for local and container execution.
--
-- The first Motif schema required container_image_digest for every release.  Local
-- appliance baselines do not run in a container, so populating that field would invent
-- provenance.  A release now identifies either a digest-pinned container OR an
-- immutable installed-distribution runtime manifest Artifact.
BEGIN;

ALTER TABLE meta.model_release
    ALTER COLUMN container_image_digest DROP NOT NULL,
    ADD COLUMN runtime_kind text NOT NULL DEFAULT 'container'
        CHECK (runtime_kind IN ('container','local_env')),
    ADD COLUMN runtime_lock_artifact_id uuid REFERENCES app.artifact(id),
    ADD CONSTRAINT model_release_runtime_identity CHECK (
        (runtime_kind = 'container'
         AND container_image_digest ~ '^sha256:[0-9a-f]{64}$')
        OR
        (runtime_kind = 'local_env'
         AND runtime_lock_artifact_id IS NOT NULL
         AND container_image_digest IS NULL)
    );

COMMENT ON COLUMN meta.model_release.runtime_kind IS
    'container requires a pinned OCI digest; local_env requires an immutable runtime '
    'manifest Artifact. The two modes cannot impersonate one another.';
COMMENT ON COLUMN meta.model_release.runtime_lock_artifact_id IS
    'Canonical Python/platform/installed-distribution manifest for local execution; '
    'the lockfile_digest column is the SHA-256 of this Artifact payload.';

INSERT INTO meta.migration (filename, sha256, content_sha256, hash_source)
VALUES ('024_model_runtime_identity.sql', '\xf25ff1d7e8775942938768bbbd289e6886a35ac86fa8945d5e5a1891c6496973'::bytea,
        '\xf25ff1d7e8775942938768bbbd289e6886a35ac86fa8945d5e5a1891c6496973'::bytea, 'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
