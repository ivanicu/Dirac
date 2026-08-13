# Dirac Motif Open-Source Ecosystem Integration Matrix

**Purpose:** maximize useful open-source leverage without turning Dirac into an unmaintainable bundle of conflicting research environments.
**Rule:** public semantic contracts remain framework-neutral; every nontrivial tool is a versioned adapter with an image digest, checkpoint digest, license record, golden fixture and failure translation.

> License labels below are an engineering inventory, not legal advice. Release CI must inspect the exact source commit, wheel, native library, checkpoint and transitive dependencies actually distributed or deployed.

---

## 1. Placement classes

### CORE

Allowed in a standard, supportable runtime image. Must have stable APIs, clear license, pinned dependencies and strong test coverage.

### ADAPTER

Runs in a separate process/image behind a Dirac Method. It may be heavy, fast-moving, license-sensitive or hardware-fragile. Failure cannot take down the control plane.

### RESEARCH

May run under an experimental Method/release. Not eligible for production promotion until reproducibility, license, checkpoint provenance and failure behavior are proven.

### MIRROR

May receive telemetry or projections, but cannot become authoritative state.

### REJECTED AS CORE

May still be useful externally, but must not own public APIs, model truth, scientific lineage or scheduler admission.

---

## 2. Recommended matrix

| Project | Function | Placement | License posture | Local 5080 | HPC scale | Dirac integration decision |
|---|---|---|---|---|---|---|
| RDKit | identity, descriptors, fingerprints, reactions, conformers | CORE | BSD-style/permissive; verify build | excellent | embarrassingly parallel | primary chemistry substrate |
| NumPy / SciPy | numerical core | CORE | permissive | excellent | node-level | standard dependency |
| PyArrow / Parquet | tabular data and interchange | CORE | Apache-2.0 | excellent | excellent | default dataset/prediction format |
| scikit-learn | linear/RF/calibration/baselines | CORE | BSD-3-Clause | excellent | shard/CPU | mandatory strong baseline |
| XGBoost | tree baseline | CORE | Apache-2.0 | excellent | distributed optional | mandatory candidate baseline |
| PyTorch | deep training/inference | CORE image | BSD-style | excellent within 16 GB envelope | DDP/FSDP2/TP | pinned scientific runtime |
| Chemprop | D-MPNN | CORE in PyTorch image | MIT | good, sequential ensemble | data/distributed training | V1 deep predictor baseline |
| BoTorch / GPyTorch | Bayesian optimization/acquisition | ADAPTER/CORE-limited | MIT | good for modest sets | centralized or batched | advanced policy competing with deterministic baseline |
| AutoDock Vina | docking baseline | ADAPTER | Apache-2.0 | good | arrays | V1 structure baseline |
| DiffDock | pose generation | ADAPTER | MIT code; verify checkpoints | possible, tightly capped | GPU shards | optional F2 model, never sole pose path |
| Boltz | structure/affinity | ADAPTER | MIT code; verify weights/data | compatibility-dependent on 16 GB | GPU allocation | optional, isolated image |
| OpenMM | MD/minimization | ADAPTER | mixed MIT/LGPL and bundled licenses; inspect exact build | good for small systems | multi-GPU/site adapter | first physics engine |
| GROMACS | HPC MD | ADAPTER | LGPL-2.1-or-later posture | possible but not V1 focus | excellent | HPC-specific MD adapter |
| OpenFE | RBFE workflow | ADAPTER | MIT | limited proof | intended for cluster | later F4 orchestration |
| MDAnalysis / MDTraj | trajectory analysis | ADAPTER | verify exact package | good | shard analysis | image-local analysis |
| REINVENT 4 | generative design | ADAPTER | Apache-2.0 per project | possible | GPU scale | optional proposal strategy |
| AiZynthFinder | retrosynthesis | ADAPTER | MIT | possible | CPU/service | route evidence, not authority |
| reaction/GFlowNet repos | synthesizable generation | RESEARCH | commit-specific | research | research | benchmark before promotion |
| GNINA | DL docking/scoring | ADAPTER with license gate | GPL/Apache dual; OpenBabel path triggers GPL | possible | arrays | separate process/image, legal review |
| Ray | intra-allocation dynamic parallelism | ADAPTER | Apache-2.0 | usually unnecessary | useful in selected workloads | never global Job truth |
| MLflow | experiment UI/mirror | MIRROR | Apache-2.0 | optional | optional | read-only projection, not model registry authority |
| Kueue | Kubernetes batch admission | SCHEDULER ADAPTER | Apache-2.0 | no | excellent | capacity/quota under K8s, not scientific DAG |
| JobSet | grouped Kubernetes Jobs | SCHEDULER ADAPTER | Apache-2.0 | no | excellent | Allocation implementation |
| Slurm | HPC scheduler | SCHEDULER ADAPTER | GPL components; site-provided | no | excellent | scheduler authority only |
| Apptainer | HPC container runtime | RUNTIME ADAPTER | BSD-style project; verify build | optional | excellent | derive SIF from pinned OCI |
| OpenTelemetry | telemetry | CORE/MIRROR | Apache-2.0 | excellent | excellent | observability, never provenance truth |
| Zarr | chunked arrays | CORE data format | MIT | excellent | object/cloud-friendly | fields/large N-D data |
| safetensors | safe tensor serialization | CORE where compatible | Apache-2.0 | excellent | excellent | simple checkpoints, not all distributed state |

