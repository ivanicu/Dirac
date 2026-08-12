-- 013 · artifacts become first-class objects.
--
-- WHY, and the driver is not tidiness: today a Gaussian cube is a JSON string field
-- and the physics daemon base64-encodes point arrays into JSON. That survives a
-- browser demo and fails every other consumer.
--
--   · an MCP tool result carrying 2 MB of base64 spends the conversation's context
--     on bytes no model will read
--   · a CLI cannot stream, range-request or VERIFY what it cannot address
--   · app.job has a method-specific field_cube_id, and the road from there is
--     docking_result_id, fep_result_id, md_trajectory_id — one column per result
--     kind, forever
--
-- The substrate already exists and is already honest: app.blob enforces
-- digest(bytes,'sha256') = sha256, so the store cannot hold a mislabelled blob.
-- What was missing is a NAME for a blob in a ROLE — "this blob is the field.cube of
-- that job" — which is what makes a reference possible in place of a payload.
--
-- SEPARATION OF CONCERNS, stated because the two tables look redundant:
--   app.blob      bytes, addressed by content. Deduplicated by nature: two jobs that
--                 produce byte-identical cubes share one blob.
--   app.artifact  a blob IN A ROLE, with a media type and its own metadata. Two
--                 artifacts may point at one blob — the same cube can be the
--                 `field.cube` of one job and the `reference.cube` of a comparison.
--   app.job_artifact  which job produced which artifact, in which role, in what
--                 order. A job may emit several (a cube AND the molfile it used).
--
-- field_cube_id on app.job STAYS as a compatibility and query-optimisation column.
-- It is not deleted and it is not the pattern.

BEGIN;

CREATE TABLE IF NOT EXISTS app.artifact (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    blob_sha256  bytea NOT NULL REFERENCES app.blob(sha256),
    media_type   text  NOT NULL CHECK (media_type <> '' AND media_type LIKE '%/%'),
    role         text  NOT NULL CHECK (role <> ''),
    size_bytes   bigint NOT NULL CHECK (size_bytes >= 0),
    encoding     text  NOT NULL DEFAULT 'identity'
                 CHECK (encoding IN ('identity', 'gzip', 'zstd')),
    metadata     jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_by_method uuid REFERENCES meta.method(id),
    created_at   timestamptz NOT NULL DEFAULT now(),

    -- The same blob in the same role with the same encoding is ONE artifact. Without
    -- this, every cache hit that re-registered its cube would mint a new id and the
    -- "content-addressed" claim would quietly become "content-addressed plus a
    -- growing pile of aliases".
    CONSTRAINT artifact_role_identity UNIQUE (blob_sha256, role, encoding)
);

COMMENT ON TABLE app.artifact IS
    'A blob in a ROLE. app.blob owns the bytes and their digest; this owns what the '
    'bytes ARE to somebody. Two artifacts may share one blob, which is why the '
    'identity is (blob, role, encoding) and not the blob alone.';

COMMENT ON COLUMN app.artifact.size_bytes IS
    'The DECODED size. Stored rather than derived so a client can decide whether to '
    'ask for the bytes before it asks — the whole point of a reference is that the '
    'decision precedes the transfer.';

CREATE INDEX IF NOT EXISTS artifact_role_idx ON app.artifact (role, created_at DESC);

CREATE TABLE IF NOT EXISTS app.job_artifact (
    job_id      uuid NOT NULL REFERENCES app.job(id) ON DELETE CASCADE,
    artifact_id uuid NOT NULL REFERENCES app.artifact(id),
    role        text NOT NULL CHECK (role <> ''),
    ordinal     integer NOT NULL DEFAULT 0 CHECK (ordinal >= 0),
    PRIMARY KEY (job_id, artifact_id, role)
);

COMMENT ON TABLE app.job_artifact IS
    'Which job produced which artifact, in which role. ON DELETE CASCADE on the job '
    'side only: reaping a job row must not delete bytes another job may reference, '
    'and app.blob is swept by bin/dirac-sweep by hand, never automatically.';

-- What an operator and the ops console read. Never the base tables: the join and
-- the human-readable size live in one place.
CREATE OR REPLACE VIEW app.v_artifact AS
SELECT a.id,
       encode(a.blob_sha256, 'hex')                       AS sha256,
       a.role, a.media_type, a.size_bytes, a.encoding,
       pg_size_pretty(a.size_bytes)                       AS size_pretty,
       m.method_id, m.version                             AS method_version,
       a.metadata, a.created_at,
       (SELECT count(*) FROM app.job_artifact ja WHERE ja.artifact_id = a.id)
                                                          AS referencing_jobs
  FROM app.artifact a
  LEFT JOIN meta.method m ON m.id = a.created_by_method;

COMMENT ON VIEW app.v_artifact IS
    'referencing_jobs = 0 is not garbage: an artifact may be registered before its '
    'job row exists, and the sweep is manual. It is the number to look at before '
    'deciding anything is unreferenced.';

INSERT INTO meta.migration (filename, sha256, content_sha256, hash_source)
VALUES ('013_artifacts_first_class.sql',
        '\x49d467823bb234d962468a82f721d394d5e74f7a953c84ed307b2b4a4a3e2a4f'::bytea,
        '\x49d467823bb234d962468a82f721d394d5e74f7a953c84ed307b2b4a4a3e2a4f'::bytea, 'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
