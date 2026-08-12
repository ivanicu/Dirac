-- 015 · durable domain graph and Mission / Run / Job separation.
BEGIN;

CREATE TYPE app.object_kind AS ENUM (
    'program','target','molecule','compound','series','protein','protein_structure',
    'complex','pose','conformer','field','prediction','campaign','synthesis_route',
    'reaction','building_block','assay','experiment','measurement','hypothesis',
    'claim','evidence','decision','dataset','model','artifact','mission','run','job',
    'literature_reference');

CREATE TYPE app.relation_kind AS ENUM (
    'derived_from','generated_by','used','measured_in','predicted_by','belongs_to',
    'member_of','supports','contradicts','supersedes','tests','explains',
    'selected_from','rejected_because','promoted_because','part_of','caused_by');

CREATE TYPE app.actor_kind AS ENUM ('human','agent','service');
CREATE TYPE app.mission_state AS ENUM ('draft','active','paused','completed','failed','cancelled');
CREATE TYPE app.run_state AS ENUM ('planned','active','waiting_approval','completed','failed','cancelled');

-- Existing project is the durable Program object. The semantic name is a view so no
-- duplicate identity table can drift from it.
CREATE VIEW design.program AS
SELECT id, code, name, target_id, status, started_on, closed_on
  FROM design.project;

CREATE TABLE design.campaign (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    program_id uuid NOT NULL REFERENCES design.project(id),
    name citext NOT NULL,
    objective text NOT NULL,
    status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('planned','active','paused','completed','cancelled')),
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by_kind app.actor_kind NOT NULL,
    created_by_id text NOT NULL,
    UNIQUE (program_id, name)
);

CREATE TABLE design.campaign_compound (
    campaign_id uuid NOT NULL REFERENCES design.campaign(id) ON DELETE CASCADE,
    compound_id uuid NOT NULL REFERENCES chem.compound(id),
    series_id uuid REFERENCES design.series(id),
    status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('proposed','active','promoted','rejected','archived')),
    added_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (campaign_id, compound_id)
);

CREATE TABLE design.hypothesis (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    program_id uuid NOT NULL REFERENCES design.project(id),
    statement text NOT NULL,
    confidence numeric(4,3) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active','supported','contradicted','superseded','closed')),
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by_kind app.actor_kind NOT NULL,
    created_by_id text NOT NULL
);

CREATE TABLE design.evidence (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    program_id uuid NOT NULL REFERENCES design.project(id),
    claim text NOT NULL,
    level meta.evidence_level NOT NULL,
    source_id uuid REFERENCES meta.source(id),
    artifact_id uuid REFERENCES app.artifact(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by_kind app.actor_kind NOT NULL,
    created_by_id text NOT NULL,
    CHECK (source_id IS NOT NULL OR artifact_id IS NOT NULL)
);

CREATE TABLE design.decision (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    program_id uuid NOT NULL REFERENCES design.project(id),
    action text NOT NULL,
    rationale text NOT NULL,
    decided_at timestamptz NOT NULL DEFAULT now(),
    decided_by_kind app.actor_kind NOT NULL,
    decided_by_id text NOT NULL
);

CREATE TABLE app.mission (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    program_id uuid REFERENCES design.project(id),
    objective text NOT NULL,
    state app.mission_state NOT NULL DEFAULT 'draft',
    autonomy_class text NOT NULL DEFAULT 'A0'
        CHECK (autonomy_class IN ('A0','A1','A2','A3')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    actor_kind app.actor_kind NOT NULL,
    actor_id text NOT NULL
);

CREATE TABLE app.run (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    mission_id uuid NOT NULL REFERENCES app.mission(id),
    attempt integer NOT NULL CHECK (attempt > 0),
    state app.run_state NOT NULL DEFAULT 'planned',
    started_at timestamptz,
    finished_at timestamptz,
    actor_kind app.actor_kind NOT NULL,
    actor_id text NOT NULL,
    UNIQUE (mission_id, attempt),
    CHECK (finished_at IS NULL OR started_at IS NOT NULL),
    CHECK (finished_at IS NULL OR finished_at >= started_at)
);

CREATE TABLE app.run_job (
    run_id uuid NOT NULL REFERENCES app.run(id) ON DELETE CASCADE,
    job_id uuid NOT NULL REFERENCES app.job(id),
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    purpose text NOT NULL,
    PRIMARY KEY (run_id, job_id),
    UNIQUE (run_id, ordinal)
);

-- The graph is generic only in storage; both endpoints and the edge vocabulary are
-- closed enums, and every edge carries actor provenance instead of an unbounded string.
CREATE TABLE app.object_relation (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_kind app.object_kind NOT NULL,
    source_id text NOT NULL,
    relation app.relation_kind NOT NULL,
    target_kind app.object_kind NOT NULL,
    target_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    actor_kind app.actor_kind NOT NULL,
    actor_id text NOT NULL,
    UNIQUE (source_kind, source_id, relation, target_kind, target_id),
    CHECK (NOT (source_kind = target_kind AND source_id = target_id))
);

CREATE INDEX object_relation_source ON app.object_relation (source_kind, source_id);
CREATE INDEX object_relation_target ON app.object_relation (target_kind, target_id);

-- Attention is derived from real state. There is no manually editable attention list
-- that can disagree with Jobs or approval-waiting Runs.
CREATE VIEW app.v_attention AS
SELECT 'job'::app.object_kind AS kind, j.id::text AS object_id,
       'failed'::text AS reason, j.finished_at AS at
  FROM app.job j WHERE j.state = 'failed'
UNION ALL
SELECT 'run'::app.object_kind, r.id::text, 'waiting_approval', r.started_at
  FROM app.run r WHERE r.state = 'waiting_approval';

INSERT INTO meta.migration (filename, sha256, content_sha256, hash_source)
VALUES ('015_domain_mission_run.sql', '\x66fc9c1dab73c79daf83f65b81587408ebefabea6c752b1112d4545d7ac1dcb4'::bytea,
        '\x66fc9c1dab73c79daf83f65b81587408ebefabea6c752b1112d4545d7ac1dcb4'::bytea, 'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
