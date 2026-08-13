# Dirac Repository Audit — 2026-08-12 Baseline

**Normative baseline:** `9d38ca434c9404843640ed5d7a4cb5d0a315d135`
**Architecture-twin source snapshot:** parent `39c9159`
**Audit goal:** determine what the current repository can actually support, what will fail under Motif, and the smallest architecture changes that preserve the good substrate while making local and HPC execution real.

---

## 1. Executive verdict

Dirac is no longer merely a browser molecule viewer, even though the root README still presents it roughly that way. The repository already contains the difficult beginnings of a scientific operating-system substrate:

- canonical JSON contracts and generated Python/TypeScript clients;
- a transport-neutral `CommandDispatcher`;
- a Method catalog with input/output validation;
- an `InvocationService` that centralizes cache, Job, Artifact and result-envelope semantics;
- durable PostgreSQL Jobs, Artifacts, command traces and a generic provenance graph;
- Mission / Run / Job identity separation;
- a single AppShell, scientific context store and Mol* scene;
- explicit local/remote security boundaries and quotas;
- an architecture twin and architecture fitness tests.

That is the right foundation. Replacing it with Ray, MLflow, Kubeflow, Airflow or a vendor “AI platform” would be an architectural regression.

The hard truth is that the current substrate is **not yet a durable ML/HPC execution plane**. It can support synchronous and small reconnectable scientific methods. It cannot yet truthfully promise recoverable training, distributed docking, FEP campaigns, preemptible GPU work, streamed multi-gigabyte artifacts or ten-thousand-GPU orchestration.

The gap is not “add a queue.” The missing layer is a precise protocol joining:

```text
Command / Method semantics
    -> compiled scientific DAG
    -> durable Job identity
    -> scheduler allocation
    -> shard and attempt execution
    -> lease / fencing / checkpoint
    -> content-addressed output commit
    -> evidence and decision lineage
```

This bundle specifies that layer without creating a second Dirac.

---

## 2. Repository state that is genuinely strong

### 2.1 Canonical contracts are already the center of gravity

`contracts/` is already used to define commands, methods, object kinds, relations and generated clients. That is a much better long-term base than hand-maintained REST models or framework-native task signatures.

**Keep:**

- JSON Schema as the public semantic contract;
- generated Python and TypeScript types;
- `ObjectRef = {kind, id}` as the cross-layer identity primitive;
- explicit error vocabulary;
- Command and Method versioning as separate concepts.

**Extend, do not fork:** Motif contracts belong under existing contract roots and must be consumed through the same generators and fitness gates.

### 2.2 Command and Method separation is correct

The repository distinguishes:

- **Command:** application intent, actor, authorization, trace, idempotency and business decision;
- **Method:** scientific computation, strict input/output, cache identity, execution and artifacts.

That separation is exactly what an AI-native scientific system needs. A human, an agent, the UI and a CLI can all issue the same Command; the Command can compile one or more Methods without exposing model-framework details.

### 2.3 Invocation is being centralized rather than copied across transports

`backend/invocation.py` explicitly removes orchestration from the HTTP route and puts it in a transport-neutral service. It validates, resolves versions, estimates cost, opens a Job, dispatches execution, validates output, stores Artifacts, updates cache and normalizes provenance.

This is the correct semantic choke point. It should become more durable, not be bypassed.

### 2.4 Artifact identity is conceptually correct

Migration `013_artifacts_first_class.sql` separates:

- `app.blob`: content-addressed bytes;
- `app.artifact`: those bytes in a semantic role;
- `app.job_artifact`: which Job produced which Artifact.

That is a sound model and should survive local POSIX, S3/Ceph and offline-HPC storage. The storage implementation must change; the identity model should not.

### 2.5 Cancellation semantics are honest

Migration `014_job_store_contract.sql` correctly distinguishes “cancellation requested” from “executor actually interrupted the work.” This is rare and good. The current implementation is incomplete, but the semantics are not lying.

### 2.6 The product shell is already converging on one scientific context

The repository has one AppShell registry, one `ScientificContextStore`, one `DiracClient`, one Runs surface and one persistent Mol* scene. Motif should enter `design.generate`, `campaigns.landscape` and `campaigns.optimize`; it must not create a second router or visualization universe.

---

## 3. Maturity snapshot

The architecture twin generated on 2026-08-12 reports, for its parent snapshot:

