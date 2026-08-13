# Test tiers and traceability

## Test tiers

| Tier | Cadence | Maximum scope | Required evidence |
|---|---|---|---|
| 0 | every change, minutes | schemas, state machines, planner, fencing, small CPU fixtures | deterministic test report |
| 1 | nightly | real Vina/OpenMM, OpenFE planner, resume/cancel, parser adversarial tests | native artifacts + metrics |
| 2 | weekly | scientific controls, cross-docking, MD repeats, RBFE pilot/two-leg checks | QualityAssessment artifacts |
| 3 | release | full ≥4-node, ≥4-edge, two-leg, three-repeat RBFE network | ≥24 executions + covariance/cycle report |

Metamorphic tests cover atom renumbering, rigid coordinate transforms, unit conversion,
candidate ordering and equivalent serialization. Differential tests compare independent
implementations where appropriate. Version-migration tests load older artifacts and
prove explicit upgrade/refusal. Floating-point tests use method tolerances or
distributional criteria, never a global bitwise assertion.

## Requirement trace

| Requirement | Implementation | Test | Metric | Artifact |
|---|---|---|---|---|
| MOTIF-SEM-001 object graph | migration 037; scientific-object schema | schema + dependency tests | stale count | scientific object document |
| MOTIF-SEM-002 orthogonal state | semantics.py; orthogonal-state schema | failure ≠ rejection; transition tests | state totals | state document |
| MOTIF-EVD-001 typed evidence | semantics.py; evidence-item schema | stale/condition/dependency exclusions | evidence stale total | EvidenceItem/EvidenceSnapshot |
| MOTIF-ML-001 honest lifecycle | model_gates.py; model-validation schema | three-compound smoke test | releases by lifecycle | validation report |
| MOTIF-PLAN-001 EVSI action planner | action_planner.py; motif.plan | cost/budget/oscillation tests | predicted-cost ratio | RoutingAction |
| MOTIF-EXEC-001 at-least-once/exactly-once commit | attempt_store.py; artifact_commit table | stale and duplicate completion tests | commit conflicts | terminal manifest |
| MOTIF-RES-001 atomic resource lease | resource_broker.py; resource_lease table | capacity/fencing/expiry test | lease wait, VRAM/scratch peak | ResourceLease |
| MOTIF-CHEM-001 parent/state aggregation | aggregate_state_values | best-state refusal test | discarded mass | ChemicalStateEnsemble |
| MOTIF-OPENFE-001 official network | openfe_network_planner.py | pinned runtime 4-node run | planner duration | LigandNetwork serialization |
| MOTIF-OPENFE-002 charge invariant | openfe_runner.py | mismatched invariant refusal | rejected input total | charge artifact |
| MOTIF-RBFE-001 two legs × repeats | rbfe.py | 24-execution and repeat test | ESS/repeat variance | execution matrix |
| MOTIF-RBFE-002 covariance/sign/gauge | rbfe.py | covariance/closure tests | cycle residual | NetworkEstimateEvidence |
| MOTIF-API-001 public plan/validate/explain | command registry, SDK, CLI | command-surface test | command outcome total | CommandTrace |
| MOTIF-DATA-001 measurement semantics | measurement-observation and measurement-v2 schemas; governance.py | representation/parent-state tests | measurement observation total | MeasurementObservation |
| MOTIF-ML-002 scoped OOD and honest dispersion | uncertainty.py; mesh.py | predictor mesh tests | OOD assessment total | model domain assessment |
| MOTIF-ML-003 gated discrete qLogNEHVI | advanced_acquisition.py; method contract | posterior/lifecycle/refusal tests | acquisition refusal total | acquisition report |
| MOTIF-DOCK-001 exact expansion and target validation | docking.py; prepared-receptor-state schema | cardinality/validation/raw-score tests | docking validation gate total | docking validation |
| MOTIF-MD-001 frozen parameterization and analysis | physics.py; MD/parameterization schemas | analysis/departure/repeat tests | MD quality assessment total | MD analysis |
| MOTIF-ART-001 bounded artifact lifecycle | artifact_lifecycle.py | Merkle/reachability/watermark tests | artifact watermark state | directory manifest |
| MOTIF-SAFE-001 hostile input boundary | safe_inputs.py | traversal/link/bomb tests | archive intake refusal total | archive assessment |
| MOTIF-OBS-001 executable telemetry/benchmark contract | observability and benchmark schemas | cardinality/field tests | contract violation total | performance benchmark |
| MOTIF-EXEC-002 live worker commit integration | invocation.py; Kubernetes executor; attempt_store.py | live PostgreSQL and executor tests | exactly-once commit total | output manifest |
| MOTIF-RES-002 global observed-capacity admission | resource_broker.py; kernel.py; Kubernetes executor | cross-process PostgreSQL lease tests | capacity refusal total | ResourceLease |

The machine-readable trace source is `requirements.json`. Verification must fail if an
implementation/test/schema path is missing or if a requirement has no metric/artifact.
