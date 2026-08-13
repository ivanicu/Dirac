-- 025 · Program is the durable aggregate root for the discovery decision loop.
--
-- The physical identity remains design.project because every existing campaign,
-- series, mission and Motif record already points at it.  This migration evolves
-- that identity in place and adds small, versioned scientific atoms around it.
BEGIN;

CREATE TYPE design.program_lifecycle AS ENUM (
    'draft', 'active', 'paused', 'completed', 'archived'
);

CREATE TYPE design.program_stage AS ENUM (
    'discovery', 'target_validation', 'hit_discovery', 'hit_to_lead',
    'lead_optimization', 'candidate_selection', 'preclinical'
);

ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'objective';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'milestone';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'program_snapshot';

DROP VIEW design.program;

ALTER TABLE design.project
    ADD COLUMN lifecycle design.program_lifecycle NOT NULL DEFAULT 'active',
    ADD COLUMN stage design.program_stage NOT NULL DEFAULT 'discovery',
    ADD COLUMN version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
    ADD COLUMN summary text,
    ADD COLUMN indication text,
    ADD COLUMN modality text,
    ADD COLUMN owner_id text,
    ADD COLUMN created_at timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN archived_at timestamptz,
    ADD COLUMN updated_by_kind app.actor_kind,
    ADD COLUMN updated_by_id text,
    ADD CONSTRAINT program_archive_consistency CHECK (
        (lifecycle = 'archived') = (archived_at IS NOT NULL)
    ),
    ADD CONSTRAINT program_actor_consistency CHECK (
        (updated_by_kind IS NULL) = (updated_by_id IS NULL)
    );

COMMENT ON TABLE design.project IS
    'Durable Program aggregate root. Project is retained as the physical table name '
    'so existing scientific foreign keys keep one identity.';

CREATE VIEW design.program AS
SELECT id, code, name, target_id, lifecycle, stage, version, summary, indication,
       modality, owner_id, started_on, closed_on, created_at, updated_at, archived_at,
       updated_by_kind, updated_by_id
  FROM design.project;

