-- 028 · One Program Work Item moves through the discovery workflow without copies.
--
-- A Work Item is the stable scientific unit of intent. Work Packages are immutable
-- revisions of its specification; runtime Jobs are execution attempts attached to it.
-- This prevents Program, Design, Decide, Make and Test from minting private task IDs.

ALTER TYPE app.object_kind ADD VALUE IF NOT EXISTS 'work_item';

BEGIN;

CREATE TYPE design.workflow_lane AS ENUM (
    'understand', 'design', 'decide', 'make', 'test_learn'
);

CREATE TABLE design.program_work_item (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    program_id uuid NOT NULL REFERENCES design.project(id) ON DELETE RESTRICT,
    work_key citext NOT NULL,
    title text NOT NULL CHECK (btrim(title) <> ''),
    current_lane design.workflow_lane NOT NULL DEFAULT 'understand',
    current_package_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by_kind app.actor_kind NOT NULL,
    created_by_id text NOT NULL,
    UNIQUE (program_id, work_key)
);

-- Existing Work Packages become revisions of one newly-minted stable Work Item.
INSERT INTO design.program_work_item(program_id,work_key,title,created_at,created_by_kind,created_by_id)
SELECT DISTINCT ON (program_id,work_key)
       program_id,work_key,title,created_at,created_by_kind,created_by_id
  FROM design.program_work_package
 ORDER BY program_id,work_key,revision;

ALTER TABLE design.program_work_package ADD COLUMN work_item_id uuid;
UPDATE design.program_work_package package
   SET work_item_id = item.id
  FROM design.program_work_item item
 WHERE item.program_id=package.program_id AND item.work_key=package.work_key;
ALTER TABLE design.program_work_package
    ALTER COLUMN work_item_id SET NOT NULL,
    ADD CONSTRAINT program_work_package_item_fk
        FOREIGN KEY (work_item_id) REFERENCES design.program_work_item(id) ON DELETE RESTRICT,
    ADD CONSTRAINT program_work_package_item_revision_unique UNIQUE (work_item_id,revision);

UPDATE design.program_work_item item
   SET current_package_id = current.id,
       title = current.title
  FROM (
      SELECT DISTINCT ON (work_item_id) id,work_item_id,title
        FROM design.program_work_package
       ORDER BY work_item_id,revision DESC
  ) current
 WHERE current.work_item_id=item.id;
ALTER TABLE design.program_work_item
    ADD CONSTRAINT program_work_item_current_package_fk
        FOREIGN KEY (current_package_id) REFERENCES design.program_work_package(id) ON DELETE RESTRICT;

CREATE TABLE design.program_work_transition (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    work_item_id uuid NOT NULL REFERENCES design.program_work_item(id) ON DELETE RESTRICT,
    from_lane design.workflow_lane,
    to_lane design.workflow_lane NOT NULL,
    reason text NOT NULL CHECK (btrim(reason) <> ''),
    transitioned_at timestamptz NOT NULL DEFAULT now(),
    transitioned_by_kind app.actor_kind NOT NULL,
    transitioned_by_id text NOT NULL,
    CHECK (from_lane IS NULL OR from_lane <> to_lane)
);
CREATE INDEX program_work_transition_item
    ON design.program_work_transition(work_item_id,transitioned_at DESC);

INSERT INTO design.program_work_transition(
    work_item_id,from_lane,to_lane,reason,transitioned_at,transitioned_by_kind,transitioned_by_id)
SELECT id,NULL,current_lane,'Migrated into the canonical Program workflow',created_at,created_by_kind,created_by_id
  FROM design.program_work_item;

CREATE TABLE design.program_work_item_dependency (
    work_item_id uuid NOT NULL REFERENCES design.program_work_item(id) ON DELETE RESTRICT,
    depends_on_work_item_id uuid NOT NULL REFERENCES design.program_work_item(id) ON DELETE RESTRICT,
    PRIMARY KEY (work_item_id,depends_on_work_item_id),
    CHECK (work_item_id <> depends_on_work_item_id)
);

INSERT INTO design.program_work_item_dependency(work_item_id,depends_on_work_item_id)
SELECT DISTINCT package.work_item_id,dependency.work_item_id
  FROM design.program_work_dependency edge
  JOIN design.program_work_package package ON package.id=edge.work_package_id
  JOIN design.program_work_package dependency ON dependency.id=edge.depends_on_id
ON CONFLICT DO NOTHING;

CREATE TABLE design.program_work_execution (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    work_item_id uuid NOT NULL REFERENCES design.program_work_item(id) ON DELETE RESTRICT,
    job_id uuid NOT NULL UNIQUE REFERENCES app.job(id) ON DELETE RESTRICT,
    purpose text,
    linked_at timestamptz NOT NULL DEFAULT now(),
    linked_by_kind app.actor_kind NOT NULL,
    linked_by_id text NOT NULL
);
CREATE INDEX program_work_execution_item
    ON design.program_work_execution(work_item_id,linked_at DESC);

CREATE FUNCTION app.sync_program_work_item_trigger() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM app.register_entity(
        'work_item',NEW.id::text,NEW.program_id::text||':'||NEW.work_key::text,
        NEW.title,'design','program_work_item');
    RETURN NEW;
END $$;

SELECT app.register_entity(
    'work_item',id::text,program_id::text||':'||work_key::text,title,
    'design','program_work_item')
  FROM design.program_work_item;
CREATE TRIGGER entity_program_work_item AFTER INSERT OR UPDATE ON design.program_work_item
    FOR EACH ROW EXECUTE FUNCTION app.sync_program_work_item_trigger();

ALTER TABLE design.program_evidence_binding
    DROP CONSTRAINT program_evidence_binding_subject_kind_check,
    ADD CONSTRAINT program_evidence_binding_subject_kind_check CHECK (
        subject_kind IN ('program','objective','hypothesis','decision','milestone',
                         'stage_gate','work_item','work_package'));

ALTER TABLE design.program_event DROP CONSTRAINT program_event_event_kind_check;
ALTER TABLE design.program_event ADD CONSTRAINT program_event_event_kind_check CHECK (event_kind IN (
    'program.created','program.updated','objective.recorded','hypothesis.recorded',
    'decision.recorded','milestone.recorded','object.linked','snapshot.created',
    'portfolio.assigned','member.assigned','stage_gate.recorded','work_package.recorded',
    'work_item.transitioned','work_execution.linked','evidence.attached','lineage.recorded'
));

COMMENT ON TABLE design.program_work_item IS
    'Stable Program job identity. It moves between workflow lanes; it is never copied per Workspace.';
COMMENT ON TABLE design.program_work_package IS
    'Immutable revision of a stable Program Work Item specification.';
COMMENT ON TABLE design.program_work_execution IS
    'A runtime Job may belong to exactly one Program Work Item; a Work Item may have many attempts.';

INSERT INTO meta.migration (filename, sha256, content_sha256, hash_source)
VALUES ('028_unique_program_work_items.sql', '\x1f10e56fc3d17f816ad4578314639817dda18cc686607159c42976589f6b0e44'::bytea,
        '\x1f10e56fc3d17f816ad4578314639817dda18cc686607159c42976589f6b0e44'::bytea, 'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
