-- 007 · The two seams the terminal state cannot be reached without.
--
-- Both are SEAMS, not features: each is small today and load-bearing later,
-- and neither can be retrofitted once results exist that lack them.
--
-- SEAM A · METHOD REGISTRY (generalises meta.producer)
--   Today a producer is one row per SERVICE, versioned by a hash of an entire
--   849-line file. Measured consequence (2026-08-10): 8 generations in 53
--   minutes and 29% of the cache dark, because editing an HTTP comment
--   invalidated every SCF ever computed. The fix is granularity: identity is
--   the hash of THE CODE THAT CAN CHANGE THE NUMBER — one compute unit plus
--   its declared import closure — and nothing else.
--   The same row is the socket every future method plugs into: an ML
--   checkpoint (Pearl-class), docking, MD, FEP, structure prediction. A model
--   is a method whose version is its checkpoint hash. That is the whole
--   mechanism by which the system grows without a rewrite.
--
-- SEAM B · JOB LEDGER
--   Today a computation exists only as a Python thread; a restart answers
--   "what happened to that 6-minute SCF?" with 404. One row per computation
--   (even a 0.02 s one) makes the EXECUTOR replaceable: in-thread today,
--   process-per-job when cancellation must be exact, a cluster when there is
--   more than one machine — with no change to any caller, because callers
--   read state from here, not from process memory.
--   It is also the only place a DAG can later be expressed (parent_job_id),
--   which is why it must exist before the first multi-step workflow, not after.
--
-- Run:  psql -U ivan -d dirac -f backend/db/migrations/007_method_registry_and_job_ledger.sql
-- Then: psql -U ivan -d dirac -f backend/db/check_constraints.sql

BEGIN;

-- ── SEAM A · method registry ───────────────────────────────────────────────

CREATE TYPE meta.exec_class AS ENUM ('interactive', 'job');
-- interactive: bounded, sub-second-to-seconds, served in the request.
-- job:         may outlive the request; MUST have a ledger row (seam B).

CREATE TABLE meta.method (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Dotted compute-unit id, NOT a service name: 'fields.mep',
    -- 'fields.qm.homo', 'surface.sigma_hole', 'torsion.strain',
    -- and later 'ml.pearl.affinity', 'dock.unidock', 'md.openmm'.
    method_id     citext NOT NULL,
    -- Hash of the compute unit + its import closure. Machine-derived; a human
    -- never types it, so a forgotten bump is impossible by construction
    -- rather than by a tripwire that has already been swallowed twice.
    version       citext NOT NULL,
    source_sha256 bytea  NOT NULL CHECK (octet_length(source_sha256) = 32),
    -- What it consumes and emits, as JSON Schema. This is what makes a method
    -- callable by something that was not written against it — the
    -- precondition for agents and for a workflow engine.
    in_schema     jsonb  NOT NULL,
    out_schema    jsonb  NOT NULL,
    -- What it can answer and what it HONESTLY REFUSES: the iodine/ECP lesson
    -- as data. e.g. {"elements_max_z": 86, "requires_ecp_from_z": 37,
    --                "refuses": ["unconverged", "gasteiger_nonfinite"]}
    capabilities  jsonb  NOT NULL DEFAULT '{}'::jsonb,
    exec_class    meta.exec_class NOT NULL,
    toolkit_id    uuid REFERENCES meta.toolkit(id),
    declared_at   timestamptz NOT NULL DEFAULT now(),
    superseded_at timestamptz,
    notes         text,
    UNIQUE (method_id, version)
);

-- Deliberately NOT "one current per method": generation N and N+1 must be
-- able to coexist so an expensive cache can be recomputed onto the new
-- version BEFORE cutting over. The old design's one-current constraint made
-- every bump a thundering herd on the most expensive path in the system.
CREATE UNIQUE INDEX method_one_current ON meta.method (method_id)
    WHERE superseded_at IS NULL;

CREATE FUNCTION meta.register_method(
    p_method_id citext, p_version citext, p_source_sha256 bytea,
    p_in_schema jsonb, p_out_schema jsonb, p_exec_class meta.exec_class,
    p_capabilities jsonb DEFAULT '{}'::jsonb,
    p_toolkit_id uuid DEFAULT NULL, p_notes text DEFAULT NULL)
