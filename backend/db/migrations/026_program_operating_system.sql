-- 026 · Program operating system: portfolio, people, gates, work, evidence and lineage.
--
-- DAIKON contributes discovery-stage semantics; openBIS contributes typed hierarchy,
-- immutable evidence and auditability; Chemotion/GSRS contribute the distinction
-- between compound, form, batch and sample.  Dirac owns the unified model below.
-- PostgreSQL will not let a newly-added enum label participate in a CHECK
-- constraint until the ALTER TYPE transaction commits.  Keep these three
-- additive, idempotent vocabulary changes outside the atomic schema body.
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'portfolio';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'stage_gate';
ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'work_package';

BEGIN;

CREATE TABLE design.portfolio (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code citext UNIQUE NOT NULL,
    name text NOT NULL CHECK (btrim(name) <> ''),
    mandate text,
    lifecycle text NOT NULL DEFAULT 'active'
        CHECK (lifecycle IN ('draft','active','paused','completed','archived')),
    version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    updated_by_kind app.actor_kind NOT NULL,
    updated_by_id text NOT NULL
);

ALTER TABLE design.project
    ADD COLUMN portfolio_id uuid REFERENCES design.portfolio(id) ON DELETE RESTRICT;

CREATE UNIQUE INDEX program_one_portfolio
    ON design.project (id, portfolio_id) WHERE portfolio_id IS NOT NULL;

CREATE TYPE design.program_member_role AS ENUM (
    'program_lead','medicinal_chemistry','computational_chemistry','biology',
    'dmpk','toxicology','synthesis','data_science','operations','reviewer','observer'
);

CREATE TABLE design.program_member (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    program_id uuid NOT NULL REFERENCES design.project(id) ON DELETE RESTRICT,
    principal_kind app.actor_kind NOT NULL,
    principal_id text NOT NULL CHECK (btrim(principal_id) <> ''),
    role design.program_member_role NOT NULL,
    responsibility text,
    assigned_at timestamptz NOT NULL DEFAULT now(),
    assigned_by_kind app.actor_kind NOT NULL,
    assigned_by_id text NOT NULL,
    retired_at timestamptz,
    CHECK (retired_at IS NULL OR retired_at >= assigned_at)
);
CREATE UNIQUE INDEX program_member_current
    ON design.program_member (program_id, principal_kind, principal_id, role)
    WHERE retired_at IS NULL;
CREATE INDEX program_member_program
    ON design.program_member (program_id, role) WHERE retired_at IS NULL;

