-- 040 · Durable, versioned FEP campaign state and explicit cross-campaign imports.
BEGIN;

CREATE TABLE app.rbfe_campaign (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version bigint NOT NULL DEFAULT 1 CHECK (version >= 1),
    status text NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','inputs_reviewed','prepared','poses_reviewed',
                          'planned','stale','archived')),
    state jsonb NOT NULL
        CHECK (jsonb_typeof(state) = 'object'),
    state_digest bytea NOT NULL CHECK (octet_length(state_digest) = 32),
    scientific_generation bigint NOT NULL DEFAULT 1
        CHECK (scientific_generation >= 1),
    scientific_digest bytea NOT NULL CHECK (octet_length(scientific_digest) = 32),
    invalidated_at timestamptz,
    invalidation_reason text,
    created_by_kind app.actor_kind NOT NULL,
    created_by_id text NOT NULL CHECK (btrim(created_by_id) <> ''),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((invalidated_at IS NULL) = (invalidation_reason IS NULL)),
    CHECK (state ? 'scientific_generation'
           AND jsonb_typeof(state->'scientific_generation') = 'number'
           AND (state->>'scientific_generation')::bigint = scientific_generation),
    CHECK (state ? 'scientific_digest'
           AND jsonb_typeof(state->'scientific_digest') = 'string'
           AND state->>'scientific_digest' =
               'sha256:' || encode(scientific_digest,'hex')),
    CHECK (updated_at >= created_at)
);

CREATE INDEX rbfe_campaign_updated_idx
    ON app.rbfe_campaign(updated_at DESC);
CREATE INDEX rbfe_campaign_owner_updated_idx
    ON app.rbfe_campaign(created_by_kind,created_by_id,updated_at DESC);
CREATE INDEX rbfe_campaign_live_idx
    ON app.rbfe_campaign(status,updated_at DESC)
    WHERE invalidated_at IS NULL AND status <> 'archived';

CREATE TABLE app.rbfe_campaign_revision (
    campaign_id uuid NOT NULL REFERENCES app.rbfe_campaign(id) ON DELETE RESTRICT,
    version bigint NOT NULL CHECK (version >= 1),
    status text NOT NULL
        CHECK (status IN ('draft','inputs_reviewed','prepared','poses_reviewed',
                          'planned','stale','archived')),
    state jsonb NOT NULL CHECK (jsonb_typeof(state) = 'object'),
    state_digest bytea NOT NULL CHECK (octet_length(state_digest) = 32),
    scientific_generation bigint NOT NULL CHECK (scientific_generation >= 1),
    scientific_digest bytea NOT NULL CHECK (octet_length(scientific_digest) = 32),
    changed_domains text[] NOT NULL DEFAULT ARRAY[]::text[]
        CHECK (array_position(changed_domains,NULL) IS NULL)
        CHECK (changed_domains <@ ARRAY[
            'project_context','receptor','source','reference',
            'canonical_ligands','ligand_identity','microstates','ligand_policy',
            'prep_policy','prepared_receptor','poses','pose_review','network',
            'protocol','execution','system_build','system_import',
            'campaign_metadata'
        ]::text[]),
    reason text NOT NULL CHECK (btrim(reason) <> ''),
    actor_kind app.actor_kind NOT NULL,
    actor_id text NOT NULL CHECK (btrim(actor_id) <> ''),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (state ? 'scientific_generation'
           AND jsonb_typeof(state->'scientific_generation') = 'number'
           AND (state->>'scientific_generation')::bigint = scientific_generation),
    CHECK (state ? 'scientific_digest'
           AND jsonb_typeof(state->'scientific_digest') = 'string'
           AND state->>'scientific_digest' =
               'sha256:' || encode(scientific_digest,'hex')),
    PRIMARY KEY (campaign_id,version)
);

CREATE TABLE app.rbfe_campaign_system_import (
    campaign_id uuid NOT NULL REFERENCES app.rbfe_campaign(id) ON DELETE RESTRICT,
    prepared_receptor_state_id uuid NOT NULL
        REFERENCES design.motif_scientific_object(id) ON DELETE RESTRICT,
    source_campaign_id uuid REFERENCES app.rbfe_campaign(id) ON DELETE RESTRICT,
    receipt jsonb NOT NULL CHECK (jsonb_typeof(receipt) = 'object'),
    receipt_digest bytea NOT NULL CHECK (octet_length(receipt_digest) = 32),
    actor_kind app.actor_kind NOT NULL,
    actor_id text NOT NULL CHECK (btrim(actor_id) <> ''),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (campaign_id,prepared_receptor_state_id),
    CHECK (source_campaign_id IS NULL OR source_campaign_id <> campaign_id)
);

COMMENT ON TABLE app.rbfe_campaign IS
    'Server-owned FEP campaign aggregate. Browser state is a projection of one versioned row.';
COMMENT ON TABLE app.rbfe_campaign_revision IS
    'Immutable campaign revision ledger. version/state_digest audit every save; scientific_generation/scientific_digest advance only for scientific transitions.';
COMMENT ON TABLE app.rbfe_campaign_system_import IS
    'Explicit provenance receipt required before one campaign may consume another campaign prepared system.';

INSERT INTO meta.migration (filename,sha256,content_sha256,hash_source)
VALUES ('040_rbfe_campaign_state.sql','\x15e19db6026db5d39dcafc2d406b0ea66b9de62f48bc7387c974028038d267fc'::bytea,
        '\x15e19db6026db5d39dcafc2d406b0ea66b9de62f48bc7387c974028038d267fc'::bytea,'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