CREATE TABLE design.program_objective (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    program_id uuid NOT NULL REFERENCES design.project(id) ON DELETE RESTRICT,
    objective_key citext NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    title text NOT NULL CHECK (btrim(title) <> ''),
    rationale text NOT NULL CHECK (btrim(rationale) <> ''),
    category text NOT NULL CHECK (category IN (
        'efficacy','selectivity','developability','safety','synthesis','evidence'
    )),
    metric text,
    direction text CHECK (direction IS NULL OR direction IN (
        'maximize','minimize','at_least','at_most','within','qualitative'
    )),
    threshold jsonb NOT NULL DEFAULT '{}'::jsonb,
    priority integer NOT NULL DEFAULT 3 CHECK (priority BETWEEN 1 AND 5),
    hardness text NOT NULL DEFAULT 'soft' CHECK (hardness IN ('hard','soft')),
    status text NOT NULL DEFAULT 'active' CHECK (status IN (
        'active','met','missed','superseded','retired'
    )),
    supersedes_id uuid REFERENCES design.program_objective(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by_kind app.actor_kind NOT NULL,
    created_by_id text NOT NULL,
    UNIQUE (program_id, objective_key, revision),
    CHECK (supersedes_id IS NULL OR supersedes_id <> id),
    CHECK ((metric IS NULL) = (direction IS NULL)),
    CHECK (jsonb_typeof(threshold) = 'object')
);
CREATE UNIQUE INDEX program_objective_one_active
    ON design.program_objective (program_id, objective_key)
    WHERE status = 'active';
CREATE INDEX program_objective_program
    ON design.program_objective (program_id, created_at DESC);

ALTER TABLE design.hypothesis
    ADD COLUMN hypothesis_key citext,
    ADD COLUMN revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    ADD COLUMN title text,
    ADD COLUMN falsification_criterion text,
    ADD COLUMN supersedes_id uuid REFERENCES design.hypothesis(id),
    ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now(),
    ADD CONSTRAINT hypothesis_supersedes_other CHECK (
        supersedes_id IS NULL OR supersedes_id <> id
    );
CREATE UNIQUE INDEX hypothesis_revision_identity
    ON design.hypothesis (program_id, hypothesis_key, revision)
    WHERE hypothesis_key IS NOT NULL;
CREATE UNIQUE INDEX hypothesis_one_active
    ON design.hypothesis (program_id, hypothesis_key)
    WHERE hypothesis_key IS NOT NULL AND status = 'active';

ALTER TABLE design.decision
    ADD COLUMN decision_key citext,
    ADD COLUMN revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    ADD COLUMN decision_type text CHECK (decision_type IS NULL OR decision_type IN (
        'scope','scientific','portfolio','stage_gate','resource','risk'
    )),
    ADD COLUMN outcome text,
    ADD COLUMN alternatives jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN supersedes_id uuid REFERENCES design.decision(id),
    ADD CONSTRAINT decision_supersedes_other CHECK (
        supersedes_id IS NULL OR supersedes_id <> id
    ),
    ADD CONSTRAINT decision_alternatives_array CHECK (
        jsonb_typeof(alternatives) = 'array'
    );
CREATE UNIQUE INDEX decision_revision_identity
    ON design.decision (program_id, decision_key, revision)
    WHERE decision_key IS NOT NULL;

CREATE TABLE design.program_milestone (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    program_id uuid NOT NULL REFERENCES design.project(id) ON DELETE RESTRICT,
    milestone_key citext NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    title text NOT NULL CHECK (btrim(title) <> ''),
    description text,
    target_date date,
    criteria jsonb NOT NULL CHECK (jsonb_typeof(criteria) = 'array'),
    status text NOT NULL DEFAULT 'planned' CHECK (status IN (
        'planned','on_track','at_risk','achieved','missed','superseded','retired'
    )),
    supersedes_id uuid REFERENCES design.program_milestone(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by_kind app.actor_kind NOT NULL,
    created_by_id text NOT NULL,
    UNIQUE (program_id, milestone_key, revision),
    CHECK (supersedes_id IS NULL OR supersedes_id <> id)
);
CREATE UNIQUE INDEX program_milestone_one_current
    ON design.program_milestone (program_id, milestone_key)
    WHERE status IN ('planned','on_track','at_risk');

CREATE TABLE design.program_object_link (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    program_id uuid NOT NULL REFERENCES design.project(id) ON DELETE RESTRICT,
    object_kind app.object_kind NOT NULL,
    object_id text NOT NULL CHECK (btrim(object_id) <> ''),
    role text NOT NULL CHECK (btrim(role) <> ''),
    rationale text,
    linked_at timestamptz NOT NULL DEFAULT now(),
    linked_by_kind app.actor_kind NOT NULL,
    linked_by_id text NOT NULL,
    retired_at timestamptz,
    CHECK (object_kind <> 'program'),
    CHECK (retired_at IS NULL OR retired_at >= linked_at)
);
CREATE UNIQUE INDEX program_object_link_current
    ON design.program_object_link (program_id, object_kind, object_id, role)
    WHERE retired_at IS NULL;

CREATE TABLE design.program_event (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    program_id uuid NOT NULL REFERENCES design.project(id) ON DELETE RESTRICT,
    aggregate_version bigint NOT NULL CHECK (aggregate_version > 0),
    event_kind text NOT NULL CHECK (event_kind IN (
        'program.created','program.updated','objective.recorded',
        'hypothesis.recorded','decision.recorded','milestone.recorded',
        'object.linked','snapshot.created'
    )),
    atom_kind app.object_kind,
    atom_id text,
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    request_id text,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    actor_kind app.actor_kind NOT NULL,
    actor_id text NOT NULL,
    UNIQUE (program_id, aggregate_version),
    CHECK ((atom_kind IS NULL) = (atom_id IS NULL))
);
CREATE UNIQUE INDEX program_event_request_id
    ON design.program_event (program_id, request_id)
    WHERE request_id IS NOT NULL;
CREATE INDEX program_event_timeline
    ON design.program_event (program_id, occurred_at DESC);

CREATE TABLE design.program_snapshot (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    program_id uuid NOT NULL REFERENCES design.project(id) ON DELETE RESTRICT,
    aggregate_version bigint NOT NULL CHECK (aggregate_version > 0),
    document jsonb NOT NULL CHECK (jsonb_typeof(document) = 'object'),
    digest bytea NOT NULL CHECK (octet_length(digest) = 32),
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by_kind app.actor_kind NOT NULL,
    created_by_id text NOT NULL,
    UNIQUE (program_id, aggregate_version),
    UNIQUE (digest)
);

COMMENT ON TABLE design.program_objective IS
    'Versioned Program objective. Executable campaign objective_spec remains a frozen downstream Design Brief.';
COMMENT ON TABLE design.hypothesis IS
    'Testable Program hypothesis; a changed claim is a superseding revision, not an overwrite.';
COMMENT ON TABLE design.decision IS
    'Append-only human or agent decision with rationale and considered alternatives.';
COMMENT ON TABLE design.program_milestone IS
    'Evidence-bearing stage gate or delivery milestone with explicit criteria.';
COMMENT ON TABLE design.program_event IS
    'Ordered audit journal for Program mutations; relational tables remain the query source of truth.';
COMMENT ON TABLE design.program_snapshot IS
    'Immutable, content-digested Program context handed to downstream Design, Compute and review runs.';

INSERT INTO meta.migration (filename, sha256, content_sha256, hash_source)
VALUES ('025_program_aggregate.sql', '\x986db9b8cfb6e93461be9c6036dbd363a58407dd960afc7c648d5a5131a5c8a0'::bytea,
        '\x986db9b8cfb6e93461be9c6036dbd363a58407dd960afc7c648d5a5131a5c8a0'::bytea, 'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
