-- 016 · generic method-version-aware result cache.
BEGIN;

CREATE TABLE app.result_cache (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    method_row_id uuid NOT NULL REFERENCES meta.method(id),
    request_digest bytea NOT NULL CHECK (octet_length(request_digest) = 32),
    result jsonb NOT NULL,
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    warnings jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(warnings) = 'array'),
    parameters_used jsonb NOT NULL DEFAULT '{}'::jsonb,
    source_job_id uuid REFERENCES app.job(id),
    compute_seconds numeric(10,3) CHECK (compute_seconds IS NULL OR compute_seconds >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (method_row_id, request_digest)
);

CREATE TABLE app.result_cache_artifact (
    result_cache_id uuid NOT NULL REFERENCES app.result_cache(id) ON DELETE CASCADE,
    artifact_id uuid NOT NULL REFERENCES app.artifact(id),
    role text NOT NULL CHECK (role <> ''),
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    PRIMARY KEY (result_cache_id, role, ordinal),
    UNIQUE (result_cache_id, artifact_id, role)
);

CREATE INDEX result_cache_lookup ON app.result_cache (method_row_id, request_digest);

-- Only rows produced by the current registered method are scientifically current.
-- Superseding source invalidates reads without deleting historical evidence.
CREATE VIEW app.v_result_cache_servable AS
SELECT c.*
  FROM app.result_cache c
  JOIN meta.method m ON m.id = c.method_row_id
 WHERE m.superseded_at IS NULL;

COMMENT ON TABLE app.result_cache IS
    'Small validated results keyed by the complete invocation digest and immutable method row. Artifact bytes remain in app.blob/app.artifact.';
COMMENT ON VIEW app.v_result_cache_servable IS
    'The only generic result-cache read surface. Method supersession invalidates stale rows without erasing provenance.';

INSERT INTO meta.migration (filename, sha256, content_sha256, hash_source)
VALUES ('016_result_cache.sql', '\xcd9f4c0c3c7d6b6b21cee96962c4e80df452ef5f01a114d712f05f3bc90416f7'::bytea,
        '\xcd9f4c0c3c7d6b6b21cee96962c4e80df452ef5f01a114d712f05f3bc90416f7'::bytea, 'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
