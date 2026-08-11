# The GPU has been broken since 2026-08-07, and the failure mode is a hang

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

## The failure mode is a hang, not an error

A forced GPU-branch run sits at 0.0% CPU and never returns. That matters for the fix:

  - `except ImportError` in mep_surface.py catches only "gpu4pyscf is not installed".
  - A runtime CUDA error would need a broader `except`.
  - A HANG needs neither — it needs a DEADLINE on the GPU attempt and a CPU retry,
    because there is no exception to catch.

Downstream, the hang surfaces as `ERR_EMPTY_RESPONSE` in the browser with no message,
and the σ-hole panel sits on UNMEASURED forever.

## What to do

1. Reboot. The device nodes are held by the desktop session (gnome-shell, Xwayland,
   mutter, 13 days up) and `nvidia` has 193 references, so the modules cannot be
   unloaded without ending that session. This is Ivan's call, not mine.
2. Then re-check: `nvidia-smi` should report cleanly and lapatinib/def2-SVP should run.
3. Independently of the reboot, the backend should bound the GPU attempt with a
   deadline and retry on CPU, carrying the reason in `gpu_unavailable_reason` — the
   field already exists for exactly this purpose. A loud fallback, not a silent one.

## What is now suspect

Anything GPU-bound between 2026-08-07 06:24 and the reboot: the field cache warms
running in the other session, any pueue GPU jobs, and any result whose provenance says
`ran_on: gpu`.
