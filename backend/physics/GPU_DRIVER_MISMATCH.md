# NVML is broken since 2026-08-07. CUDA compute is not. (Corrected.)

> **RETRACTION, same session, one measurement later.** The first version of this file
> claimed the GPU had been broken for four days and that the failure mode was a hang.
> Both are wrong. A forced GPU-branch run on 4-bromobenzonitrile (nao 164) returned
> **232.7 s, OK, V_S,max 27.93, one σ-hole, ran_on gpu** — identical to the CPU answer.
> What I read as a hang was a process sitting at 0.0% CPU, which is exactly what GPU
> offload looks like from `ps`. I called the normal signature of the thing working the
> evidence that it was broken.

## The fault

    dpkg upgrade      2026-08-07 06:24   nvidia 595.71.05 -> 595.84
    last boot         2026-07-29 08:42
    loaded module     595.71.05          (/proc/driver/nvidia/version)
    userspace library 595.84             (libnvidia-ml.so.595.84)
    nvidia-smi        Failed to initialize NVML: Driver/library version mismatch

The driver package was upgraded and the machine never rebooted, so the running kernel
still carries the old module. Four days.

## Why it was invisible

`GPU_CROSSOVER_NAO = 150`, so only calculations at or above 150 basis functions take
the GPU branch. Everything smaller kept working and looked healthy:

    bromobenzene   def2-SVP  nao 141  CPU branch   OK   V_S,max 44.02  sigma-hole 1
    chlorobenzene  def2-SVP           CPU branch   OK   V_S,max 31.60  sigma-hole 1
    fluorobenzene  def2-SVP           CPU branch   OK   V_S,max 18.01  sigma-hole 0
    lapatinib      sto-3g    nao 234  GPU branch   OK   (no d functions)
    lapatinib      def2-SVP  nao 698  GPU branch   FAILS

The sto-3g run crossing the threshold and still succeeding is the clue that the GPU is
not dead but DEGRADED: the failing kernel is `RYS_build_jk (dp|dd)`, a d-function
integral path, and a minimal basis has no d functions to hit it with.

## What actually survives, and what does not

SURVIVES, verified:
  - The driver mismatch is real. `nvidia-smi` cannot initialise NVML, so **monitoring
    and any tool that reads GPU utilisation are blind**, including anything that
    schedules on "is the GPU busy".
  - `RYS_build_jk (dp|dd) failed` on lapatinib/def2-SVP is a real error on a real
    molecule. It is a d-function integral path; STO-3G on the same molecule crosses the
    same threshold and succeeds because a minimal basis has no d functions.
  - `cudaErrorDevicesUnavailable` on the 6-31G retry was contention with another
    session's cache warm, and says nothing about the driver.

RETRACTED:
  - "The GPU has been broken for four days." CUDA compute works; one kernel path
    failed on one molecule.
  - "The failure mode is a hang." It is not; the GPU branch returns.
  - "Everything GPU-bound since 2026-08-07 is suspect." Unsupported. Results whose
    meta says `ran_on: gpu` are not invalidated by anything measured here.

NEW, and measured: on this box in this state the GPU is **slower** than the CPU for
this workload — 232.7 s against 201.8 s on the same molecule and basis. `GPU_SPEEDUP`
is 2.0 in `mep_surface.py` and the cost predictor divides by it, so every prediction
for a GPU-branch calculation is optimistic by roughly a factor of two. That is why a
90 s budget looked adequate for work that needed far more, and it is a defect
independent of the driver.

## What to do

1. A reboot still restores NVML and removes the version skew, and is worth doing — but
   it is no longer urgent-because-broken, it is hygiene. The device nodes are held by a
   13-day desktop session and `nvidia` has 193 references, so the modules cannot be
   unloaded without ending it. Ivan's call.
2. `GPU_SPEEDUP = 2.0` should be re-measured rather than assumed. On the one molecule
   measured here the true factor is 0.87 — the GPU is the slower path — and the cost
   gate's refusals and predictions are built on top of that constant.
3. The `except ImportError` around the gpu4pyscf import still only covers "not
   installed". A runtime CUDA failure — which `RYS_build_jk` demonstrably is — has no
   fallback, and a loud CPU retry carrying the reason in `gpu_unavailable_reason` would
   have turned that molecule's hard failure into an answer with a caveat.

## The lesson worth keeping

Three causes were proposed today for one symptom — the cost gate, the basis, a
gpu4pyscf kernel bug — and each had real evidence behind it. The fourth, "the driver is
mismatched so the GPU is dead", had the best evidence of all: a genuine version skew, a
genuine NVML failure, and a process sitting at zero CPU. It was still wrong, because
zero CPU is what success looks like when the work is somewhere else. **A signature is
not a diagnosis until something distinguishes it from the healthy case**, and the thing
that distinguished it here cost one 4-minute run: execute the same calculation both
ways and compare.