RETURNS uuid LANGUAGE plpgsql AS $$
DECLARE existing meta.method%ROWTYPE; new_id uuid;
BEGIN
    SELECT * INTO existing FROM meta.method
     WHERE method_id = p_method_id AND version = p_version;

    IF FOUND THEN
        IF existing.source_sha256 <> p_source_sha256 THEN
            -- Cannot happen while version IS the source hash; kept because a
            -- caller may pass a human version, and then this is the tripwire.
            RAISE EXCEPTION
                'method %/% already registered with a different source hash',
                p_method_id, p_version;
        END IF;
        RETURN existing.id;
    END IF;

    UPDATE meta.method SET superseded_at = now()
     WHERE method_id = p_method_id AND superseded_at IS NULL;

    INSERT INTO meta.method (method_id, version, source_sha256, in_schema,
                             out_schema, capabilities, exec_class, toolkit_id, notes)
    VALUES (p_method_id, p_version, p_source_sha256, p_in_schema,
            p_out_schema, p_capabilities, p_exec_class, p_toolkit_id, p_notes)
    RETURNING id INTO new_id;
    RETURN new_id;
END;
$$;

COMMENT ON FUNCTION meta.register_method IS
    'Call at service startup, once per compute unit. version should be the hash '
    'of the unit plus its import closure so it is derived, never remembered.';

-- The cache may name a method instead of a service. Nullable during the
-- transition: existing rows keep producer_id, new rows carry both, and the
-- read view accepts either. A dual-write window is how a live cache survives
-- a key change — the alternative is a flag day that darkens every row.
ALTER TABLE app.field_cube
    ADD COLUMN method_row_id uuid REFERENCES meta.method(id);

CREATE INDEX field_cube_method ON app.field_cube (method_row_id)
    WHERE method_row_id IS NOT NULL;

-- ── SEAM B · job ledger ────────────────────────────────────────────────────

CREATE TYPE app.job_state AS ENUM
    ('queued', 'running', 'done', 'failed', 'cancelled');

-- The error vocabulary is an ENUM because it is a CONTRACT the frontend
-- branches on (SPEC.md §4.6). Free text here would let every future method
-- invent its own spelling of "too expensive" and the UI could not react.
CREATE TYPE app.job_error AS ENUM
    ('PARSE', 'UNCONVERGED', 'UNPARAMETERIZED', 'BUDGET',
     'UNSUPPORTED', 'TOO_LARGE', 'INTERNAL', 'CANCELLED');

CREATE TABLE app.job (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    method_row_id  uuid NOT NULL REFERENCES meta.method(id),
    state          app.job_state NOT NULL DEFAULT 'queued',
    -- Scientific inputs by IDENTITY, never by value: this is what makes a job
    -- reconnectable to the object graph, i.e. what makes lineage possible.
    compound_id    uuid REFERENCES chem.compound(id),
    conformer_hash bytea CHECK (conformer_hash IS NULL OR octet_length(conformer_hash) = 32),
    input_sha256   bytea NOT NULL CHECK (octet_length(input_sha256) = 32),
    params         jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- DAG seam: present from day one so the first workflow is a write, not a
    -- migration. Unused until a multi-step method exists.
    parent_job_id  uuid REFERENCES app.job(id),
    -- Where the answer went. A job whose artifact is a cached field points at
    -- the row; a future job may point at a blob or another table.
    field_cube_id  uuid REFERENCES app.field_cube(id),
    budget_seconds numeric(10,3) CHECK (budget_seconds > 0),
    est_seconds    numeric(10,3) CHECK (est_seconds IS NULL OR est_seconds >= 0),
    seconds        numeric(10,3) CHECK (seconds IS NULL OR seconds >= 0),
    peak_rss_mb    integer CHECK (peak_rss_mb IS NULL OR peak_rss_mb > 0),
    error_code     app.job_error,
    error_detail   text,
    -- Which process ran it. NULL for in-thread; a pid or an external job id
    -- once the executor moves out of process. The column is the reason the
    -- executor can change without a migration.
    worker         text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    started_at     timestamptz,
    finished_at    timestamptz,

    -- The state machine, enforced by the schema rather than by a comment:
    CONSTRAINT job_terminal_has_finish CHECK (
        (state IN ('done','failed','cancelled')) = (finished_at IS NOT NULL)),
    CONSTRAINT job_running_has_start CHECK (
        (state = 'queued') = (started_at IS NULL)),
    CONSTRAINT job_failed_has_code CHECK (
        (state = 'failed') = (error_code IS NOT NULL AND error_code <> 'CANCELLED')),
    CONSTRAINT job_done_has_seconds CHECK (
        state <> 'done' OR seconds IS NOT NULL),
    CONSTRAINT job_time_order CHECK (
        finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at),
    -- Same all-or-nothing rule the coarse cache key already carries: half a
    -- chemical identity reads as "unknown molecule" forever.
    CONSTRAINT job_identity_complete CHECK (
        (compound_id IS NULL) = (conformer_hash IS NULL))
);

