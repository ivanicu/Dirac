# Dirac

![Dirac — a scientific operating environment for molecular discovery](docs/screenshots/dirac-hero.png)

> **Dirac is to Schrödinger's molecular-design platform what the Dirac equation is to
> the Schrödinger equation: an open-source, browser-native upgrade.**

Dirac is an open, browser-native molecular discovery platform that combines a mol\* +
RDKit workbench with Programs, versioned scientific Methods, durable Jobs,
content-addressed Artifacts and provenance. Computational chemists, ML researchers,
automation and agents work through the same semantic Commands—from hypothesis to
inspectable evidence.

![Dirac rendering an electrostatic field around the 1CBS ligand](docs/screenshots/02_fields_electrostatic_well.png)

## Introducing Motif v3

We built **Motif v3** to carry molecular machine learning from raw observations to a
validated model release, decision-ready predictions and the next experiment. It brings
model development, computational chemistry and production execution into one traceable
loop:

```text
Observations → Dataset Snapshot → Feature Release → Model Release
             → Prediction + Uncertainty → Acquisition → Simulation → Evidence → Decision
```

### A complete molecular ML lifecycle

| Capability | What Motif v3 provides |
|---|---|
| Reproducible data and features | Immutable dataset snapshots preserve molecular identity, endpoint semantics, units, censoring, assay context, splits and source lineage. Versioned RDKit feature releases make the representation itself inspectable and reusable. |
| Deep models plus rigorous baselines | Chemprop D-MPNN ensembles run through PyTorch and Lightning alongside Ridge, nearest-neighbor, random forest, XGBoost, censored Tobit and pairwise-ranking models. Every member is trained and compared over the same frozen data contract. |
| Governed model releases | Checkpoints are data-only, digest-verified Artifacts tied to source, parameters, runtime, dataset and feature releases. Technical smoke, scientific validation, promotion and retirement are distinct lifecycle states. |
| Honest uncertainty and applicability | Ensemble dispersion, conditional conformal intervals and model × endpoint × representation domain assessment remain separate signals. Out-of-domain inputs can be refused instead of receiving an unjustifiably confident score. |
| Multi-objective molecular design | BoTorch/GPyTorch qLogEHVI operates over an explicit finite candidate set with validated posteriors, hard constraints, pending-point conditioning, Pareto ranking, diversity and selection-sensitivity analysis. |
| Research-to-production execution | Training, prediction and acquisition use durable Jobs, governed CPU/GPU resources, retries, cancellation, content-addressed outputs and local or Kubernetes/Kueue execution without changing scientific identity. |
| ML and physics in one evidence graph | Predictions can route into structure preparation, docking, torsion, MD, OpenFE and covariance-aware RBFE; results return as typed evidence with conditions, quality assessment, dependencies and claim eligibility. |

Motif v3 makes the engineering around a model as explicit as the model itself. A research
idea can become a reproducible experiment, a promoted capability and a scientist-facing
decision surface without losing the failures, uncertainty, compute cost or physical
assumptions that produced it. Its public `motif.plan`, `motif.validate` and
`motif.explain` Commands expose evidence-driven action planning through the same
interface as the rest of Dirac.

See the [Motif v3 system of record](docs/product/motif-v3/README.md) for its semantic,
physical, execution and validation contracts.

## One continuous scientific loop

| Scientific step | Dirac surface | Durable result |
|---|---|---|
| Frame the question | Programs connect objectives, hypotheses, compounds and work items. | A versioned intent and ownership context |
| Explore molecular context | One persistent mol\* scene and shared RDKit chemistry coordinate protein, ligand, 2D and 3D views. | Reusable selections, structures and derived chemical features |
| Run a method | A semantic Command resolves a versioned Method and submits work through one invocation path. | A durable Job with exact inputs, actor and runtime identity |
| Interrogate the outcome | Completed, partial, failed, stale and cancelled work remains distinguishable; large outputs stay addressable as Artifacts. | Results, uncertainty, provenance and typed failure evidence |
| Make the next decision | Programs and Campaigns relate evidence back to hypotheses and follow-up work. | A traceable decision rather than an isolated file or notebook cell |

This loop is the product boundary: visualization, models, simulation, data and compute are
parts of one scientific decision system rather than separate tools.

## What teams can use today

