# Dirac Motif v2 — Test, Benchmark and Acceptance Plan

**Goal:** demonstrate that Motif is scientifically honest, operationally durable and scale-ready. A successful UI demo is not a release test.

---

## 1. Test layers

| Layer | Purpose | Blocking |
|---|---|---|
| contract | schemas, generated clients, vocabulary | every PR |
| migration | clean/upgrade/concurrency/constraints | database PRs |
| unit | deterministic module behavior | every PR |
| property | chemical/data invariants | science PRs |
| integration | Command→Method→Job→Artifact | every capability |
| recovery/chaos | crash, stale workers, corruption | execution release |
| scientific regression | frozen outputs/tolerance | model/method release |
| retrospective evaluation | leakage/calibration/utility | model/policy promotion |
| local appliance benchmark | 9900X/5080 envelope | local V1 release |
| scheduler scale | Slurm/K8s behavior | scale release |
| prospective DMTA | real scientific advantage | production claim |

---

## 2. Required test fixtures

### F-CONTROL-001 — Minimal canonical world

Contains:

- one program, target, campaign and series;
- 20 compounds, forms, batches, samples;
- one assay, one protocol, two endpoints;
- exact, censored, missing, failed-QC and not-tested measurements;
- one objective spec;
- tiny proposal/prediction/portfolio Artifacts.

Used for schema, migration, Command/Method and read-model tests.

### F-SCIENCE-001 — Small endpoint fixture

- 500–2,000 compounds;
- temporal metadata;
- at least two chemical series;
- censored values and protocol versions;
- predefined scaffold/series/temporal splits;
- frozen descriptor/fingerprint outputs;
- no confidential data.

Used in CI-capable baseline tests.

### F-SCIENCE-002 — Medium local appliance fixture

- raw proposal budget 50,000;
- 5,000 F1 candidates;
- multi-endpoint labels;
- 256 F2 structure candidates;
- 32 F3 fields candidates;
- 24–48 portfolio capacity;
- expected resource envelope and output row counts.

Used for release benchmark on target 9900X/5080 hardware.

### F-STRUCTURE-001 — Pose golden set

- diverse receptor/ligand complexes;
- experimental structures with preparation manifests;
- symmetry-aware atom mappings;
- known unsupported cases;
- expected Vina/DiffDock adapter outputs within tolerance.

### F-EXEC-001 — Synthetic large artifacts

- 1 MiB, 1 GiB and 10+ GiB byte streams;
- deterministic content generator;
- corrupt/truncated variants;
- multipart interruption scripts.

### F-SCALE-001 — 10,000-resource simulation

No actual 10,000 GPUs required for metadata validation. Simulates:

- 10,000 ranks/tasks;
- 100,000–1,000,000 shards;
- aggregate heartbeat pages;
- retry storms;
- outbox/projector load;
- scheduler state churn;
- Artifact manifests without large payload bytes.

---

## 3. Contract tests

### CT-001 — Every schema is Draft 2020-12 valid

- load every JSON Schema;
- `Draft202012Validator.check_schema()`;
- fail on unresolved internal refs;
- fail on duplicate `$id`.

**Pass:** 100%.

### CT-002 — Fail-closed validator

- remove/disable `jsonschema` in a test image;
- start control plane;
- invoke a Command/Method.

**Pass:** service refuses startup or invocation with typed internal configuration error. It MUST NOT treat payload as valid.

### CT-003 — Format enforcement

Negative examples:

- malformed UUID;
- invalid date-time;
- mutable image tag without digest;
- malformed SHA-256;
- invalid Method ID.

**Pass:** all rejected before execution.

### CT-004 — Additional properties

Add unknown fields at every object nesting level.

**Pass:** rejected unless the schema explicitly defines an extension bag.

### CT-005 — Measurement conditional semantics

Cases:

- `equal` without value;
- `not_tested` with value;
- `<` without upper bound;
- interval lower > upper (semantic validator);
- derived value without derivation;
- normalized unit without conversion provenance.

**Pass:** invalid cases rejected; valid censored/missing cases preserved.

### CT-006 — ObjectRef kind-specific constraints

Put a `model` ref where a `compound` ref is required.

**Pass:** rejected.

### CT-007 — Generated client parity