- 3,712 nodes and 6,434 edges in the architecture model;
- 56 database tables, 35 database types, 13 views and 20 migrations;
- 18 Commands, 36 ObjectKinds, 24 RelationKinds and 12 scientific Methods;
- 8 workspaces and 30 declared views;
- 1,115 Jobs: 825 done, 29 cancelled and 261 failed;
- 79 Artifacts and only 3 cached results;
- 1,106 command traces;
- product implementation marked partial: 7 of 30 views implemented.

The twin itself classifies the system around an L3 substrate: it can describe current structure and state, but cannot yet predict unseen latency/failure, replay all execution inputs or close the optimization loop.

The numbers matter less than the shape: **the control skeleton exists; the scientific product and durable execution depth do not.**

---

## 4. P0 blockers

### P0-01 — Canonical vocabulary and PostgreSQL vocabulary have drifted

Migration `015_domain_mission_run.sql` creates PostgreSQL enums with 30 object kinds and 17 relation kinds. The current canonical contracts expose 36 object kinds and 24 relations after adding medicinal-chemistry identity terms such as:

```text
compound_form, batch, sample, formulation, quality_release, protocol
has_form, produced_as, sampled_from, formulated_as,
released_by, assayed_under, has_measurement
```

Generated clients can therefore emit identities that PostgreSQL cannot store. This is not documentation drift; it is a runtime integrity failure waiting to happen.

**Required action:**

1. migration 020 appends the missing enum values immediately;
2. CI compares canonical contract vocabularies to live database vocabularies;
3. before rapid third-party extension, migrate extensible object/relation vocabularies away from hard PostgreSQL ENUMs toward registry tables plus FKs. Actor and lifecycle enums may remain PostgreSQL ENUMs because they are intentionally closed.

### P0-02 — Command contract validation must be fail-closed everywhere

The Method catalog refuses to run if `jsonschema` is unavailable. The Command registry path has historically tolerated import failure by treating validation errors as empty. A production control boundary may never interpret “validator unavailable” as “valid.”

**Required action:**

- one shared validation module;
- `Draft202012Validator` plus `FormatChecker`;
- startup self-test that loads every schema, validates positive/negative fixtures and aborts service start on failure;
- no `except ImportError: errors = []` or equivalent anywhere in Command/Method paths.

### P0-03 — Current Method version is not a complete computational identity

`InvocationService` documentation equates the registry digest with “identical code.” For ML and physics, identical Python source does not imply identical science. Results can change with checkpoint, featurizer, calibration, policy, container, CUDA toolkit, kernel, force field, parameter file and numerical mode.

**Required composite execution digest:**

```text
sha256(
  method_descriptor_digest
  || handler_source_digest
  || repository_commit
  || container_image_digest
  || dependency_lock_digest
  || model_checkpoint_digest_set
  || featurizer_digest
  || dataset_snapshot_digest_set
  || calibration_digest
  || policy_digest
  || forcefield_or_parameter_digest
  || hardware_compatibility_profile
  || numeric_mode
)
```

Absent components are explicit `null`, not omitted ambiguously. Cache identity must use this digest, canonical inputs and deterministic seed material.

### P0-04 — Durable Job identity is followed by process-local execution ownership

`InvocationService.submit()` opens a durable Job and then stores a `Future` in process memory. If the process exits after the row is created but before completion, the database can retain a queued/running Job with no live executor ownership. The current worker reaping logic is PID/age-oriented rather than lease/fencing-oriented.

**Required action:** introduce `job_attempt`, `execution_allocation`, durable lease expiry, monotonic fencing tokens and a recovery reconciler. A Job is semantic identity; an Attempt owns execution for a bounded lease.

### P0-05 — `RemoteExecutor` is only a callback seam, not a scheduler protocol

The current `RemoteExecutor` correctly avoids leaking Slurm/Kubernetes details into `InvocationService`, but it only wraps `submit`, optional `execute` and optional `cancel` callbacks. It has no contract for:

- resource requests;
- scheduler ID and site identity;
- allocation state;
- attempts and preemption;
- lease/heartbeat;
- checkpoint location;
- output manifests;
- fencing;
- retries;
- data locality;
- array or distributed topology.

**Required action:** preserve the seam but replace callback-only semantics with an explicit `SchedulerAdapter` + `ExecutionRequest` + `ExecutionEvent` protocol.

### P0-06 — Running cancellation is cooperative only in prose, not end to end

