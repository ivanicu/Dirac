"""Point every temporary file at DISK, not at the quota-limited tmpfs.

MEASURED FAILURE, 2026-08-11, and it cost a working shell for ~15 minutes: `/tmp` on
this box is a 30 GB tmpfs mounted with `usrquota` (read out of /proc/mounts, not
assumed), user ivan was holding 23.5 GB across 313,374 files, and the next write
returned EDQUOT. That does not surface as a disk-full error anywhere useful — it
surfaced as EVERY shell command exiting 1, because the tool harness writes each
command's output into /tmp. The whole session's ability to run anything was gone, and
`df` reported 6 GB free the entire time, because df measures the FILESYSTEM and the
limit was on the USER.

WHY THIS MODULE AND NOT A UNIT-FILE ENV VAR: pyscf reads `TMPDIR` at import time and
writes SCF integral scratch there. A def2-SVP run on a drug-sized ligand writes GB-scale
files. Setting it only in the systemd unit fixes the daemon and leaves every other
entry point — a test, a CLI invocation, an SDK script, `capture_v1_golden.py` — still
pointed at the tmpfs. Those are exactly the paths a developer runs by hand, dozens of
times, which is how the quota filled in the first place. So the redirection lives in
code that all of them import, and the unit files set it too so an operator can SEE it.

  root filesystem   1.8 T, 78% used, 397 G free   ← where GB-scale scratch belongs
  /tmp (tmpfs)      30 G, per-user quota, RAM-backed

A tmpfs is the right place for a lock file and the wrong place for a 6 MB cube written
forty times an hour.

IDEMPOTENT AND OVERRIDABLE: an explicit TMPDIR or PYSCF_TMPDIR from the environment
WINS and is left alone. This module only fills a vacuum — a tool that overrode a
deliberate operator setting would be worse than the bug it fixes.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import tempfile

# Under ~/.cache rather than /var/tmp: it is unambiguously ours, needs no privileges,
# survives reboots (so a crashed SCF's scratch can be inspected rather than vanishing),
# and is a directory a human can delete without thinking about who else uses it.
DEFAULT_SCRATCH = pathlib.Path.home() / '.cache' / 'dirac' / 'scratch'

# The observed wedge point, kept as a NUMBER rather than a feeling. Used by
# scripts/check_scratch_headroom.sh to warn before the next one.
OBSERVED_WEDGE_BYTES = 23_500_000_000


def scratch_dir() -> pathlib.Path:
    return pathlib.Path(os.environ.get('DIRAC_SCRATCH') or DEFAULT_SCRATCH)


def redirect(verbose: bool = False) -> pathlib.Path:
    """Send tempfile and pyscf to disk. Safe to call repeatedly.

    Returns the directory in use so a caller can log it — a silent redirection is a
    surprise waiting for whoever later wonders where the scratch went.
    """
    target = scratch_dir()
    target.mkdir(parents=True, exist_ok=True)
    explicit = bool(os.environ.get('TMPDIR') or os.environ.get('PYSCF_TMPDIR'))
    if not explicit:
        os.environ['TMPDIR'] = str(target)
        os.environ['PYSCF_TMPDIR'] = str(target)
        # tempfile CACHES its answer on first use, so setting the env var alone is not
        # enough inside a process that has already created one temp file. Both are set:
        # the env var for pyscf (a separate C-level consumer that reads os.environ) and
        # the module attribute for anything already holding the cached value.
        tempfile.tempdir = str(target)
    if verbose:
        print(f'[scratch] temporary files → {tempfile.gettempdir()} '
              f'({"explicit env" if explicit else "redirected off the tmpfs quota"})',
              flush=True)
    return target


def usage() -> dict:
    """How much scratch is sitting there, and how much room the filesystem has.

    Reported rather than acted on: this module must never delete anything on its own.
    A background cleaner that removed a file a running SCF still had open would produce
    a failure far stranger than a full disk, and the whole point of putting scratch on a
    1.8 T volume is that it does not need to be swept urgently.
    """
    target = scratch_dir()
    total = 0
    files = 0
    if target.exists():
        for p in target.rglob('*'):
            try:
                if p.is_file():
                    total += p.stat().st_size
                    files += 1
            except OSError:
                continue
    du = shutil.disk_usage(target if target.exists() else pathlib.Path.home())
    return {'dir': str(target), 'bytes': total, 'files': files,
            'filesystem_free_bytes': du.free,
            'filesystem_free_gb': round(du.free / 1e9, 1)}


# Applied on import. A redirection that has to be remembered at every entry point is a
# redirection that will be forgotten at one of them, and the one that is forgotten is
# the interactive script a developer runs forty times in an afternoon.
redirect()