| Capability | Current implementation |
|---|---|
| Interactive molecular workbench | Protein and ligand exploration, synchronized 2D/3D chemistry, pharmacophore editing, property analysis, torsion and field visualizations |
| Program and experiment context | Durable Programs, objectives, hypotheses, compounds, work packages, decisions, evidence and lineage projected across shared Workspaces |
| ML lifecycle | Governed paths for dataset snapshots, model training, prediction, ranking, release validation, uncertainty and out-of-domain assessment |
| Computational chemistry | Versioned structure, docking, conformer, torsion, MD, OpenFE and RBFE Methods with explicit inputs, outputs and refusal conditions |
| Long-running compute | PostgreSQL-backed Jobs and attempts, retries, cancellation, resource leases, fenced completion and content-addressed Artifacts |
| Execution at different scales | Inline, thread, process, local GPU and Kubernetes/Kueue adapters keep placement separate from scientific intent |
| Programmatic integration | HTTP v2, Python SDK, CLI and generated safe agent projection share the browser application's command boundary |
| Operational understanding | Typed errors, command observations, a read-only operations surface and a source-derived architecture twin |

## Representative surfaces

| Molecular workspace | Property analysis | Operations |
|---|---|---|
| <img src="docs/screenshots/01_lab_1cbs_hbond.png" alt="Synchronized molecular workspace showing 2D ligand chemistry and a 3D protein pocket" width="320"> | <img src="docs/screenshots/03_properties_cockpit.png" alt="Molecular property analysis cockpit" width="320"> | <img src="docs/screenshots/05_ops_console.png" alt="Operations console showing Jobs and artifact-cache state" width="320"> |
| Synchronized ligand chemistry and structural context | Decision-oriented molecular descriptors | Live Job, service and artifact state |

## Construction status

Dirac currently defines 8 stable Workspaces and 30 routable Views, of which 12 are
connected to working modules. The contract layer contains 92 versioned semantic Commands
over 83 canonical ObjectKinds and 30 scientific Method manifests behind one invocation
path. PostgreSQL owns Programs, Jobs, attempts, artifacts, relations, provenance and
execution control.

These counts are source-derived from the working tree on 2026-08-13; the documentation
gate fails when they drift from the registries. Dirac is under active development: the
platform substrate and product shell are real, while 18 Views remain explicit
implementation shells and individual scientific Methods still require method-specific
validation. See [Construction status](STATUS.md) for the current evidence boundary and
[Architecture](ARCHITECTURE.md) for the live system shape.

## Product model

Dirac organizes work around scientific intent, not around algorithms:

| Layer | Owns |
|---|---|
| Programs | objectives, hypotheses, compounds, work items, decisions, evidence and lineage |
| Workspaces and Views | human navigation and composable modules over shared scientific context |
| Commands | versioned application actions with schemas, mutation policy, actor identity and typed errors |
| Methods | reproducible scientific computation, runtime identity, estimates and refusal conditions |
| Jobs and Runs | durable execution state, attempts, scheduling, retries, cancellation and attention |
| Artifacts and provenance | content-addressed outputs linked to exact methods, inputs and actors |

Algorithms such as docking, QM, MD, RBFE and ML are Methods reached through Commands.
They are not separate products or navigation silos.

## Architecture

```text
Canonical JSON contracts
  ObjectKind · Relation · Command · Method · Error · Artifact
                         │
                         ▼
CommandRegistry → CommandDispatcher → typed application handler
                         │
                         ▼
MethodCatalog → InvocationService → JobStore → Executor
                         │                         │
                         └──────────────► ArtifactStore + provenance

HTTP v2 · Python SDK · CLI · MCP · browser application
                         │
ScientificContextStore → AppShell → Workspace/View/Module registries
                         │
                  one persistent mol* SceneService
```

PostgreSQL is the durable authority. The browser is a projection over shared context and
semantic commands; it does not own a second scientific API. Long computations return a
Job and addressable artifacts rather than embedding large results in transport envelopes.

## Quick start: browser application

Prerequisites: Node.js 22 or newer and npm.

```bash
git clone https://github.com/ivanicu/Dirac.git
cd Dirac
npm ci
npm run build:dirac
node_modules/.bin/http-server build/dirac -p 1360 -g -c-1 -a 0.0.0.0 -P http://127.0.0.1:1360?
```

Open <http://localhost:1360/>. The first load fetches the vendored RDKit WASM bundle;
after that the browser cache supplies it.

