-- 045 · RBFE preparation artifacts have explicit campaign ownership.
--
-- Content-addressed metadata records the first writer's context; it cannot model
-- the many-to-many fact that byte-identical receptor or pose artifacts can be reused
-- by several campaigns.  Job provenance already has app.job_artifact.  This table is
-- its campaign-side peer, and both links are written in the same transaction as the
-- campaign CAS that publishes preparation.
BEGIN;

-- Role is part of Artifact identity.  A campaign link that can relabel an
-- Artifact would make provenance disagree with the bytes it authorizes.  The
-- composite key lets PostgreSQL enforce that both columns name the same fact.
ALTER TABLE app.artifact
    ADD CONSTRAINT artifact_id_role_key UNIQUE (id, role);

CREATE TABLE app.rbfe_campaign_artifact (
    campaign_id uuid NOT NULL
        REFERENCES app.rbfe_campaign(id) ON DELETE RESTRICT,
    artifact_id uuid NOT NULL,
    role text NOT NULL CHECK (btrim(role) <> ''),
    ordinal integer NOT NULL DEFAULT 0 CHECK (ordinal >= 0),
    linked_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (campaign_id, artifact_id, role),
    CONSTRAINT rbfe_campaign_artifact_role_fk
        FOREIGN KEY (artifact_id, role)
        REFERENCES app.artifact(id, role) ON DELETE RESTRICT
);

CREATE INDEX rbfe_campaign_artifact_artifact_idx
    ON app.rbfe_campaign_artifact(artifact_id, campaign_id);

-- Preserve ownership for any campaign artifacts published before this relation
-- existed.  Compare text-to-text so malformed legacy metadata cannot abort the
-- migration with a UUID cast error.
INSERT INTO app.rbfe_campaign_artifact (campaign_id,artifact_id,role,ordinal)
SELECT c.id,a.id,a.role,0
FROM app.artifact a
JOIN app.rbfe_campaign c ON c.id::text=a.metadata->>'campaign_id'
ON CONFLICT DO NOTHING;

COMMENT ON TABLE app.rbfe_campaign_artifact IS
    'Explicit many-to-many ownership of immutable RBFE preparation artifacts. '
    'Rows are committed atomically with the campaign preparation CAS; Job lineage '
    'is recorded independently in app.job_artifact.';

INSERT INTO meta.migration (filename,sha256,content_sha256,hash_source)
VALUES ('045_rbfe_campaign_artifact_ownership.sql','\xab225ad936749af678fc970f00161157837d739e250bd9de0d0022b7ab1f54af'::bytea,
        '\xab225ad936749af678fc970f00161157837d739e250bd9de0d0022b7ab1f54af'::bytea,'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
