# Execution, artifacts and operations

## Identity and terminal commit

The execution model separates:

- scientific identity: inputs + MethodManifest + parameters + condition + environment + numeric contract;
- LogicalJob identity: requested semantic computation;
- Attempt identity: one leased execution;
- Allocation identity: scheduler instance;
- ArtifactCommit identity: the sole terminal scientific output for a LogicalJob.

The worker uploads content-addressed artifacts and a manifest. The controller verifies
the manifest schema, execution digest, required roles, artifact hashes/sizes, lease and
fencing token. In one database transaction it inserts `app.artifact_commit`, marks the
Attempt succeeded, creates MethodOutcome and writes the outbox terminal event. A replay
of the identical commit is idempotent; a late/conflicting Attempt is rejected.

## ResourceBroker

One atomic lease covers CPU, RAM, GPUs, VRAM, scratch, persistent growth, process slots,
SCF slots and campaign credits. It records owner, campaign, backend, estimates, actuals,
expiry, heartbeat and fencing token. Admission uses observed capacity minus external
usage minus active reservations.

Storage is split into scratch, artifact, database, system and emergency reserves.
Kubernetes ephemeral-storage requests do not prove scratch capacity; production scratch
requires a dedicated path/PVC and enforceable quota. Swap exhaustion is an incident and
blocks new heavy work until diagnosed.

Kueue owns cluster admission. Motif selects one of the installed
`WorkloadPriorityClass` values and writes it through the
`kueue.x-k8s.io/priority-class` Job label:

- `motif-interactive` (1000): declared wall-time at most 300 seconds;
- `motif-standard` (100): normal bounded jobs;
- `motif-long` (10): OpenFE/RBFE or declared wall-time above 3600 seconds.

Unknown caller-supplied classes are refused. Aging, preemption, checkpointability,
starvation limits and campaign quota remain scheduler policy. Workers convert SIGTERM
to a cooperative cancellation request; Kubernetes grants 120 seconds for checkpoint
and clean termination before force-kill.

## Artifact lifecycle

Data tiers:

- hot scratch: incomplete attempt, lease-bound;
- active scientific artifacts: reachable from live evidence/release/decision roots;
- reduced trajectories: selections and frames required for reproducible analysis;
- cold archive: critical native inputs, checkpoints and final reports;
- tombstoned: unreachable, delayed deletion pending audit window.

Directory artifacts use a Merkle manifest. Garbage collection is reachability-based and
honours pins, legal/scientific holds, tombstones and delayed deletion. Critical releases
require a verified backup. “Keep every final trajectory forever” is not a capacity plan.

## Computational safety

All parsers enforce payload and decompressed-size bounds, file-count limits, timeouts and
nesting limits. Archives reject traversal, absolute paths, special devices and symlink
escapes. Worker entrypoints are fixed; user input never selects a shell command. Runtime
images are digest-pinned and heavy scientific workers have no network egress by default.
Temporary paths are private to the Attempt. Controllers reap orphan processes and revoke
expired leases. Zip bombs, malformed structure files and oversized JSON are test cases.

## Observability

Metrics use bounded labels (`method_id`, `release`, `backend`, `state`, `error_category`),
not compound/job IDs. Required metrics include:

| Metric | Type / unit | Alert or SLO |
|---|---|---|
| `motif_attempt_terminal_total` | counter | failures/lost by method and release |
| `motif_attempt_wall_seconds` | histogram seconds | p95 vs cost-model envelope |
| `motif_resource_lease_wait_seconds` | histogram seconds | p95 and starvation |
| `motif_gpu_vram_peak_bytes` | histogram bytes | >90% observed VRAM warning |
| `motif_scratch_peak_bytes` | histogram bytes | reserve breach blocks admission |
| `motif_artifact_commit_conflict_total` | counter | any nonzero is critical |
| `motif_evidence_stale_total` | counter | unexpected invalidation spike |
| `motif_planner_predicted_cost_ratio` | histogram actual/predicted | calibration drift |
| `motif_rbfe_repeat_ess` | histogram samples | protocol-specific minimum |
| `motif_rbfe_cycle_residual_kcal_mol` | histogram kcal/mol | policy threshold |

Planner evaluation logs policy, action set, chosen action, outcome model, predicted cost,
actual cost and realized utility. Safe evaluation uses shadow/replay plus logged
propensities; controlled random exploration is allowed only within a predeclared safe
set. Counterfactual claims are prohibited without an identification design.
