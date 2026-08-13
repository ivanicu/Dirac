# ADR-003 — Ray may run inside an Allocation, never above Dirac

**Status:** Accepted with constraints

## Context

Ray is useful for dynamic Python task graphs, actor-based model serving and data-parallel execution. It is tempting to map every Dirac Job to a Ray Job and make Ray the distributed backend.

That would produce two overlapping control planes: Dirac and Ray would each have retries, cancellation, tasks, status, logs and state. On Slurm, Ray itself also needs a cluster lifecycle. On Kubernetes, Kueue/KubeRay add another admission layer.

## Decision

Ray is an optional implementation inside a scheduler Allocation:

```text
Dirac Job
 -> Dirac Attempt
 -> Slurm/Kubernetes Allocation
 -> optional ephemeral Ray cluster
 -> tasks/shards
 -> Dirac output manifest
```

Rules:

- Ray cluster lifetime is bounded by one Allocation or tightly scoped execution pool;
- Dirac owns retries visible to science; internal Ray retries are bounded and summarized;
- Ray ObjectRefs never enter public contracts or PostgreSQL lineage;
- inputs/outputs are Artifacts, not persistent Ray object-store state;
- Kueue/Slurm owns capacity admission; Dirac owns project/scientific admission;
- failure translation maps Ray errors to Dirac typed failures;
- a non-Ray implementation must remain possible for every public Method.

## When Ray is justified

- dynamic intra-allocation fan-out where static arrays are inefficient;
- repeated inference with expensive shared model actors;
- data pipelines that materially benefit from Ray Data after benchmarking.

## When Ray is not justified

- simple local process pools;
- Slurm job arrays for homogeneous docking/FEP shards;
- durable model registry;
- cross-project quota/approval;
- scientific workflow truth.
