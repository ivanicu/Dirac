# Dirac Motif v2 — Source and Evidence Register

This register distinguishes observed repository facts, source-spec requirements, current external capabilities, and design inferences. It exists to prevent an architecture recommendation from being mistaken for already-landed code.

## 1. Reviewed baselines

### Product/science source

- Embedded source: `source/MOTIF_ML_DRUG_DESIGN_SPEC_v1.md`.
- Original status: proposed, implementation-ready architecture specification.
- Original baseline: Dirac repository, services and PostgreSQL schema as of 2026-08-12.
- Product thesis: a closed-loop, multi-objective, evidence-driven small-molecule design engine.

### Repository

- Public repository: `https://github.com/ivanicu/Dirac`.
- Reviewed branch/commit: `main@9d38ca434c9404843640ed5d7a4cb5d0a315d135`.
- The architecture twin embedded by that commit was generated from parent `39c9159`; repository commit and generated twin source commit are therefore recorded separately.
- The audit is a static review of the public repository and retrieved raw files. It is not a claim that migrations, services, CUDA workloads or end-to-end browser flows were executed in the author's private deployment.

## 2. Repository evidence used by the audit

The following files and schema surfaces were inspected directly:

```text
backend/invocation.py
backend/execution.py
backend/jobs.py
backend/artifacts.py
backend/catalog.py
backend/dirac_app/dispatcher.py
backend/db/migrations/013_artifacts_first_class.sql
backend/db/migrations/014_job_store_contract.sql
backend/db/migrations/015_domain_mission_run.sql
backend/db/migrations/019_remote_security_boundary.sql
contracts/domain/object-kinds.json
contracts/domain/relations.json
contracts/commands/registry.json
contracts/methods/
src/app/shell/workspace-plans.ts
src/app/shell/registries.ts
src/app/context/scientific-context-store.ts
```

### Observed strengths

- Canonical ObjectRef/Command/Method/Error/Artifact concepts are already present.
- Invocation, durable Job identity, cache, Artifact and relation concepts are already present.
- PostgreSQL is already treated as the authority for domain and execution state.
- Mission, Run and run-to-job linkage exist.
- AppShell, shared scientific context, a single client and a persistent molecular scene already exist.
- Remote security boundaries, scopes, quotas and audit concepts already exist.

### Observed gaps that motivate v2

- Database enum vocabulary lags canonical contract vocabulary.
- Command and Method validation are not yet proven to share one fail-closed validator with format checking.
- Method identity is source-centric and does not yet cover checkpoint, featurizer, calibration, container and policy identity.
- `RemoteExecutor` is a callback seam rather than a scheduler protocol.
- Submission still relies on process-memory futures for active work.
- Job ledger and Artifact bookkeeping retain best-effort paths that are unsafe for expensive work.
- Cancellation does not yet provide a durable cooperative protocol for running remote work.
- Mission/Run linkage is not yet a compiled DAG with Step, Allocation, Shard and Attempt state.
- Handler outputs are byte-buffer oriented rather than streaming large immutable artifacts.
- Dataset, model, calibration and decision-policy releases are not yet first-class governed state.

These observations are documented with the exact implications and target changes in `docs/REPOSITORY_AUDIT.md`.

## 3. External primary references

All external projects remain adapters or implementation candidates unless the normative specification explicitly marks them as core. Exact versions, commits, images, checkpoints and licenses must be frozen before release.

### Execution and scale

- PyTorch 2.13 release blog: `https://pytorch.org/blog/pytorch-2-13-release-blog/`
  - current candidate baseline reviewed on 2026-08-12;
  - torchcomms, FSDP2 communication overlap and distributed improvements are relevant, but APIs marked unstable must not leak into public Dirac contracts.
- Slurm job arrays: `https://slurm.schedmd.com/job_array.html`
  - appropriate for large homogeneous shard sets; site limits still govern actual scale.
- Kueue: `https://kueue.sigs.k8s.io/`
  - appropriate for quota/admission/topology-aware Kubernetes batch scheduling.
- Ray cluster FAQ: `https://docs.ray.io/en/latest/cluster/faq.html`
  - supports the decision to keep Ray inside an allocation rather than make it the cross-tenant Dirac authority.

### Hardware

- NVIDIA GeForce RTX 5080 specifications: `https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5080/`
- AMD Ryzen 9 9900X specifications: `https://www.amd.com/en/products/processors/desktops/ryzen/9000-series/amd-ryzen-9-9900x.html`

The local profile is designed around the actual 16 GB VRAM and 12-core/24-thread CPU envelope. It is not a claim that every optional foundation model will fit or perform well on that machine.

### Chemistry, modeling and physics

- RDKit: `https://www.rdkit.org/`
- Apache Arrow: `https://arrow.apache.org/`
- PyTorch: `https://pytorch.org/`
- Chemprop: `https://github.com/chemprop/chemprop`
- BoTorch: `https://botorch.org/`
- AutoDock Vina: `https://vina.scripps.edu/`
- DiffDock: `https://github.com/gcorso/DiffDock`
- Boltz: `https://github.com/jwohlwend/boltz`
- OpenMM: `https://openmm.org/`
- GROMACS: `https://www.gromacs.org/`
- OpenFE: `https://openfree.energy/`
- REINVENT 4: `https://github.com/MolecularAI/REINVENT4`
- AiZynthFinder: `https://github.com/MolecularAI/aizynthfinder`
- GNINA: `https://github.com/gnina/gnina`

License labels in `OPEN_SOURCE_ECOSYSTEM.md` are an engineering inventory, not legal advice. The exact distributed artifact, native dependency, model weights and dataset terms must be scanned and reviewed.

## 4. Evidence classes used in the bundle

| Label | Meaning |
|---|---|
| CURRENT | Directly observed in the reviewed repository or source specification. |
| INFERENCE | Architecture consequence derived from observed code and stated product constraints. |
| P0 REQUIRED | Required before expensive or distributed work can be called durable. |
| V1 REQUIRED | Required for the local closed-loop reference appliance. |
| SCALE REQUIRED | Required before large multi-node or multi-site use. |
| OPTIONAL ADAPTER | Replaceable open-source implementation behind a Dirac-owned interface. |
| DRAFT | Concrete proposal that still requires integration against the live repository/schema. |

## 5. Validation boundary

The bundle's static validator checks:

- JSON syntax and Draft 2020-12 schema validity;
- YAML syntax and backlog dependency integrity;
- SQL transaction and delimiter sanity;
- relative Markdown links;
- presence of all declared deliverables;
- SHA-256 manifest integrity.

It cannot substitute for:

- applying migrations 000 through 022 to a clean PostgreSQL database;
- generated Python/TypeScript type compilation;
- browser flow and accessibility testing;
- CUDA/driver/container compatibility tests on the actual RTX 5080 host;
- Slurm/Kubernetes admission, preemption and reconciliation tests;
- scientific benchmark execution;
- prospective DMTA evidence.

Those are explicit release gates, not hidden assumptions.