Generate Python/TypeScript types, serialize fixtures, round-trip through server validation.

**Pass:** no field/name/enum drift.

### CT-008 — Canonical ↔ PostgreSQL vocabulary

Compare:

- `contracts/domain/object-kinds.json`;
- `contracts/domain/relations.json`;
- generated clients;
- `meta.v_object_kind_registry`;
- `meta.v_relation_kind_registry`.

**Pass:** exact set equality and reviewed ordering policy.

---

## 4. Migration tests

### DB-001 — Clean install

Apply migrations 000→latest to an empty supported PostgreSQL version.

**Pass:** no manual intervention; schema dump matches reviewed golden.

### DB-002 — Upgrade from 019

Restore representative pre-Motif DB; apply 020–022.

**Pass:** existing Jobs/Artifacts/Runs remain readable; no lost relations.

### DB-003 — Enum alignment

Insert relations using all 36 ObjectKinds and 24 RelationKinds.

**Pass:** all accepted; unknown values rejected.

### DB-004 — One live Attempt

Race multiple claimers for one Job.

**Pass:** unique live-attempt constraint and transaction semantics allow one owner.

### DB-005 — Fencing monotonicity

Create attempt, expire lease, create takeover, submit old token.

**Pass:** old terminal update rejected.

### DB-006 — Outbox atomicity

Force failure after domain update but before transaction commit.

**Pass:** neither update nor outbox visible. On success, both visible.

### DB-007 — Terminal completion rollback

Force required Artifact link failure.

**Pass:** Job does not become done.

### DB-008 — Query plans

Synthetic scale:

- 1M Jobs/Attempts;
- 10M Shards summaries;
- 100M outbox history archived/partitioned as designed.

**Pass:** critical list/reconcile queries use indexes and remain within profile target.

---

## 5. Artifact tests

### AR-001 — Streaming memory bound

Generate 10+ GiB Artifact as a stream.

**Pass:** worker RSS remains below configured bound; no full buffering.

### AR-002 — Content deduplication

Upload identical bytes under same/different roles.

**Pass:** one blob identity; role-specific Artifact identities per contract.

### AR-003 — Interrupted upload

Kill worker mid-multipart/local temp write.

**Pass:** no committed Artifact; temp session reclaimable.

### AR-004 — Digest mismatch

Alter bytes before commit or relay.

**Pass:** quarantine/refusal; Job cannot complete.

### AR-005 — Range read

Read byte ranges and Parquet metadata/footer.

**Pass:** correct ranges, authorization enforced.

### AR-006 — Backend parity

Run golden fixture against Postgres-inline, local CAS and S3-compatible test backend.

**Pass:** same Artifact digest/metadata semantics.

### AR-007 — Restore drill

Restore PostgreSQL metadata plus Artifact bytes into clean environment.

**Pass:** release IDs, digests and read paths validate; missing bytes reported explicitly.

### AR-008 — GC safety

Create Artifacts referenced only by dataset/model/evidence/decision—not Jobs.

**Pass:** GC retains them. Unreferenced ephemeral blobs age out under policy.

---

## 6. Execution durability tests

### EX-001 — Process death after Job open

Kill control process after durable row, before executor submit.

**Pass:** reconciler returns Job to schedulable state or marks explicit failure; no eternal queued ghost.

### EX-002 — Worker death while running

Kill -9 local worker.

**Pass:** lease expires, Attempt becomes lost, retry starts under policy, completed shards reused.

### EX-003 — Stale late completion

Pause worker A, expire/take over with worker B, resume A.

**Pass:** A result rejected by fencing; B remains authoritative.

### EX-004 — Duplicate output manifest

Deliver same terminal manifest repeatedly.

**Pass:** idempotent commit, no duplicate links/events.

### EX-005 — Database unavailable at completion

Worker has uploaded outputs but control DB unavailable.

**Pass:** manifest remains recoverable; Job not falsely done; reconcile commits later with valid token or supersedes safely.

### EX-006 — Cancellation queued

Cancel before worker starts.

**Pass:** scheduler submission prevented/cancelled; Job confirmed cancelled.

### EX-007 — Cancellation running/cooperative

Request cancel during batch/epoch/MD safe point.

