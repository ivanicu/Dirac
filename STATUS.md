# Construction status

Last re-derived: **2026-08-13** from the current working tree, repository gates and the
live supervised local services.

This page distinguishes platform plumbing, connected product capability and scientific
validation. A green contract or transport gate does not validate a scientific model.

## Source-derived platform snapshot

| Capability | Current evidence |
|---|---|
| Canonical domain | 83 ObjectKinds and 27 controlled relation kinds; ObjectRef types generated for Python and TypeScript |
| Semantic Commands | 92 registered Commands with schemas, actor identity, mutation policy, required-Job policy and typed errors |
| Scientific Methods | 30 Method manifests behind one InvocationService |
| Durable schema | 49 forward-only migrations, `000` through `048` |
| Product shell | 8 Workspaces and 30 routable Views |
| Connected UI | 13 of 30 Views connected to 17 composable modules; the remaining 17 are explicit shells |
| Interfaces | HTTP v2, Python SDK, CLI and generated safe MCP projection share the command boundary |
| Execution | durable JobStore plus inline, thread, process, local GPU and Kubernetes/Kueue adapters |
| Results | content-addressed artifacts, method-current caches, provenance and command observations |
| Program state | Programs, portfolios, objectives, hypotheses, decisions, work packages/items, evidence and lineage |
| Scientific automation | Motif dataset, model, proposal, structure, MD, RBFE, validation and closed-loop control paths |
| Architecture twin | source-derived graph with contract, SQL, module, runtime-observation and change-impact projections |

## Connected frontend modules

| Module area | State |
|---|---|
| `facets/field-wells/` | connected through field commands and artifacts |
| `facets/ligand-physics/` | connected through surface and torsion Jobs |
| `facets/property-cockpit/` | connected to RDKit descriptor calculation |
| `facets/pharmacophore-designer/` | connected for editable models and library screening |
| `facets/bond-atlas/` | connected to shared ligand bond information |
| `facets/halogen-audit/` | connected with explicit geometry/QM evidence boundaries |

Connected Views currently cover Program overview, molecule design/objectives, three
structure Views, compound records, material/sample records, experiment records, evidence,
and active/history compute projections. The other Views are navigable contracts, not
claims of shipped scientific capability.

## Verified local topology

Verified on 2026-08-13 with service state, listening sockets and HTTP responses:

| Component | State |
|---|---|
| `dirac-web.service` / `:1360` | active; `0.0.0.0`; HTTP 200; the only Dirac web server |
| `dirac-fields.service` / `:8901` | active; `0.0.0.0`; health HTTP 200; unified application/scientific control plane |
| `dirac-ops.service` / `:1355` | active; `0.0.0.0`; HTTP 200; read-only operational projection |
| `dirac-digital-twin.service` | active; watches first-party source and regenerates the twin |
| legacy `:8902` | no listener; standalone physics unit archived as superseded |

The default LAN profile is unauthenticated. It is not a public multi-user deployment.

## Verification boundary

Repository gates cover typechecking, production builds, palette/CSS invariants, document
facts, generated contracts, migrations, portability, layering, physics protections,
transport parity, remote security and architecture-twin coherence. Database and live
transport gates require their real dependencies; a skipped gate is unverified, not green.

The working tree contains ongoing Motif scientific-semantics work. Counts above include
those present files and contracts; they should not be interpreted as a release tag.

## Scope verdict

The command/method/job/artifact substrate and the complete navigation shell exist. The
product remains partial: 13/30 Views are connected, application-grade HCI action semantics
are still being migrated, and individual scientific Methods need method-specific validation
before prospective claims can be made. Workflow completeness and scientific validity are
separate acceptance gates.