---

## 3. Chemistry core

### 3.1 RDKit

Use for:

- SMILES/Mol/SDF parsing;
- valence and sanitization;
- canonicalization with a Dirac-owned policy release;
- tautomer/protomer enumeration under explicit policy;
- stereochemistry checks;
- Morgan fingerprints/descriptors;
- substructure gates;
- reaction SMARTS/template execution;
- ETKDG conformers;
- MCS/MMP-like operations;
- atom mapping validation utilities.

Do not treat default RDKit canonicalization as an eternal scientific law. Dirac must freeze:

- RDKit build/version;
- sanitization flags;
- aromaticity model;
- tautomer rules;
- charge normalization;
- isotope/stereo/salt policy;
- InChI implementation/version if used.

Identity-policy changes create a new release and may create supersession relations; they do not rewrite old compound evidence.

### 3.2 Reaction libraries

Reaction templates must be data releases, not source-code constants. Each template records:

- template ID/version;
- SMARTS/atom mapping;
- source/license;
- validation examples;
- known scope/failure;
- allowed reactant classes;
- product sanitization rules;
- estimated reliability evidence.

Open template sets can seed the system, but internal success/failure evidence becomes the valuable layer.

---

## 4. Predictive modeling

### 4.1 Mandatory baseline ladder

Every endpoint model report includes the same frozen split and at least:

1. nearest-neighbor / similarity baseline;
2. linear or ElasticNet on fingerprint/descriptors;
3. Random Forest or XGBoost;
4. Chemprop D-MPNN;
5. optional pretrained molecular encoder;
6. ensemble/calibration/domain layer.

A deep model is not promoted because it is newer. It must show stable utility or complementary value across temporal/series/scaffold/protocol slices.

### 4.2 PyTorch runtime

Candidate 2026 baseline: PyTorch 2.13, which adds distributed/fault-tolerance and FSDP2 improvements. Dirac must still pin a tested image rather than follow `latest`.

Compatibility matrix per image:

```text
image digest
Python
PyTorch
CUDA runtime
minimum/maximum driver
NCCL / torchcomms
Triton / CuTe backend
GPU architectures
precision modes
compile mode
known adapter exclusions
```

### 4.3 RTX 5080 policy

For 16 GB VRAM:

- sequential ensemble members;
- one resident large model;
- BF16/FP16 after golden validation;
- dynamic batch probing;
- gradient accumulation;
- activation checkpointing only when training requires it;
- CPU feature cache in Arrow/Parquet;
- explicit peak-VRAM estimator;
- no simultaneous DiffDock/Boltz/Chemprop resident processes.

### 4.4 Distributed training

Use native PyTorch primitives first:

- DDP for conventional data parallelism;
- FSDP2 when model/state requires sharding;
- tensor parallel only for truly large models;
- `torchrun` elastic entrypoint;
- PyTorch Distributed Checkpoint;
- topology-aware scheduler placement;
- exact data sampler and seed state.

Ray Train or an operator may wrap this inside an Allocation, but the release and checkpoint identity remains Dirac-owned.

---

## 5. Structure and physics

### 5.1 Docking ladder

V1:

```text
RDKit conformer
 -> Vina pose baseline
 -> interaction fingerprint/strain checks
 -> optional DiffDock
 -> Dirac fields/torsion
 -> human structural review
```

Why Vina stays:

- cheap;
- reproducible enough for a baseline;
- works locally and as arrays;
- does not require a large GPU model;
- exposes when a learned model fails to add value.

### 5.2 DiffDock/Boltz adapters

Each has:

- isolated OCI image;
- exact code commit and checkpoint digest;
- model/data license record;
- receptor/ligand preparation contract;
- max atoms/residues/VRAM profile;
- deterministic seed policy;
- timeout/cancellation behavior;
- golden complexes;
- output conversion into canonical pose/interaction Artifacts;
- refusal codes for unsupported systems.

Never put repository-native output directories directly in the UI. Convert outputs through the Method contract.

### 5.3 OpenMM

Use for:

- minimization;
- restrained relaxation;
- short MD proof;
- parameterized local physics escalation;
- checkpoint/restart.

Before production, freeze:

- force field files/digests;
- water/ion model;
- protonation/parameterization tool releases;
- integrator/timestep/thermostat/barostat;
- platform (CUDA/CPU) and precision;
- random seeds;
- constraints/cutoff/PME settings;
- topology and initial coordinates.

Because OpenMM distributions contain multiple third-party licenses, image-level SBOM/license review is mandatory even though the main project is permissive/LGPL-oriented.

### 5.4 GROMACS

Use when HPC operational maturity and throughput justify it. Keep it a command-line/container adapter:

- immutable `.tpr` and input manifest;
- checkpoint `.cpt`;
- scheduler/MPI/GPU profile;
- trajectory/result conversion;
- exact GROMACS build and compile flags;
- LGPL obligations reviewed for distribution model.

### 5.5 OpenFE/RBFE

OpenFE orchestrates alchemical workflows; it does not remove the need for:

- ligand mapping/network policy;
- force-field/charge provenance;
- engine/container identity;
- window/replica scheduling;
- convergence/failure/cycle closure;
- complete negative/failure outcomes;
- human authorization for expensive compute.

OpenFE enters only after the execution substrate proves checkpoint/retry and Artifact integrity.

---

## 6. Generative and synthesis tools

### 6.1 REINVENT 4

Good optional adapter for sequence/RL-style generation. It must compete with local edits/reaction enumeration and must emit:

- parent/seed lineage;
- scoring-component release IDs;
- sampling seed;
- duplicate and domain metrics;
- route/identity gate results;
- no opaque aggregate score as product output.

### 6.2 AiZynthFinder

Use as route-evidence adapter, not as final truth. Record:

- stock snapshot;
- expansion/filter model digests;
- search settings;
- route alternatives;
- timeout/failure;
- license/source of reaction data/models;
- estimated—not guaranteed—route status.

### 6.3 GFlowNet/reaction-flow research

Promote only after:

- exact environment and checkpoints reproduce;
- reaction/action space license is clear;
- synthesis-validity benchmarks beat enumeration baselines;
- local 16 GB and cluster resource profiles exist;
- reward hacking tests pass;
- output can be grounded to reaction provenance.

---

## 7. Scheduling and orchestration

### 7.1 Slurm

Use native job arrays for homogeneous shards and `srun`/`torchrun` for distributed training. Slurm can submit/manage very large arrays efficiently, subject to site limits. Dirac adapter must discover/receive:

- partitions;
- accounts/QOS;
- MaxArraySize and site caps;
- preemption/time-limit signals;
- GPU GRES syntax;
- MPI/container conventions;
- shared filesystem policy.

### 7.2 Kubernetes, Kueue and JobSet

Kueue owns cluster batch admission/quota; JobSet groups multiple Jobs as one workload. Dirac maps one Allocation to these resources and persists their UIDs privately.

Do not use Kubernetes CRDs as public scientific schemas. A migration from JobSet to another controller should not change `cycle.start` or `ml.motif.train`.

### 7.3 Ray

Permitted uses:

- shared model actors within one Allocation;
- dynamic task dispatch when static arrays benchmark poorly;
- Ray Data for a specific, measured pipeline advantage.

Not permitted as:

- global Job database;
- project admission/quota;
- durable model registry;
- Artifact authority;
- cross-site scheduler;
- public agent API.

On Slurm, prefer native arrays for simple homogeneous work; a nested Ray cluster adds operational failure modes.

