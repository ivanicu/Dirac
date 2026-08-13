# Dirac Motif v2 — Implementation-Grade Specification Bundle

> Superseded by [`../motif-v3/README.md`](../motif-v3/README.md). This directory is a
> versioned design record retained for provenance, not current implementation guidance.

This bundle converts the existing Motif product/science specification into a landable software and execution specification for the current Dirac repository.

## Baseline

- Dirac repository baseline: `main` at commit `9d38ca434c9404843640ed5d7a4cb5d0a315d135` (2026-08-12 review).
- Embedded architecture twin in that commit was generated from parent `39c9159`; the distinction is intentional and recorded in the audit.
- Product/science source in this repository: `../MOTIF_ML_DRUG_DESIGN_SPEC.md`.
- Target local appliance: Ryzen 9 9900X + RTX 5080 16 GB + 64 GB RAM minimum.
- Target scale-out: Slurm or Kubernetes/Kueue/JobSet, from one GPU to thousands or tens of thousands, without changing public Command/Method semantics.

## Read order

1. `DIRAC_MOTIF_V2_IMPLEMENTABLE_SPEC.md` — normative integrated specification.
2. `docs/REPOSITORY_AUDIT.md` — evidence-based review of the current repository.
3. `docs/SOURCE_EVIDENCE.md` — reviewed baselines, evidence classes and validation boundary.
4. `IMPLEMENTATION_BACKLOG.yaml` — dependency-ordered engineering backlog with acceptance gates.
5. `TEST_AND_BENCHMARK_PLAN.md` — software, scientific and scale verification.
6. `OPEN_SOURCE_ECOSYSTEM.md` — dependency placement, licensing and isolation rules.
7. Repository root `contracts/` — canonical JSON Schemas for the execution and scientific-data seams.
8. Repository root `backend/db/migrations/020_*.sql` through `028_*.sql` — tested sequential Motif/Program migrations.
9. `config/` — local, Slurm and Kubernetes runtime profiles.
10. `adr/` — architecture decisions that prevent a second control plane.

## Status vocabulary

- **CURRENT**: observed in the reviewed repository.
- **P0 REQUIRED**: must land before expensive or distributed Motif workloads are considered durable.
- **V1 REQUIRED**: required for the local closed-loop product slice.
- **SCALE REQUIRED**: required before multi-node / large-cluster operation.
- **OPTIONAL ADAPTER**: replaceable implementation behind a Dirac-owned interface.

## Validation

The repository gates validate JSON/YAML, generated clients, public registry drift,
contract-to-database vocabulary alignment, clean migrations and migration hashes.

## Repository integration status — 2026-08-12

Implemented and verified in source:

- fail-closed contracts and Motif v2 schemas;
- migrations 020–028 with canonical Program/Work Item/entity identity;
- composite execution identity and cache v2 provenance;
- streaming Local CAS, range reads and reliable output completion;
- Run Steps, Attempts, leases, fencing, outbox, retry and reconciliation primitives;
- fixed-entrypoint local CPU/GPU scheduler adapters with fail-closed admission;
- immutable dataset snapshots and exact/series/scaffold/protocol/time leakage diagnostics;
- a governed predictor mesh: label-free Morgan + RDKit descriptors, ridge/1NN,
  random forest, XGBoost, interval-censored Tobit, pairwise ranking and Chemprop
  D-MPNN ensemble, with conditional conformal calibration, compact applicability
  domain, bootstrap confidence intervals and complete specification curves;
- RDKit local-edit and reaction enumeration with full Proposal provenance;
- deterministic constrained Pareto portfolio partitioning without a hidden score,
  plus BoTorch qLogEHVI, variance-per-cost information value and sensitivity analysis;
- ETKDGv3 conformer ensembles, a real AutoDock Vina pose baseline, restartable
  OpenMM MD, and RBFE network planning/uncertainty aggregation with partial-edge
  failure semantics;
- fail-closed deployment manifests for optional DiffDock and OpenFE engines; a
  mutable image, missing license/checkpoint digest or absent golden fixture cannot
  be advertised as an executable adapter;
- public `dataset.snapshot.create`, `model.train`, `proposal.generate` and
  `campaign.rank` Commands.
- governed `endpoint.register`, `objective.save` and `result.ingest` Commands with
  canonical document digests, actor binding, atomic Artifact/outbox persistence and
  content-idempotent replay;
- governed `policy.release.register`; Objective persistence refuses missing or
  wrong-kind policies, incoherent Program/Campaign/Target references and endpoint
  direction drift;
- a protocol-resolved measurement v2 ledger that retains missing, censored and QC
  failure semantics instead of coercing them into numeric results.
- fail-closed completion projection from Dataset Snapshot and Model Train Jobs into
  governed `app.dataset_snapshot` and `meta.model_release` records, with exact
  training-data digest binding, Artifact lineage and transactional outbox events;
- truthful model runtime identity: a pinned container digest or an immutable local
  Python/platform/distribution manifest Artifact, never an invented container digest.

Verified against the live Dirac control plane:

- a recoverable pre-upgrade PostgreSQL backup was created and validated with
  `pg_restore --list`;
- migrations 020–028 were applied to the live `dirac` database and all 29
  migration hashes pass the integrity check;
- the current 27 compute Methods were registered in `meta.method` through the canonical
  registry path;
- real CPU Jobs completed through Command → Invocation → Executor → Job → Artifact
  for dataset snapshotting, baseline training, proposal generation and constrained
  campaign ranking;
- Attempt leasing was integration-tested on PostgreSQL for single-owner claim,
  lease takeover, monotonic fencing, stale-completion rejection and idempotent
  terminal completion.
- live synthetic-control-plane smoke records passed `endpoint.register`,
  `objective.save` and `result.ingest` twice each: first-write creation, second-write
  deduplication, one outbox event per aggregate and durable command traces.
- live synthetic release smoke produced valid Dataset Snapshot
  `83c18208-aa6a-4080-9147-09a4e75ecbdc` (four required Artifacts) and candidate
  Model Release `8db47cac-0953-4026-a782-fa04544f5bb9` (checkpoint, validation and
  local-runtime Artifacts), then replayed both Commands onto the same governed IDs;
- a full public Command → Job → Artifact → governance smoke produced valid Dataset
  Snapshot `35be1d08-6b27-43ea-b9c5-be0412ea7dde` and predictor-mesh candidate
  Model Release `873a5bfe-539b-4a85-9691-508f203049c9`, including two Chemprop members;
  the full model specification
  curve is persisted, and its synthetic numbers are not scientific validation;
- pueue GPU task 845 trained and predicted with a two-member Chemprop D-MPNN on an
  NVIDIA GeForce RTX 5080; task 847 ran OpenMM CUDA, wrote a checkpoint and resumed it;
- AutoDock Vina executed an actual receptor/grid/ligand pose search and returned a
  pose ensemble and affinity report; OpenMM Reference checkpoint/restart and RBFE
  complete/partial network behavior are covered by tests;
- isolated PostgreSQL tests prove that changed training rows are refused when their
  canonical digest does not match the linked Dataset Snapshot, leaving no model row.

Not represented as complete:

- DiffDock inference and OpenFE edge simulation remain optional isolated adapters;
  their core fail-closed manifest gates are landed, but no checkpoint/image/golden
  fixture has been supplied, so Motif does not fabricate readiness or RBFE values;
- Slurm, Kubernetes/Kueue and prospective wet-lab validation retain their external
  infrastructure and evidence gates;
- reboot/chaos drills and the eight-hour appliance soak have not run.