CREATE INDEX job_state_created ON app.job (state, created_at DESC);
CREATE INDEX job_method ON app.job (method_row_id, created_at DESC);
CREATE INDEX job_compound ON app.job (compound_id) WHERE compound_id IS NOT NULL;

-- At most one job per (method, input, params) may be alive. This is in-flight
-- deduplication as a CONSTRAINT: two tabs asking for the same field cannot
-- start two SCFs, and the rule cannot be forgotten by a caller.
CREATE UNIQUE INDEX job_one_inflight
    ON app.job (method_row_id, input_sha256, md5(params::text))
 WHERE state IN ('queued', 'running');

-- What a supervisor and the admin surface read. Never the base table: this
-- view is where "how long has it been stuck" is a column, not arithmetic in
-- five callers.
CREATE VIEW app.v_job_live AS
SELECT j.id, m.method_id, m.version AS method_version, j.state,
       j.compound_id, j.budget_seconds, j.est_seconds,
       round(extract(epoch FROM now() - coalesce(j.started_at, j.created_at))::numeric, 1)
           AS age_seconds,
       j.worker, j.created_at, j.started_at
  FROM app.job j JOIN meta.method m ON m.id = j.method_row_id
 WHERE j.state IN ('queued', 'running')
 ORDER BY j.created_at;

COMMENT ON VIEW app.v_job_live IS
    'The queue, as the admin dashboard and any watchdog see it. A job whose '
    'age_seconds exceeds budget_seconds is a deadline that did not fire — '
    'exactly the 36-minute runaway of 2026-08-10, now visible as a query.';

-- Restart recovery: a process cannot leave work claimed forever. Called at
-- startup, before anything is accepted. Without this, a crash makes the
-- in-flight dedup index refuse the retry of the very job that died.
CREATE FUNCTION app.reap_orphaned_jobs(p_worker text)
RETURNS integer LANGUAGE sql AS $$
    WITH reaped AS (
        UPDATE app.job
           SET state = 'failed', error_code = 'INTERNAL',
               error_detail = 'worker restarted while job was in flight',
               -- A queued job that never started still needs a start stamp to
               -- leave 'queued' (job_running_has_start). Caught by the gate
               -- suite before this file was committed: without it, a queued
               -- orphan is unreapable, so it holds its slot in the in-flight
               -- dedup index forever and the RETRY of the job that died is
               -- refused. The constraint was right; the reaper was wrong.
               started_at = coalesce(started_at, now()),
               finished_at = now(),
               seconds = coalesce(seconds, 0)
         WHERE state IN ('queued','running')
           AND worker IS NOT DISTINCT FROM p_worker
        RETURNING 1)
    SELECT count(*)::integer FROM reaped;
$$;

-- The ledger records the hash of the FILE CONTENT, not of the filename.
-- Migrations 001-006 all recorded digest('<filename>') — so an applied
-- migration could be edited afterwards and nothing would ever notice, one
-- directory away from the producer system built to prevent exactly that.
-- Computed by sha256sum at authoring time; verified by the gate suite.
INSERT INTO meta.migration (filename, sha256)
VALUES ('007_method_registry_and_job_ledger.sql', '\xff8aa3f196b7dc60e86d61d7bc742e2aa7811568f54c7ca3f5c6d3388460a41c'::bytea)
ON CONFLICT (filename) DO NOTHING;

COMMIT;