**Pass:** token observed, optional checkpoint committed, scheduler termination confirmed, public state accurate.

### EX-008 — Uncooperative task

Task ignores token.

**Pass:** grace expires, adapter hard-kills if policy allows; state and reason distinguish forced cancellation.

### EX-009 — Checkpoint recovery

Preempt training/MD, restart on same/different allowed topology.

**Pass:** resumes from compatible checkpoint; incompatible resume refuses.

### EX-010 — OOM resize

Force CUDA OOM.

**Pass:** one policy-defined batch reduction; provenance records resize; no infinite retry.

### EX-011 — Retry classification

Inject invalid input, node loss, corruption and scientific refusal.

**Pass:** only retryable classes retry.

### EX-012 — Control-plane restart

Restart all Dirac services during running local/remote Jobs.

**Pass:** status reconciles; no duplicate scientific Job; no lost terminal result.

---

## 7. Run DAG tests

### DAG-001 — Linear pipeline

Snapshot→proposal→gate→prediction→portfolio.

**Pass:** correct dependencies and Artifacts.

### DAG-002 — Fan-out/fan-in

Shard predictions and poses, then aggregate.

**Pass:** fan-in starts only after required shards; optional failures handled by policy.

### DAG-003 — Conditional fidelity

Only high-value/uncertainty candidates enter F2/F3.

**Pass:** exact policy release and condition evidence recorded.

### DAG-004 — Approval wait

Pause before expensive F4/promote.

**Pass:** no downstream submit until authorized actor approval.

### DAG-005 — Partial recomputation

Change acquisition policy only.

**Pass:** reuse frozen proposals/predictions; rerun acquisition; new policy digest and portfolio.

### DAG-006 — Plan immutability

Attempt to mutate active RunPlan.

**Pass:** rejected; create superseding/new Run.

---

## 8. Chemistry property tests

### CHEM-001 — Standardization idempotence

`standardize(standardize(x)) == standardize(x)` under one release.

### CHEM-002 — Parent/form/salt separation

Equivalent salts map to configured parent while retaining form identities.

### CHEM-003 — Stereo policy

Specified/unspecified/enumerated stereochemistry produces explicit status and no silent merge.

### CHEM-004 — Protected motif

Generate thousands of transformations.

**Pass:** protected atom/substructure invariant never violated.

### CHEM-005 — Forbidden chemistry

Known forbidden/reactive patterns.

**Pass:** exact reason codes and no hidden pass.

### CHEM-006 — Reaction atom mapping

Products preserve validated mapping and template constraints.

### CHEM-007 — Duplicate burden

Exact, tautomer, stereo and near-duplicate cases.

**Pass:** policy-specific classification stable.

### CHEM-008 — Proposed identity minting

Concurrent proposals of same new structure.

**Pass:** canonical compound identity deduplicated without losing separate proposal lineage.

---

## 9. Dataset and leakage tests

### DATA-001 — Snapshot repeatability

Same source transaction/watermark and policy.

**Pass:** identical manifest/data/split digests.

### DATA-002 — Unit normalization

Known conversions and invalid dimensions.

**Pass:** exact provenance; incompatible units refused.

### DATA-003 — Censoring preservation

Less-than/greater-than/interval labels.

**Pass:** no point-value collapse in snapshot unless an explicit transform release says so.

### DATA-004 — Protocol separation

Semantically different assay/protocol versions with same column label.

**Pass:** not silently merged.

### DATA-005 — Leakage injection

Inject exact molecule, series, temporal and replicate leakage.

**Pass:** detected and surfaced; invalid snapshot cannot promote model.

### DATA-006 — Missingness

Not-tested, failed synthesis, failed assay, QC fail.

**Pass:** distinct states and reasons retained.

---

## 10. Model tests

### ML-001 — Baseline completeness

Promotion report omits simple baseline.

**Pass:** promotion refuses.

### ML-002 — Execution digest sensitivity

Change checkpoint, featurizer, calibration, dataset or policy independently.

**Pass:** digest/cache invalidates appropriately.

### ML-003 — Split determinism

Same snapshot/policy/seed.

**Pass:** identical assignment.

### ML-004 — Ensemble seed scope

Parallel/sharded execution order changes.

