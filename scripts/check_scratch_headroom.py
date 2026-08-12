#!/usr/bin/env python3
"""Is there room to write a cube? Asked before it matters, not after.

THE FAILURE THIS EXISTS TO PRE-EMPT, measured 2026-08-11: /tmp on this box is a 30 GB
tmpfs mounted with `usrquota`. User ivan reached 23.5 GB across 313,374 files, and the
next write returned EDQUOT. What that looked like from inside the session:

  · EVERY shell command exited 1, because the tool harness writes command output to /tmp
  · `df` reported 6.0 GB free throughout — df measures the FILESYSTEM, the limit was on
    the USER, and the two numbers have nothing to do with each other
  · pyscf died inside cubegen.write with `OSError: [Errno 122] Disk quota exceeded`,
    which reaches a chemist as an internal error about a path they have never heard of

The durable fix is backend/scratch.py, which sends all scratch to a 1.8 T ext4 volume.
This script is the SECOND half: the fix only holds while nothing points back at /tmp,
and a check that watches the wrong number is worse than none. So it measures BOTH:

  ① where the scratch is configured to go, and whether that is off the tmpfs
  ② the per-user occupancy of /tmp, which is the number that actually ran out

WHY IT IS A RATCHET AND NOT A FIXED LIMIT: the quota itself is unreadable here — the
`quota` binary is not installed — so the threshold cannot be derived from the system. It
is derived from the OBSERVED wedge (23.5 GB) with a margin, and the number is written
down as a measurement with its date rather than chosen as a round figure.

Usage: python3 scripts/check_scratch_headroom.py [--selftest]
Exit:  0 healthy · 1 scratch is on the quota-limited tmpfs, or occupancy is near the
       observed wedge point · 2 could not measure
"""
from __future__ import annotations

import os
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'backend'))

# Measured 2026-08-11: the write that failed did so with 23.5 GB held by this user.
OBSERVED_WEDGE_GB = 23.5
# Warn with enough room left to finish a round of work rather than at the cliff edge.
WARN_AT_GB = 18.0


def tmpfs_mounts() -> dict[str, dict]:
    """Which mounts are tmpfs, and which of those enforce a per-user quota.

    Read out of /proc/mounts rather than assumed, because `usrquota` is the entire
    reason df was misleading, and a future remount without it would make this check
    warn about a limit that no longer exists.
    """
    out = {}
    try:
        for line in pathlib.Path('/proc/mounts').read_text().splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[2] == 'tmpfs':
                opts = parts[3].split(',')
                out[parts[1]] = {
                    'usrquota': 'usrquota' in opts,
                    'size': next((o.split('=')[1] for o in opts
                                  if o.startswith('size=')), 'unset')}
    except OSError:
        pass
    return out


def user_bytes_in(path: str) -> tuple[int, int]:
    """Bytes and file count owned by this user under `path`, one filesystem only.

    os.walk with a uid filter rather than `du`: the number that matters is per-USER, and
    du would happily total another user's files and report a healthy figure while this
    user's quota is exhausted. That is the same class of mistake as reading df.
    """
    uid = os.getuid()
    total = files = 0
    root_dev = None
    for dirpath, dirnames, filenames in os.walk(path, onerror=lambda e: None):
        try:
            st = os.stat(dirpath)
        except OSError:
            continue
        if root_dev is None:
            root_dev = st.st_dev
        elif st.st_dev != root_dev:
            dirnames[:] = []
            continue
        for name in filenames:
            try:
                fst = os.lstat(os.path.join(dirpath, name))
            except OSError:
                continue
            if fst.st_uid == uid:
                total += fst.st_size
                files += 1
    return total, files


def main() -> int:
    problems: list[str] = []
    notes: list[str] = []

    mounts = tmpfs_mounts()
    quota_mounts = [m for m, info in mounts.items() if info['usrquota']]
    notes.append(f'tmpfs mounts with a per-user quota: {quota_mounts or "none"}')

    # ── ① where is scratch configured to go? ─────────────────────────────────
    try:
        import scratch
        target = str(scratch.scratch_dir())
    except Exception as e:                                          # noqa: BLE001
        print(f'check_scratch_headroom: cannot import backend/scratch.py ({e}); the '
              f'redirection cannot be verified and is therefore UNVERIFIED, not '
              f'healthy', file=sys.stderr)
        return 2

    on_quota_fs = None
    for m in quota_mounts:
        if target == m or target.startswith(m.rstrip('/') + '/'):
            on_quota_fs = m
    if on_quota_fs:
        problems.append(
            f'scratch is configured at {target}, which is under {on_quota_fs} — a tmpfs '
            f'with a per-user quota. pyscf writes the whole cube there before returning '
            f'it, so a full quota kills every quantum request with an OSError from '
            f'inside cubegen. Set DIRAC_SCRATCH, or let backend/scratch.py default.')
    else:
        du = shutil.disk_usage(target if pathlib.Path(target).exists()
                               else pathlib.Path.home())
        notes.append(f'scratch → {target} · {du.free / 1e9:.0f} GB free on its volume')
        if du.free < 5e9:
            problems.append(f'scratch volume has only {du.free / 1e9:.1f} GB free')

    # ── ② the number that actually ran out ───────────────────────────────────
    for m in quota_mounts:
        b, n = user_bytes_in(m)
        gb = b / 1073741824
        pct = 100 * gb / OBSERVED_WEDGE_GB
        line = (f'{m}: this user holds {gb:.1f} GB in {n:,} files '
                f'({pct:.0f}% of the {OBSERVED_WEDGE_GB} GB that wedged the session on '
                f'2026-08-11)')
        if gb >= OBSERVED_WEDGE_GB:
            problems.append(line + ' — AT OR ABOVE the observed failure point')
        elif gb >= WARN_AT_GB:
            problems.append(line + f' — above the {WARN_AT_GB} GB warning line, and the '
                                   f'first symptom is every shell command exiting 1, '
                                   f'not a disk error')
        else:
            notes.append(line)

    for n in notes:
        print(f'  OK    {n}')
    for p in problems:
        print(f'  FAIL  {p}')
    if problems:
        print('\nThe cheapest reclaim is stale per-session scratch directories; the '
              'durable fix is that nothing writes GB-scale files to a RAM-backed '
              'filesystem with a per-user quota in the first place.')
    return 1 if problems else 0


def selftest() -> int:
    """Prove the check can CONVICT, by pointing scratch at the quota-limited tmpfs.

    Without this the healthy verdict is unfalsifiable — and a check whose only observed
    output is PASS has not been shown to have resolution.
    """
    import subprocess
    env = dict(os.environ, DIRAC_SCRATCH='/tmp/dirac-selftest-scratch')
    r = subprocess.run([sys.executable, __file__], capture_output=True, text=True,
                       env=env)
    convicted = r.returncode == 1 and 'per-user quota' in r.stdout
    print(r.stdout.strip())
    print('─' * 90)
    print(f'{"RED OK  " if convicted else "BLIND   "} pointing scratch at the tmpfs '
          f'makes the gate fail (exit {r.returncode})')
    return 0 if convicted else 1


if __name__ == '__main__':
    sys.exit(selftest() if '--selftest' in sys.argv else main())
