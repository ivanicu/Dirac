# ADR-001 — Dirac remains the sole semantic control plane

**Status:** Accepted by this specification
**Decision type:** Architectural invariant

## Context

Motif requires model training, generation, docking, MD/FEP, batch screening and distributed execution. Existing ecosystems such as Ray, MLflow, Kubeflow, Slurm and Kubernetes offer valuable implementation capabilities. They also bring their own job IDs, registries, lifecycle states and APIs.

Dirac already owns canonical Commands, Methods, Jobs, Artifacts, ObjectRefs, actors, authorization, command traces, scientific context and PostgreSQL lineage. Allowing a framework-native control surface to become co-authoritative would split scientific identity and make replay dependent on the framework that happened to execute a workload.

## Decision

Dirac owns:

- application intent and actor;
- scientific Method identity and schemas;
- Run DAG and Job identity;
- dataset/model/policy release;
- admission, project quota and approval;
- Artifact identity and provenance;
- reviewed evidence and decisions.

Schedulers and ML frameworks are adapters or mirrors. Their IDs are stored only on Allocation/Attempt records. Public clients never need them.

## Consequences

Positive:

- local and HPC deployments use one semantic API;
- models/schedulers can be replaced without losing history;
- CLI/MCP/UI do not duplicate scientific logic;
- governance and evidence remain consistent.

Costs:

- Dirac must implement a real scheduler protocol, reconciler and release registry rather than delegating everything to one framework;
- adapters require explicit translation and tests.

## Rejected alternatives

1. **Ray as the global job system:** rejected because framework task identity is not a durable scientific contract and cluster multi-tenancy/admission remains a separate concern.
2. **MLflow as model authority:** rejected because experiment tracking is not sufficient for assay-resolved data, policy releases, approval or complete execution identity.
3. **Kubeflow as product workflow:** rejected because Kubernetes CRDs should not leak into Command/Method contracts.
4. **Slurm IDs as Job IDs:** rejected because scheduler resubmission/preemption would change scientific identity.