**Pass:** member seeds and outputs remain within declared determinism semantics.

### ML-005 — Calibration coverage

Evaluate temporal/series/out-of-domain slices.

**Pass:** target coverage and interval width reported with CIs; failures visible.

### ML-006 — Applicability domain

Known distant compounds.

**Pass:** correct status/refusal policy; no confident in-domain display.

### ML-007 — Local VRAM envelope

Run V1 D-MPNN ensemble on 5080.

**Pass:** no uncontrolled OOM; sequential load; peak recorded.

### ML-008 — Distributed checkpoint reshard

Save/load across permitted world sizes.

**Pass:** model/optimizer/sampler state verified within tolerance.

### ML-009 — Promotion/rollback

Promote candidate, then rollback.

**Pass:** explicit actor/evidence, old release remains immutable, cache identity follows selected release.

---

## 11. Structure and physics tests

### STR-001 — Structure provenance completeness

Missing chain/protonation/cofactor/preparation.

**Pass:** F2 refusal or explicit incomplete warning per policy.

### STR-002 — Pose symmetry

Symmetric ligand atom permutations.

**Pass:** symmetry-corrected metric.

### STR-003 — Adapter isolation

Crash DiffDock/Boltz process.

**Pass:** control plane survives; typed failure; fallback only if frozen policy permits.

### STR-004 — Vina baseline parity

Frozen image/input/seeds.

**Pass:** expected pose/score tolerance.

### STR-005 — Existing Dirac fields parity

Old and migrated invocation paths on golden molecules.

**Pass:** cube/summary parity within declared tolerance.

### PHY-001 — MD checkpoint

Interrupt and resume.

**Pass:** trajectory continuity/restart metadata valid.

### PHY-002 — FEP network partial failure

Fail selected edge/window.

**Pass:** network report shows coverage/failure; no fabricated complete ΔΔG.

### PHY-003 — Decision utility

Compare portfolio before/after physics.

**Pass:** records whether expensive compute changed decision; scientific evaluation later measures correctness.

---

## 12. Acquisition tests

### ACQ-001 — Hard constraints

No candidate meeting all constraints.

**Pass:** portfolio infeasible refusal, not “best bad” silently selected.

### ACQ-002 — Capacity exactness

Vary synthesis/assay slots.

**Pass:** selected portfolio never exceeds capacities.

### ACQ-003 — Exploration quota

High-score cluster dominates.

**Pass:** configured exploration/diversity allocation honored.

### ACQ-004 — Reason completeness

Every candidate.

**Pass:** exactly one selected/reserve/rejected/refused status and at least one reason.

### ACQ-005 — Deterministic baseline

Same input/policy/seed.

**Pass:** identical portfolio.

### ACQ-006 — Advanced policy comparison

BoTorch/other adapter.

**Pass:** same frozen data/budget; full baseline grid; no cherry-pick.

### ACQ-007 — Sensitivity

Perturb thresholds/weights within declared range.

**Pass:** sensitivity Artifact and unstable selections highlighted.

---

## 13. Frontend tests

### UI-001 — Truth vocabulary

Visual snapshots and DOM assertions.

**Pass:** measured/predicted/evidence/decision distinct.

### UI-002 — Single scene

Navigate Generate/Landscape/Optimize.

**Pass:** one persistent Mol* SceneService, no duplicated scene state.

### UI-003 — Stale context

Switch campaign during in-flight request.

**Pass:** old result cannot overwrite current context.

### UI-004 — Honest progress

Unknown denominator and partial shards.

**Pass:** no timer-fabricated percentage.

### UI-005 — Large collection

50k proposals/5k predictions.

**Pass:** server pagination/aggregation; browser memory bounded.

### UI-006 — Failure paths

Out-of-domain, quota refusal, missing Artifact, approval required.

**Pass:** typed actionable state, no empty success.

### UI-007 — Review/promote permissions

Human/agent/service roles.

**Pass:** unauthorized action refused; trace/actor preserved.

---

## 14. Local appliance benchmark protocol

### 14.1 Hardware capture

Record:

- CPU model/BIOS/microcode;
- RAM capacity/speed;
- GPU model/VBIOS/driver;
- storage model/filesystem/free space;
- OS/kernel;
- image digests;
- ambient power/performance mode if relevant.

