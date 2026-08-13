# Dirac Motif v3 — scientific-compute system of record

Status: implementation baseline, 2026-08-13.

This directory replaces the monolithic “fidelity pipeline” interpretation of Motif.
The implementation is a scientific object graph plus an action planner. F0–F5 remain
human-readable method-cost labels; they are not candidate states and never imply that
a later method must run.

## Non-negotiable claim boundary

Motif can produce governed computational evidence. It cannot turn a successful process,
a docking score, a stable-looking trajectory, a small calibration set, or an RBFE
bootstrap into a validated drug claim. Every claim must name its subject, physical or
assay condition, MethodManifest, QualityAssessment, dependencies, applicability domain,
scientific state and claim-eligibility code.

## Dirac constraints that Motif must obey

1. Dirac Command and Method registries are the only public semantic surface.
2. PostgreSQL is the durable authority for Jobs, Attempts, leases, evidence and actions.
3. CAS Artifacts are immutable; a database row stores identity and lineage, not large
   scientific payloads.
4. Execution is at-least-once. A LogicalJob has many Attempts, but only one fenced,
   terminal ArtifactCommit.
5. Local CPU, local GPU and Kubernetes/Kueue work all need ResourceBroker leases.
   Kueue admission does not account for host VRAM, local scratch or campaign budget.
6. Runtime identity includes source, schemas, parameters, image or local runtime,
   external binaries, driver/CUDA where applicable, thread policy and numeric contract.
7. Numerical reproducibility is tolerance- or distribution-based unless a MethodManifest
   explicitly proves bitwise determinism.
8. Current hardware is a single RTX 5080 16 GiB, Ryzen 9 9900X and at least 64 GiB RAM.
   The one-GPU resource is globally serialized; no method owns a hidden GPU queue.
9. Large trajectory/scratch data has bounded retention and cannot silently consume the
   root filesystem.
10. Unsupported chemistry is method-scoped. It does not globally reject a ChemicalEntity.

## Implemented source of truth

- Machine contracts: `contracts/domain/motif/*.schema.json`.
- Database: `backend/db/migrations/037_motif_scientific_semantics.sql`.
- Semantics: `backend/motif/semantics.py`.
- Action planning: `backend/motif/action_planner.py`.
- Model release gates: `backend/motif/model_gates.py`.
- Resource leases: `backend/motif/resource_broker.py`.
- Terminal commit: `backend/execution_control/attempt_store.py`.
- Artifact lifecycle and safe archive intake: `backend/motif/artifact_lifecycle.py`,
  `backend/motif/safe_inputs.py`.
- Measurement, prepared receptor, torsion, observability and benchmark contracts:
  `contracts/domain/motif/`.
- Official OpenFE network planner: `backend/motif/openfe_network_planner.py`.
- OpenFE edge execution: `backend/motif/openfe_runner.py`.
- Covariance-aware RBFE assembly: `backend/motif/rbfe.py`.
- Public commands: `motif.plan`, `motif.validate`, `motif.explain`.

## Correct delivery order

| Phase | Deliverable | Gate |
|---|---|---|
| −1 | semantic foundation | object graph, orthogonal state, typed evidence, MethodManifest |
| 0 | execution/storage | LogicalJob/Attempt, atomic leases, fenced commit, artifact lifecycle |
| 1 | thin loop | dataset snapshot, transparent baseline, typed prediction, deterministic Pareto |
| 2 | structure | coupled chemical states, receptor prep, conformer and docking validation |
| 3 | MD | frozen parameterization, analysis protocol, repeat/negative-evidence semantics |
| 4 | RBFE | pilot edge → two legs → repeats → connected redundant network |
| 5 | action planner | learned outcome/cost models only after trustworthy telemetry exists |
| 6 | Bayesian acquisition | executable only with a validated/promoted model and explicit posterior contract |

## Documents

- [SEMANTIC_FOUNDATION.md](SEMANTIC_FOUNDATION.md)
- [PHYSICAL_METHOD_PROTOCOLS.md](PHYSICAL_METHOD_PROTOCOLS.md)
- [EXECUTION_AND_OPERATIONS.md](EXECUTION_AND_OPERATIONS.md)
- [TEST_AND_TRACEABILITY.md](TEST_AND_TRACEABILITY.md)
