# Dirac Architecture

Current as of 2026-08-12. This document describes live boundaries; product intent and
information architecture live under `docs/product/`, exact schemas under `contracts/`,
and runtime evidence in `STATUS.md`.

## System shape

```text
Canonical JSON contracts
  ObjectKind · ObjectRef · RelationKind · Command · Method · Error · Artifact
                     │ generated Python + TypeScript
                     ▼
Application command layer
  CommandRegistry → CommandDispatcher → typed handler
                     │
                     ▼
Invocation kernel
  MethodCatalog → validate → cache → JobStore → Executor → ArtifactStore → provenance
        │                    │                │
        │                    │                └─ Inline / Thread / Process / Remote
        │                    └─ field cache + generic method-version result cache
        └─ 12 executable scientific Methods
                     │
          ┌──────────┼──────────┐
          ▼          ▼          ▼
       HTTP v2   LocalTransport  durable PostgreSQL
          │          │          meta · chem · bio · design · app · audit
          └────┬─────┘
               ▼
      Python SDK · TypeScript SDK
          │          │
       CLI/MCP     AppShell + modules
                     │
        ScientificContextStore + persistent SceneService
```

No adapter owns scientific business logic. HTTP parses and serializes; CLI renders;
MCP projects safe commands; the GUI calls the same semantic commands. Validation,
method identity, execution mode, caching, Job creation, artifact addressing, canonical
errors and provenance belong below those adapters.

## Canonical contracts

- `contracts/domain/object-kinds.json` owns 30 stable `ObjectKind` values and the
  `{kind,id}` `ObjectRef` shape.
- `contracts/domain/relations.json` owns the controlled relation vocabulary.
- `contracts/commands/registry.json` owns semantic commands, schemas, mutation and Job
  policies, actor provenance, handlers and canonical errors.
- `contracts/methods/*.method.json` owns scientific method input/output schemas,
  execution policy, artifacts, refusals and cacheability.
- `contracts/errors.json` owns the error vocabulary.
- `scripts/gen_commands.py` validates and generates both SDK and application types. Its
  redproof proves duplicate commands and unknown object kinds are rejected.

## Application and scientific execution

`backend/dirac_app/dispatcher.py` is the transport-neutral semantic entrypoint. It
validates command input and output, records `human | agent | service` actor identity,
and enforces that long commands declared `job_policy=required` actually return a Job.

`backend/invocation.py` is the single scientific invocation path. It validates method
input/output, consults method-current caches, creates or joins Jobs, runs an injected
Executor, stores content-addressed artifacts, and returns one v2 envelope with exact
method version and provenance. Long scientific commands always submit through the
durable JobStore.

The executor boundary has four implementations: inline and bounded threads for the
current service, process isolation for picklable workers, and an injected remote adapter
for a future queue/cluster. SDK, CLI, HTTP and MCP do not change when the executor does.

## Durable state

PostgreSQL is the durable authority:

- `meta.method` identifies computational currency by source digest.
- `app.job` records durable lifecycle and in-flight deduplication.
- `app.blob` and `app.artifact` content-address result bytes; `app.job_artifact` links
  outputs without adding one Job column per result type.
- field cubes use the domain cache; other deterministic methods use
  `app.v_result_cache_servable`, which makes superseded method results unservable without
  deleting historical evidence.
- `design.program`, Campaign, Hypothesis, Evidence and Decision establish program-level
  scientific state.
- Mission, Run and Job are distinct. `app.run_job` links execution to delegated intent.
- `app.object_relation` is a controlled, actor-attributed graph over canonical refs.
- `app.v_attention` is derived from failed Jobs and approval-waiting Runs; it is not a
  manually editable list that can drift from reality.

Migrations are forward-only and content-hash checked. Applied migrations 000–016 are the
current schema history.

## Client application

`src/app/shell/registries.ts` is the one registry for eight Workspaces, thirty Views and
composable modules. Gated Views exist for information architecture but only implemented
Views are surfaced. `AppShell` owns routing and deep-link restoration;
`ScientificContextStore` owns Program, focus, selection and the application's only
staleness generation; `SceneService` owns one mol* instance that survives navigation.

The current vertical slice exposes Design, Structures and Runs. Existing chemistry
facets are modules over the shared scene and context. Molecule embedding, fields,
surface MEP and torsion strain enter through semantic commands; long results return by
Job and content-addressed artifacts.

## Runtime topology

- `dirac-fields.service` on `:8901` is the one application/scientific control plane.
- `dirac-web.service` on `:1360` serves the one production frontend bundle.
- `dirac-ops.service` on `:1355` is a read-only operational projection.
- PostgreSQL `dirac` owns durable state.
- the legacy `dirac-physics.service` is disabled and no process listens on `:8902`.

The local/LAN deployment is intentionally unauthenticated; network reachability remains
the boundary. A future multi-user or WAN deployment must add authentication and policy
before it may expose mutation commands.

## Growth rule

New capability is additive: register an ObjectKind only when needed, define a Method for
scientific compute, define a semantic Command for application behavior, persist durable
objects and relations, then expose the command through SDK/UI and the safe MCP projection.
No new Workspace or adapter may introduce a private scientific API, cache key, Job state,
error vocabulary, staleness clock, or mol* instance.

## Executable Digital Twin

`docs/architecture/dirac-digital-twin.html` is the offline interactive projection of
this architecture. It embeds `dirac-digital-twin.json`, a source-derived graph covering
every in-scope first-party Python, JavaScript, Shell and custom TypeScript function/method, their real
import/call edges, semantic contracts, AppShell registries, SQL objects, runtime
services and the system's principal information flows. Upstream Mol* and other
third-party internals remain explicit external boundaries rather than copied source.

Regenerate both artifacts from the repository and a best-effort live runtime snapshot:

```bash
python3 scripts/build_digital_twin.py
```