### 14.2 Cold and warm runs

Run at least:

- cold cache;
- warm model/data cache;
- after service restart;
- after host reboot;
- repeated seeds where statistical.

### 14.3 Metrics

Per Step:

- queue/start/end;
- CPU/GPU utilization;
- peak RSS/VRAM;
- read/write bytes;
- Artifact sizes;
- cache/dedup;
- energy optional;
- warnings/failures;
- estimator predicted vs actual.

### 14.4 V1 target gates

- medium fixture completes without swap/OOM;
- Job visible P95 < 2 s;
- read models P95 < 200 ms local;
- full cycle P50 target <= 120 min and P95 target <= 4 h excluding optional long physics;
- restart reconciliation <= 60 s;
- cancellation acknowledgement <= 5 s;
- 100% required Artifact integrity;
- replay passes determinism tolerance.

Targets not met are reported; the fixture is not reduced post hoc without a versioned benchmark change.

---

## 15. Slurm/Kubernetes scale tests

### SCALE-001 — Submission throttling

Generate more Allocations than site permits.

**Pass:** bounded outstanding submissions; no API/scheduler flood.

### SCALE-002 — Massive arrays

Use site max array size and split larger manifests.

**Pass:** complete shard coverage, no duplicate/lost shard.

### SCALE-003 — Aggregated heartbeat

10k simulated workers.

**Pass:** PostgreSQL heartbeat writes remain under configured cap.

### SCALE-004 — Retry storm

Fail 10% nodes/shards simultaneously.

**Pass:** backoff/circuit breaker prevents control collapse.

### SCALE-005 — Preemption

Preempt distributed training and shard arrays.

**Pass:** checkpoint/requeue policy, correct Attempt lineage.

### SCALE-006 — Site gateway restart

Restart gateway while scheduler work continues.

**Pass:** reconcile without duplicate scientific Job.

### SCALE-007 — Air-gapped relay

No direct object-store/DB access.

**Pass:** signed bundle staging and result relay verify.

### SCALE-008 — Kueue quota/priority

Competing projects/queues.

**Pass:** Kueue capacity decision reflected as Allocation status; Dirac project quota remains separate.

### SCALE-009 — JobSet partial failure

One replicated job fails.

**Pass:** Dirac Attempt policy determines retry; CRD UID stays private.

### SCALE-010 — Distributed training topology

1→8→64+ GPUs as available.

**Pass:** checkpoint, convergence/tolerance, throughput scaling and communication telemetry reported.

---

## 16. Retrospective scientific evaluation

### 16.1 Frozen analysis plan

Before results:

- endpoints;
- splits;
- baselines;
- metrics;
- bootstrap procedure;
- multiplicity handling;
- slices;
- promotion thresholds;
- failure handling.

### 16.2 Required reports

- complete model × split × endpoint matrix;
- confidence intervals;
- calibration/domain slices;
- activity cliffs;
- data leakage report;
- resource/cost;
- acquisition replay;
- negative results and regressions;
- model card limitations.

### 16.3 No single-number promotion

AUC/MAE alone cannot promote a model. Promotion evaluates decision utility, calibration, domain and runtime reliability.

---

## 17. Prospective DMTA protocol

Before unblinding:

- freeze Design Brief;
- freeze data/model/policy releases;
- register baseline arm;
- set experimental capacity;
- define primary/secondary endpoints;
- define failed synthesis/assay handling;
- define statistical analysis;
- hash pre-registration Artifact.

After completion:

- account for every proposal;
- report selected/not selected;
- report attempted/failed/successful synthesis;
- report all measurements and missingness;
- effect sizes and intervals;
- cost/time/Pareto progress;
- deviations;
- next-cycle changes.

Only this gate supports prospective product claims.

---

## 18. Release evidence bundle

Each release candidate emits one immutable bundle:

```text
release-manifest.json
contract validation report
database migration report
SBOM/license report
image/checkpoint/data/policy digests
unit/integration/chaos results
local benchmark report
scheduler scale report if applicable
scientific validation report
model/policy cards
known failures and waivers
approvals
```

The release bundle itself is a Dirac Artifact linked to the model/policy/platform release.
