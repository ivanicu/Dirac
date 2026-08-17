# Dirac Architecture

Current as of 2026-08-17. This document describes live boundaries; product intent and
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
        └─ 30 executable scientific Methods
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

- `contracts/domain/object-kinds.json` owns 83 stable `ObjectKind` values and the
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

`backend/invocation.py` is the single scientific invocation path for 30 executable scientific Methods. It validates method
input/output, consults method-current caches, creates or joins Jobs, runs an injected
Executor, stores content-addressed artifacts, and returns one v2 envelope with exact
method version and provenance. Long scientific commands always submit through the
durable JobStore.

The executor boundary supports inline and bounded threads, process isolation, governed
local GPU execution, and Kubernetes/Kueue placement. Admission seals the execution route
and production identity before asynchronous dispatch; worker evidence and fencing prevent
an incompatible or superseded worker from publishing completion. SDK, CLI, HTTP and MCP
do not change when execution placement changes.

## Durable state

PostgreSQL is the durable authority:

- `meta.method` identifies computational currency by source digest.
- `app.job` records durable lifecycle, invocation identity, generated outcome class and
  in-flight deduplication.
- `app.blob` and `app.artifact` content-address result bytes; `app.job_artifact` links
  outputs without adding one Job column per result type.
- field cubes use the domain cache; other deterministic methods use
  `app.v_result_cache_servable`, which makes superseded method results unservable without
  deleting historical evidence.
- `design.program`, Campaign, Hypothesis, Evidence and Decision establish program-level
  scientific state.
- Mission, Run and Job are distinct. `app.run_job` links execution to delegated intent.
- `app.object_relation` is a controlled, actor-attributed graph over canonical refs.
- `app.v_attention` contains operational/scientific failures and approval-waiting Runs;
  expected refusals remain in the Job ledger without becoming incidents.
- `app.command_trace` and `app.v_command_observation` retain semantic-command outcomes,
  latency, cache, actor and linked-Job evidence for the architecture twin.

Migrations are forward-only and content-hash checked. Migrations 000–048 are the
current schema history.

## Client application

`src/app/shell/registries.ts` is the one registry for eight Workspaces, thirty Views and
composable modules. Every View has a stable route and product shell; `implemented` remains
the separate truth for connected scientific capability. `AppShell` owns routing and deep-link restoration;
`ScientificContextStore` owns Program, focus, selection and the application's only
staleness generation; `SceneService` owns one mol* instance that survives navigation.

Twelve of thirty Views currently expose connected modules across Programs, Design,
Structures, Campaigns, Synthesis, Experiments, Knowledge and Runs. Existing chemistry
facets are modules over the shared scene and context. Molecule embedding, fields,
surface MEP and torsion strain enter through semantic commands; long results return by
Job and content-addressed artifacts.

The focused Discovery Lab is a second browser build, not a second scientific system. Its
landing page, FEP workbench and Field workbench share one navigation contract and the same
application/scientific backend while remaining deployable independently from the full
AppShell. It does not create another Command registry, Method catalog, Job state machine,
artifact identity or campaign generation clock.

## Runtime topology

- `dirac-fields.service` on `:8901` is the one application/scientific control plane.
- `dirac-web.service` on `:1360` serves the full product shell.
- `dirac-discovery-lab.service` on `:1370` serves the independent Discovery Lab bundle.
- `dirac-ops.service` on `:1355` is a read-only operational projection.
- PostgreSQL `dirac` owns durable state.
- the legacy `dirac-physics.service` is disabled and no process listens on `:8902`.

The local/LAN profile is intentionally unauthenticated; network reachability remains its
boundary. Remote mode is explicit and fail-closed on missing bearer identity, TLS, scope,
quota or artifact authorization. The local profile must not be exposed directly as a
public multi-user service.

## System invariants

1. A semantic Command has one application behavior regardless of transport.
2. Long-running work crosses a durable Job boundary before execution.
3. Scientific outputs remain content-addressed and linked to exact inputs, Method,
   execution identity and actor.
4. Navigation changes a projection; it does not create a second molecule, Program,
   campaign generation or SceneService.
5. Atom identity is preserved through molfile construction, RDKit perception, SVG
   interaction and mol\* selection.
6. Refused, stale, unverified and completed are distinct scientific states.

## Growth rule

New capability is additive: register an ObjectKind only when needed, define a Method for
scientific compute, define a semantic Command for application behavior, persist durable
objects and relations, then expose the command through SDK/UI and the safe MCP projection.
No new Workspace or adapter may introduce a private scientific API, cache key, Job state,
error vocabulary, staleness clock, or mol* instance.

## Architecture Optimization Twin

`docs/architecture/dirac-digital-twin.html` is the offline interactive projection of
this architecture. It embeds `dirac-digital-twin.json`, a source-derived graph covering
every in-scope first-party Python, JavaScript, Shell and custom TypeScript
function/method, their real
import/call edges, semantic contracts, AppShell registries, SQL objects, runtime
services and the system's principal information flows. Its default views are guided
system narratives, architecture fitness checks, evidence-backed optimization findings
and a change-impact simulator; function-level detail remains searchable on demand.
Upstream Mol* and other third-party internals remain explicit external boundaries rather
than copied source.

The truthful maturity is **L3 observed**, not a predictive twin. Source structure is
continuously synchronized while `dirac-digital-twin.service` is active. Semantic command
traces are durably recorded in PostgreSQL, joined to their Job's eventual terminal
outcome, and aggregated onto `command:<id>` / `method:<id>` nodes whenever the twin
rebuilds. The model can therefore calibrate observed command latency and outcomes in
addition to detecting drift, ranking static hotspots and estimating dependency radius.
It still cannot predict unseen inputs or autonomously change the architecture; that is
the L4 boundary. The platform substrate is complete against its approved DoD; the
product capability is explicitly partial (currently 8/8 Workspaces represented and 12/30 Views connected)
even though the navigable product shell is complete at 8/8 and 30/30.

`scripts/digital_twin_scope.json` is the ownership boundary. The watcher recursively
discovers tracked and untracked files under all first-party roots and automatically
includes new code roots unless they are explicitly classified as upstream/external.
Python AST, the TypeScript compiler, Shell and SQL parsers add files, functions, imports,
calls, schema objects and references. Outputs are written atomically after a 900 ms
debounce. `gate-14-architecture-twin` fails on a new/deleted file, a source fingerprint
change, dangling graph data, a stale embedded model, unhandled commands, unmapped
methods, adapter bypasses, duplicate state owners, any module import cycle, or leakage
of generated RDKit code. Its self-test proves both dangling-edge and cycle detection.

Regenerate both artifacts from the repository and a best-effort live runtime snapshot:

```bash
python3 scripts/build_digital_twin.py
node scripts/watch_digital_twin.mjs --selftest
bash scripts/gates.sh twin
```
