# Construction status

Last re-derived: **2026-08-12**, from the running local system and repository gates.

## Platform substrate

| capability | current evidence |
|---|---|
| Canonical domain | 30 ObjectKinds, controlled relations and generated ObjectRef types in Python and TypeScript |
| Semantic commands | 17 registered commands; dispatcher validates input/output, actor and required-Job policy |
| Scientific methods | 12/12 registered Methods executable through one InvocationService |
| SDK / CLI / MCP | Python and TypeScript SDKs share commands; CLI JSON is parseable; MCP is a generated safe projection with no mutation tools |
| Durable execution | PostgreSQL JobStore, bounded ThreadExecutor, plus Inline/Process/Remote executor contracts |
| Results | content-addressed artifacts; method-current field cache and generic deterministic result cache |
| Application context | one ScientificContextStore generation; LigandStore and async facets delegate to it |
| AppShell | exactly 8 Workspaces and 30 Views; only implemented Design, Structures and Runs views surfaced |
| 3D scene | one SceneService-owned mol* instance survives route navigation |
| Durable domain | Program, Campaign, Hypothesis, Evidence, Decision, Mission, Run, Run↔Job and controlled relations |
| Attention | derived from failed Jobs and approval-waiting Runs |
| Architecture twin | L2 diagnostic; event-driven source synchronization plus generation-time runtime snapshot, health findings and static change-impact simulation |

## Current frontend modules

| module | state |
|---|---|
| `facets/field-wells/` | implemented; field commands and content-addressed cubes |
| `facets/ligand-physics/` | implemented; surface MEP and torsion through durable Jobs |
| `facets/property-cockpit/` | implemented; RDKit descriptor cockpit |
| `facets/pharmacophore-designer/` | implemented; editable model and screening |
| `facets/bond-atlas/` | implemented; shared ligand bond information |
| `facets/halogen-audit/` | implemented with explicit geometry/QM evidence boundary |

## Live local topology

| component | state |
|---|---|
| `dirac-fields.service` / `:8901` | active; unified command, method, Job, cache and artifact control plane |
| `dirac-web.service` / `:1360` | active; serves `build/dirac` |
| `dirac-ops.service` / `:1355` | active; read-only ops projection |
| `dirac-digital-twin.service` | active; recursive first-party source watcher and atomic DT regeneration |
| legacy `:8902` | absent; service disabled and hand-run process terminated after migration |
| PostgreSQL `dirac` | migrations 000–016 applied; content hashes clean |

## Verification snapshot

- TypeScript typecheck and focused AppShell/ScientificContext/LigandStore specs: green.
- Production `dirac` bundle: green.
- Migration hash gate: 17 compared, zero drift; tamper redproof convicted.
- Real generic cache: first torsion call `computed`, second `db`; result and artifact
  SHA-256 identical.
- Real HTTP and CLI: 17 commands discovered; `system.health` reports durable PostgreSQL
  JobStore and ArtifactStore.
- Real MCP JSON-RPC: initialization, generated safe tool discovery and semantic health
  call succeeded; mutation commands excluded.
- Real long command: agent-attributed torsion command created a durable Job, completed
  with exact method version, and linked `torsion.profile` artifact.

The local/LAN service has no authentication. Do not treat it as a public multi-user
deployment. Product expansion Workspaces remain gated until their scientist-facing views
are implemented; the platform architecture they will use is now present.

## Scope verdict

The approved **platform substrate phase is complete**. The whole Dirac product is not:
3 of 8 Workspaces and 7 of 30 Views currently expose implemented vertical slices. The
remaining registry entries are product intent protected by capability gates, not shipped
features. The Architecture Optimization Twin reports this distinction directly.