Thread/process `Future.cancel()` only cancels queued work. `InvocationContext.check_budget()` can detect deadlines only when handlers call it, and there is no injected cancellation token linked to durable `cancel_requested_at`.

**Required action:**

- inject a `CancellationToken` checked at declared safe points;
- adapters translate cancel to process signal, Slurm `scancel`, Kubernetes delete/suspend or site-specific action;
- workers checkpoint before cooperative termination when policy permits;
- force kill after grace period is a separate, audited transition;
- public Job state becomes `cancelled` only when interruption is confirmed.

### P0-07 — Artifact production is memory-bound and PostgreSQL-bound

`HandlerResult.artifacts` is `list[tuple[str, bytes]]`. That forces the complete Artifact into process memory before registration. The current authoritative blob table stores bytes in PostgreSQL. This works for small cube files and fails for:

- multi-gigabyte Parquet prediction matrices;
- MD trajectories;
- model checkpoints;
- distributed checkpoint shards;
- large pose ensembles;
- Zarr field stores;
- thousands of concurrent outputs.

**Required action:**

- inject `ArtifactReader`, `ArtifactWriter` and `CheckpointWriter` capabilities;
- stream or multipart-upload bytes while hashing;
- keep Artifact identity in PostgreSQL;
- local backend: POSIX/NVMe CAS;
- scale backend: S3-compatible/Ceph object storage;
- PostgreSQL stores digest, backend, immutable locator, size and metadata, not bulk bytes;
- preserve an inline-byte threshold only for tiny results.

### P0-08 — Terminal Job completion is not a reliable commit barrier

The current invocation path intentionally swallows several ledger and Artifact-link failures so that “observability cannot break science.” That principle is acceptable for optional telemetry and unacceptable for durable expensive computation. A Job must never become `done` while required Artifacts are absent, unverified or unlinked.

**Required action:**

A terminal success commit requires:

1. output manifest validated against Method contract;
2. every required Artifact uploaded;
3. every digest verified;
4. Artifact rows and Job links committed;
5. result summary written;
6. Attempt fencing token still current;
7. Job transitioned atomically to `done`;
8. outbox event written in the same transaction.

Optional telemetry may fail open. Scientific completion may not.

### P0-09 — Mission → Run → Job is not yet a DAG execution model

`app.run_job` stores only `ordinal` and `purpose`. Motif needs conditional branches, fan-out/fan-in, adaptive escalation, retries, barriers, approval waits and partial recomputation. An ordinal list cannot represent those semantics.

**Required action:** add `run_step` and `run_step_edge`. A Step compiles to one or more Jobs; a Job may map to an Allocation with many Shards and Attempts.

### P0-10 — Medicinal-chemistry evidence schema is a useful declaration, not a sufficient scientific contract

The newly added schema correctly asserts that a designed molecule is not an assayed sample. However, it is not yet adequate for training data or regulated scientific provenance:

- generic refs allow arbitrary string `kind` rather than canonical/kind-specific constraints;
- batch requires `actual_yield`, which does not represent pending/failed synthesis and does not distinguish mass from percent yield;
- sample always requires a release reference, excluding provisional or failed-QC material;
- measurement requires `value` and `unit` even when qualifier is `not_tested`;
- no conditional schema for `<`, `>`, censored intervals or missing reasons;
- endpoint, protocol, species/system, readout, replicate, LLOQ/ULOQ, uncertainty and raw-vs-derived semantics are absent;
- `format: date-time` is not enforced without `FormatChecker`;
- new object kinds are not yet backed by durable tables/read models.

**Required action:** land the `measurement-v2` contract in this bundle, plus endpoint/protocol tables and assay-aware snapshot extraction.

---

## 5. P1 gaps that block a credible closed loop

### P1-01 — Dataset snapshot is not a first-class immutable release

CSV directories, DataFrames and query results are not sufficient identities. A dataset snapshot must freeze selection query, identity policy, endpoint/protocol versions, units, transforms, censoring, QC/exclusion, split manifest and row lineage.

### P1-02 — Model and policy releases are not first-class governed objects

Checkpoint folders and framework experiment directories must not be production truth. Model, generation, acquisition, calibration and fidelity policies all need immutable releases, validation artifacts, lifecycle state and explicit promotion.

### P1-03 — No endpoint registry with assay semantics

An endpoint is not a column name. It binds biological system, assay/protocol version, readout, direction, units, label transform, censoring policy and intended domain. Without that registry, “multi-task learning” silently mixes incompatible labels.

### P1-04 — No scientific outbox/event projection boundary

