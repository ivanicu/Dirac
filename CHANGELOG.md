# Changelog

This file records product-level changes after Dirac became a distinct application. The
repository inherited mol\* history and currently has no reliable release-tag series;
`package.json` is the package version authority and Git is the detailed change ledger.

## Unreleased

### Product surfaces

- Added an independently built Discovery Lab that provides shared navigation between the
  focused FEP and Field workbenches without replacing the full product shell.
- Added recoverable FEP campaign preparation, exact pose/mapping review, scientific
  generation binding and governed OpenFE run qualification.
- Added linked parent/proposal field comparison across 2D, 3D, receptor-pocket, MEP and
  MLP views.

### Application platform

- Established one canonical domain vocabulary, semantic Command registry and generated
  Python/TypeScript contracts.
- Added a transport-neutral dispatcher and one Method invocation path shared by HTTP,
  Python SDK, CLI, MCP and the browser.
- Added durable Jobs, content-addressed Artifacts, provenance, command observations,
  attention and method-current caches in PostgreSQL.
- Added local, process, GPU and Kubernetes/Kueue execution adapters with fenced attempt,
  lease, retry and completion semantics.

### Product operating model

- Made Program the durable root for objectives, hypotheses, compounds, portfolios,
  decisions, work packages/items, evidence and lineage.
- Added an 8-Workspace / 30-View AppShell with one ScientificContextStore and one
  persistent mol\* SceneService.
- Connected Program, molecular design, structure, campaign, synthesis, experiment,
  knowledge and compute projections without presenting the remaining shells as shipped
  scientific capability.
- Added typed HCI contracts for context, actions, work graphs, handoffs and projection
  truthfulness.

### Scientific workflows

- Integrated RDKit-JS chemistry, 2D/3D ligand synchronization, properties,
  pharmacophores, field wells, surface MEP, torsion and explicit evidence boundaries.
- Added Motif contracts and implementations for dataset snapshots, model training and
  calibration, proposal generation, ranking, structure work, MD, RBFE, OpenFE and
  governed closed-loop execution.
- Preserved scientific runtime identity, units, refusal conditions and artifacts through
  the same Job/provenance path.

### Operations and verification

- Standardized the current local topology on web `:1360`, unified application service
  `:8901`, and read-only operations `:1355`; the standalone `:8902` service is retired.
- Added source-derived architecture-twin generation and drift checks.
- Added gates for types, production builds, design invariants, documentation facts,
  contracts, migrations, portability, layering, physics protections, security and
  architecture coherence.
- Added a remote fail-closed security profile while retaining the explicitly
  unauthenticated local/LAN development profile.
- Added tenant-scoped Job idempotency, durable dispatch recovery and fenced terminal
  publication for long campaign preparation and execution workflows.
- Reworked repository metadata around product workflows, explicit architecture boundaries,
  source-derived status, contribution guidance, private security reporting and third-party
  attribution.

## Initial Dirac application — 2026-08-10

- Forked the application surface from mol\* while preserving upstream engine history.
- Added the first integrated molecular workbench with RDKit-JS chemistry perception,
  ligand depiction, pharmacophore overlays and the initial Fields backend.
- Adopted the single-tree `main` development model and Dirac visual identity.
