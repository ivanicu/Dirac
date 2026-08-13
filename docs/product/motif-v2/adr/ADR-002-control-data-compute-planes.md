# ADR-002 — Separate control, artifact/data and compute planes

**Status:** Accepted by this specification

## Context

The current repository has sound content-addressed Artifact identity but stores bytes through a path suited to small results. Motif will produce large Parquet matrices, SDF ensembles, model checkpoints, Zarr fields and MD trajectories. At scale, workers may live on Slurm, Kubernetes or restricted HPC sites.

## Decision

### Control plane

PostgreSQL and Dirac services own identities, states, relations, governance, summaries and read-model sources.

### Artifact/data plane

Immutable content-addressed bytes live in a backend selected by deployment profile:

- local POSIX/NVMe CAS;
- S3-compatible/Ceph object storage;
- HPC shared filesystem/site cache;
- tiny PostgreSQL inline compatibility tier.

PostgreSQL stores digest, location, size, metadata and references.

### Compute plane

Workers receive a bounded `ExecutionRequest`, authorized Artifact capabilities and short-lived credentials. They cannot write domain tables directly.

## Completion rule

A Job is successful only after required Artifacts are verified and linked in an atomic terminal commit guarded by the current fencing token.

## Consequences

- large outputs stream without process-memory or database-byte limits;
- workers can operate at remote/air-gapped sites;
- Artifact integrity is independent of scheduler;
- database backups no longer pretend to contain petabyte-scale scientific bytes and must be paired with Artifact restore procedures.