On the canonical workstation, **do not run that server command**: `dirac-web.service`
already owns the one allowed web port, 1360. Rebuild with `npm run build:dirac` and reload
the existing page. See [Runtime and deployment](deploy/README.md).

The browser can open bundled structures and run in-browser RDKit features without the
Python service. Durable Programs, server-side Methods, Jobs and artifacts require the
application service and PostgreSQL.

## Application service and CLI

The canonical workstation uses the repository-local scientific environment:

```bash
backend/env/bin/python backend/field_server.py
PYTHONPATH=python/src python3 -m dirac.cli commands --json
PYTHONPATH=python/src python3 -m dirac.cli health --json
```

The service listens on `0.0.0.0:8901` and exposes health, command discovery, invocation,
Jobs and artifacts through HTTP v2. The CLI can use in-process or HTTP transport and
reports which transport handled the request. Environment provisioning, PostgreSQL
migrations and security profiles are documented in [Backend](backend/README.md),
[Database](backend/db/README.md), [Deployment](deploy/README.md), and
[Remote security](docs/security/REMOTE.md).

The local/LAN profile is intentionally unauthenticated. Do not expose it to the public
internet. The remote profile must add HTTPS, operator-issued credentials, scopes, quotas
and artifact authorization.

## Verification

The repository gate suite derives its claims from source and runs every selected gate
even if an earlier one fails:

```bash
bash scripts/gates.sh
```

Some gates require the local PostgreSQL database or the scientific daemon. For a
portable source/build pass:

```bash
bash scripts/gates.sh tsc build palette css docs contracts portability layering twin
```

The main CI additionally migrates a clean PostgreSQL 18 + pgvector database from `000`
through the current migration and checks contract/schema alignment.

## Repository map

| Path | Purpose |
|---|---|
| `src/app/` | AppShell, context, domain types, registries and client modules |
| `src/app.frontend.facets.molstar-rdkit.editable/` | integrated browser application and scientific facets |
| `src/chemistry.backend.perception.rdkit-wasm.editable/` | shared RDKit-JS chemistry substrate |
| `backend/dirac_app/` | command registry, dispatcher and application handlers |
| `backend/motif/` | governed molecular-design and closed-loop scientific workflows |
| `backend/execution_control/` | allocation, leases, retries, reconciliation and completion |
| `backend/executors/` | local and Kubernetes/Kueue execution adapters |
| `backend/db/` | PostgreSQL schema, migrations and database gates |
| `contracts/` | canonical Object, Command, Method and Error schemas plus generated types |
| `python/` | dependency-light Python SDK, CLI and MCP adapter |
| `docs/product/` | product, HCI and Motif specifications; Motif v3 is the current baseline |
| `docs/adr/` | accepted architecture decisions |
| `docs/archive/` | dated evidence and superseded plans; never current guidance |
| `deploy/` | current service topology and deployment assets |

The upstream mol\* engine remains in the tree under explicitly named vendored/read-only
areas. [Source ownership](src/VENDORED.md) distinguishes upstream substrate from Dirac
code.

## Documentation

- [Construction status](STATUS.md) — what is connected, measured or still partial
- [Architecture](ARCHITECTURE.md) — current technical boundaries
- [Product architecture](docs/product/PRODUCT_ARCHITECTURE.md) — Workspaces, Views and intent model
- [Domain model](docs/product/DOMAIN_MODEL.md) and [Command model](docs/product/COMMAND_MODEL.md)
- [Human interface charter](docs/product/HUMAN_INTERFACE_CHARTER.md) and [HCI contracts](docs/product/HCI_SEMANTIC_CONTRACTS.md)
- [Program reference jobs](docs/product/DIRAC_PROGRAM_REFERENCE_JOBS_SPEC.md)
- [Motif v3](docs/product/motif-v3/README.md) — current scientific-compute system of record
- [Documentation map](docs/README.md)

## Scientific honesty

Dirac separates workflow capability from scientific validation. A registered Method or
passing transport parity test proves that computation is routed and recorded correctly;
it does not by itself validate a force field, model, dataset or prospective prediction.
Methods must expose their runtime identity, units, refusal conditions and provenance, and
the UI must not upgrade planned or partial capability into scientific readiness.

## Acknowledgments and license

Dirac is built on [mol\*](https://github.com/molstar/molstar) and
[RDKit](https://github.com/rdkit/rdkit). See [LICENSE](LICENSE) for the MIT license;
the bundled RDKit-WASM retains its BSD-3-Clause terms.
