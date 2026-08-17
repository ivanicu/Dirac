# Construction status

Last re-derived: **2026-08-17** from the canonical registries, contracts, migrations,
repository gates, GitHub CI and live supervised local services.

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
| Connected UI | 12 of 30 Views connected to 15 composable modules; the remaining 18 are explicit shells |
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

## Motif Workbench

The separately built Motif Workbench is not counted as another AppShell Workspace or View.
It is a focused development frontend over the same application/scientific backend:

| Entry | State |
|---|---|
| `discovery-lab/` | connected Motif Workbench navigation and backend-health landing page; directory name retained for build compatibility |
| `fep-workbench/` | connected campaign, pose-review, mapping and governed-run qualification workflow |
| `field-workbench/` | connected parent/proposal field-comparison workflow |

FEP and Field share navigation, visual language and backend contracts. Motif Workbench is
deployed independently from Dirac Workspace while both interaction models are under active
development, so neither frontend can silently replace the other.

## Verified local topology

Verified on 2026-08-17 with listening sockets and HTTP responses:

| Component | State |
|---|---|
| `dirac-web.service` / `:1360` | `0.0.0.0`; HTTP 200; full Dirac Workspace |
| `dirac-discovery-lab.service` / `:1370` | `0.0.0.0`; HTTP 200; Motif Workbench with FEP and Field navigation; legacy unit identifier retained |
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

Counts above describe the canonical source tree, not a release tag or a claim that every
registered Method has completed prospective scientific validation.

## Scope verdict

The command/method/job/artifact substrate, Dirac Workspace and focused Motif Workbench
exist. The product remains partial: 12/30 AppShell Views are connected, the remaining Views
are explicit shells, and individual scientific Methods need method-specific validation
before prospective claims can be made. Workflow completeness, execution correctness and
scientific validity remain separate acceptance gates.