---

## 8. Data formats and storage

### 8.1 Canonical choices

- JSON: contracts/manifests/summaries only;
- Arrow IPC: process and streaming interchange;
- Parquet: tabular authority for dataset/prediction/proposal matrices;
- SDF: structure collection paired with Parquet IDs;
- Zarr: chunked N-D fields/large arrays;
- XTC/DCD/TRR: trajectories;
- PyTorch DCP: distributed training state;
- safetensors: simple immutable tensor weights where compatible.

### 8.2 Why not pickle

Pickle is executable, Python-specific and fragile. It MUST NOT be an untrusted/public Artifact format. Framework-native checkpoint formats are allowed only in restricted model release Artifacts with exact runtime and trusted loading policy.

### 8.3 Why not CSV as authority

CSV loses:

- data types;
- null semantics;
- units/schema metadata;
- nested lineage;
- efficient predicate pushdown;
- stable large-scale performance.

CSV may be an export Artifact, never the dataset release authority.

---

## 9. Observability and experiment mirrors

### 9.1 OpenTelemetry

Use SDK + Collector for vendor-neutral traces/metrics/logs. It may export to Prometheus/Grafana/Tempo/Loki or commercial systems. Scientific truth remains in Dirac.

### 9.2 MLflow/W&B

Allowed mirror fields:

- Dirac model release ID;
- Job/Run IDs;
- metrics;
- parameter summary;
- Artifact links;
- image/dataset digests.

Forbidden:

- mirror run ID as canonical model ID;
- production stage controlled only in mirror;
- model loaded by mutable “latest” alias;
- raw partner data copied without policy;
- loss of Dirac actor/approval evidence.

---

## 10. License and checkpoint governance

Every adapter/release MUST record:

```text
source repository and commit
declared code license
wheel/container/native library licenses
checkpoint license and source
dataset/pretraining disclosure
commercial-use restrictions
attribution/notice requirements
copyleft/linkage assessment
distribution vs internal-service decision
reviewer and review date
```

### 10.1 Isolation levels

- **L0 core:** permissive, linked/imported normally;
- **L1 process adapter:** separate process, stable files/JSON protocol;
- **L2 container adapter:** separate image, license notices and distribution controls;
- **L3 external/site adapter:** organization does not distribute binary; invokes site/service under contract;
- **L4 rejected:** incompatible or unclear rights.

GNINA with OpenBabel defaults should be L2/L3 until legal/product distribution decisions are explicit.

### 10.2 Model weights are not automatically covered by code license

A repository MIT license does not necessarily grant rights to every hosted checkpoint or training dataset. The model release gate verifies weights separately.

---

## 11. Build and supply-chain requirements

For each image:

1. build from pinned base digest;
2. dependency lock with hashes;
3. produce SBOM (e.g. SPDX/CycloneDX);
4. collect license notices;
5. scan vulnerabilities;
6. sign image and provenance attestation;
7. run unit/golden/hardware tests;
8. record OCI digest in Method release;
9. optionally convert to Apptainer and record source-OCI relation;
10. forbid runtime dependency installation.

### 11.1 Adapter readiness checklist

- [ ] exact source and checkpoint digest;
- [ ] license review;
- [ ] input/output converter;
- [ ] resource estimator;
- [ ] cancellation safe points or scheduler kill semantics;
- [ ] checkpoint/restart where applicable;
- [ ] typed errors/refusals;
- [ ] local fixture;
- [ ] cluster fixture;
- [ ] deterministic/statistical tolerance;
- [ ] Artifact streaming;
- [ ] no direct domain DB access;
- [ ] versioned model card/known limitations.

---

## 12. Reference sources

- PyTorch 2.13 release: https://pytorch.org/blog/pytorch-2-13-release-blog/
- Slurm job arrays: https://slurm.schedmd.com/job_array.html
- Kueue: https://kueue.sigs.k8s.io/
- Ray cluster FAQ: https://docs.ray.io/en/latest/cluster/faq.html
- OpenMM: https://github.com/openmm/openmm
- OpenFE license: https://github.com/OpenFreeEnergy/openfe/blob/main/LICENSE
- GROMACS license: https://www.gromacs.org/about.html
- GNINA license note: https://github.com/gnina/gnina

The exact dependency and checkpoint set used by a release—not this document—is authoritative.