CREATE TABLE design.program_stage_gate (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    program_id uuid NOT NULL REFERENCES design.project(id) ON DELETE RESTRICT,
    gate_key citext NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    stage design.program_stage NOT NULL,
    title text NOT NULL CHECK (btrim(title) <> ''),
    criteria jsonb NOT NULL CHECK (
        jsonb_typeof(criteria) = 'array' AND jsonb_array_length(criteria) > 0),
    status text NOT NULL DEFAULT 'planned'
        CHECK (status IN ('planned','ready','approved','rejected','superseded')),
    evidence_summary text,
    decision_id uuid REFERENCES design.decision(id) ON DELETE RESTRICT,
    target_date date,
    assessed_at timestamptz,
    supersedes_id uuid REFERENCES design.program_stage_gate(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by_kind app.actor_kind NOT NULL,
    created_by_id text NOT NULL,
    UNIQUE (program_id, gate_key, revision),
    CHECK (supersedes_id IS NULL OR supersedes_id <> id),
    CHECK ((status IN ('approved','rejected')) = (assessed_at IS NOT NULL)),
    CHECK (status NOT IN ('approved','rejected') OR decision_id IS NOT NULL)
);
CREATE UNIQUE INDEX program_stage_gate_one_current
    ON design.program_stage_gate (program_id, gate_key)
    WHERE status <> 'superseded';

CREATE TABLE design.program_work_package (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    program_id uuid NOT NULL REFERENCES design.project(id) ON DELETE RESTRICT,
    work_key citext NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    title text NOT NULL CHECK (btrim(title) <> ''),
    description text NOT NULL CHECK (btrim(description) <> ''),
    status text NOT NULL DEFAULT 'backlog'
        CHECK (status IN ('backlog','ready','active','blocked','done','cancelled','superseded')),
    priority integer NOT NULL DEFAULT 3 CHECK (priority BETWEEN 1 AND 5),
    owner_kind app.actor_kind,
    owner_id text,
    due_on date,
    deliverable_refs jsonb NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(deliverable_refs) = 'array'),
    supersedes_id uuid REFERENCES design.program_work_package(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by_kind app.actor_kind NOT NULL,
    created_by_id text NOT NULL,
    UNIQUE (program_id, work_key, revision),
    CHECK ((owner_kind IS NULL) = (owner_id IS NULL)),
    CHECK (supersedes_id IS NULL OR supersedes_id <> id)
);
CREATE UNIQUE INDEX program_work_package_one_current
    ON design.program_work_package (program_id, work_key)
    WHERE status <> 'superseded';

CREATE TABLE design.program_work_dependency (
    work_package_id uuid NOT NULL REFERENCES design.program_work_package(id) ON DELETE CASCADE,
    depends_on_id uuid NOT NULL REFERENCES design.program_work_package(id) ON DELETE RESTRICT,
    PRIMARY KEY (work_package_id, depends_on_id),
    CHECK (work_package_id <> depends_on_id)
);

CREATE TABLE design.program_evidence_binding (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    program_id uuid NOT NULL REFERENCES design.project(id) ON DELETE RESTRICT,
    subject_kind app.object_kind NOT NULL,
    subject_id text NOT NULL CHECK (btrim(subject_id) <> ''),
    relation app.relation_kind NOT NULL
        CHECK (relation IN ('supports','contradicts','tests','explains')),
    evidence_kind app.object_kind NOT NULL,
    evidence_id text NOT NULL CHECK (btrim(evidence_id) <> ''),
    claim text NOT NULL CHECK (btrim(claim) <> ''),
    strength numeric(4,3) CHECK (strength BETWEEN 0 AND 1),
    attached_at timestamptz NOT NULL DEFAULT now(),
    attached_by_kind app.actor_kind NOT NULL,
    attached_by_id text NOT NULL,
    UNIQUE (program_id, subject_kind, subject_id, relation, evidence_kind, evidence_id),
    CHECK (subject_kind IN ('program','objective','hypothesis','decision','milestone','stage_gate','work_package')),
    CHECK (evidence_kind IN ('evidence','measurement','dataset','artifact','literature_reference','prediction','complex','pose','field','batch','sample'))
);
CREATE INDEX program_evidence_subject
    ON design.program_evidence_binding (program_id, subject_kind, subject_id);

ALTER TABLE design.program_event DROP CONSTRAINT program_event_event_kind_check;
ALTER TABLE design.program_event ADD CONSTRAINT program_event_event_kind_check CHECK (event_kind IN (
    'program.created','program.updated','objective.recorded','hypothesis.recorded',
    'decision.recorded','milestone.recorded','object.linked','snapshot.created',
    'portfolio.assigned','member.assigned','stage_gate.recorded','work_package.recorded',
    'evidence.attached','lineage.recorded'
));

CREATE OR REPLACE VIEW design.program AS
SELECT id, code, name, target_id, lifecycle, stage, version, summary,
       indication, modality, owner_id, started_on, closed_on, created_at, updated_at,
       archived_at, updated_by_kind, updated_by_id, portfolio_id
  FROM design.project;

COMMENT ON TABLE design.program_member IS
    'Current and historical Program responsibilities. Assignment is explicit and actor-attributed.';
COMMENT ON TABLE design.program_stage_gate IS
    'Versioned evidence-bearing stage gate. Approval or rejection must reference a Decision.';
COMMENT ON TABLE design.program_work_package IS
    'Scientific work with ownership, delivery state, due date and typed deliverable references.';
COMMENT ON TABLE design.program_evidence_binding IS
    'Program-local evidence graph; claims remain separate from the evidence objects that support or contradict them.';

INSERT INTO meta.migration (filename, sha256, content_sha256, hash_source)
VALUES ('026_program_operating_system.sql', '\x19506a96800e14bfd86cb9109b993c12d4229af2dffb6b62da969663eec93d1a'::bytea,
        '\x19506a96800e14bfd86cb9109b993c12d4229af2dffb6b62da969663eec93d1a'::bytea, 'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