Jobs, Artifacts, read models and notifications need reliable projection updates. Writing directly to UI projection tables from workers will create coupling and partial states. Use a transactional outbox from the control-plane transaction; projectors are idempotent.

### P1-05 — Product views are mostly shells

The correct views are declared, but `design.generate`, `campaigns.landscape` and `campaigns.optimize` are not yet the evidence-aware closed-loop product. They need contract-driven read models, not large browser arrays or ad hoc polling.

### P1-06 — Scientific runtime is not frozen as a product artifact

The lightweight SDK should remain dependency-minimal. Scientific runtimes belong in separate pinned OCI/Apptainer images with lockfiles, SBOM, signatures and license manifests.

---

## 6. Scale-specific gaps

### 6.1 PostgreSQL cannot receive a heartbeat from every GPU

At 10,000 GPUs, a five-second heartbeat per worker is 2,000 writes per second before progress, logs and task results. The database should receive aggregated Allocation/Shard progress from an allocation leader or site agent, not per-rank chatter.

### 6.2 A Job per molecule is an anti-pattern

Fifty thousand proposals, five thousand predictions and thousands of docking/FEP tasks must be represented by immutable shard manifests and result manifests. One PostgreSQL Job per molecule creates excessive rows, transactions, scheduler submissions and UI noise.

### 6.3 Scheduler identity and scientific identity must remain distinct

A Slurm array ID, Kubernetes JobSet UID or Ray task ID is not a Dirac Job ID. Scheduler IDs are Attempt/Allocation metadata. Dirac owns scientific identity and lineage.

### 6.4 Data locality and site policy are first-class

Large HPC environments may be air-gapped, prohibit outbound object-store access, require shared filesystems, impose module/Apptainer policies or partition by data sensitivity. The protocol must support:

- pre-staged immutable input bundles;
- site-local artifact proxy/cache;
- relay of signed result manifests;
- S3/Ceph or POSIX backend selection;
- scheduler account/partition/QOS;
- no worker direct database credentials.

---

## 7. Scorecard

Scores are architecture judgments, not repository facts.

| Area | Current maturity | What that means |
|---|---:|---|
| Canonical semantic substrate | 8/10 | unusually strong; preserve it |
| Command/Method boundary | 8/10 | correct abstraction, schemas need hardening |
| Local small-method execution | 6/10 | workable for current fields/torsion style jobs |
| Durable expensive execution | 3/10 | identity exists; leases/attempts/commit barrier do not |
| Artifact identity | 7/10 | model is right; storage path is not scale-ready |
| Scientific data semantics | 4/10 | direction is right; endpoint/protocol/censoring incomplete |
| Dataset/model governance | 2/10 | largely specified, not implemented |
| Closed-loop product | 2/10 | views and concepts exist; cycle is not operational |
| HPC / 1,000–10,000 GPU control | 1/10 | only an adapter seam exists |
| Local Motif prototype potential | 7/10 after P0 | the current substrate can support an excellent appliance once execution and release seams land |

---

## 8. Architectural decisions from the audit

1. **Dirac remains the only semantic control plane.**
2. **PostgreSQL remains the authority for identities, state, lineage and governance, not bulk bytes.**
3. **Scheduler frameworks are adapters behind Dirac, not public APIs or truth stores.**
4. **The same Command/Method/Artifact semantics run locally and at HPC scale.**
5. **A local RTX 5080 appliance is not a toy branch; it is deployment profile `local_appliance_v1`.**
6. **The first credible Motif is a complete closed loop with strong simple baselines, not a showcase of the largest generator.**
7. **No terminal scientific success without verified required Artifacts and current fencing token.**
8. **No prediction/measurement/evidence/decision collapse in schemas or UI.**
9. **No model release without frozen data, calibration, applicability domain, validation and approver.**
10. **No scale claim without fault-injection and scheduler-scale evidence.**

---

## 9. Immediate repository corrections before Motif feature work

The first merge sequence should be deliberately unglamorous:

```text
contract vocabulary alignment
-> shared fail-closed schema validator
-> composite execution digest
-> streaming artifact capabilities
-> Job Attempt / Allocation / Lease / fencing
-> reliable terminal commit + outbox
-> Run Step DAG
-> local process/GPU worker appliance
```

Starting with a pocket-conditioned generator before this sequence would create a powerful model attached to a dishonest operating system. That would demo well and fail exactly when a real campaign becomes expensive.
