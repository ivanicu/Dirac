"""Gaussian cube canonicalisation. Small, and it fixes something load-bearing.

MEASURED DEFECT, found by the acceptance test and not by reading anything: pyscf's
cubegen writes the WALL CLOCK into the cube's second line —

    Orbital value in real space (1/Bohr^3)
    PySCF Version: 2.14.0  Date: Tue Aug 11 19:54:41 2026

so the SHA-256 of a quantum cube is a function of the time of day. Three consecutive
runs of the identical calculation on the identical geometry:

    raw        7d30b3c3… / 9986e64a… / cd984ebe…    all different
    line 1 replaced   fcf18034… / fcf18034… / fcf18034…    identical

CONSEQUENCES, in the order they matter:
  · content-addressed storage cannot DEDUPLICATE a quantum cube at all. Every
    recomputation of the same field mints a new blob, and app.artifact's UNIQUE
    (blob, role, encoding) never fires — the idempotence PR-04 tested and proved in
    the store is unreachable in production for six of ten methods.
  · the acceptance test's "same artifact SHA-256 across transports" is IMPOSSIBLE,
    not merely unproven. Two transports that compute in different seconds must
    disagree.
  · and it passed once by luck, which is worse than failing: the first parity run went
    green because both legs happened to land inside the same wall-clock second. A test
    that depends on the clock will be green whenever it is fast and red whenever the
    machine is loaded, and the second case reads as a real regression.

WHAT IS REMOVED AND WHAT IS KEPT. The timestamp goes; the pyscf version STAYS, because
it is real provenance and it is deterministic. So the canonical line is

    PySCF Version: 2.14.0  Date: canonical (dirac: timestamp removed for addressing)

which keeps a human reading the file informed of both facts — which toolkit wrote it,
and that its date was deliberately removed.

WHY NOT FIX IT INSIDE field_quantum: that function's SOURCE is hashed into the method
version, so editing it invalidates every cached quantum field. That cost was paid once
today for a one-line refusal change and measured; it is not worth paying again for a
comment line. Canonicalisation therefore happens in the CONSUMERS — the handler and
the route — both of which are outside the hashed unit.

Import-light (stdlib only), because a CLI verifying `dirac artifacts verify` must
canonicalise exactly the same way, and it cannot import a chemistry toolkit to do it.
"""
from __future__ import annotations

import re

# Anchored to pyscf's exact format so it cannot match a chemist's own comment. A looser
# rule (any line containing a date) would silently rewrite user text, and the failure
# would be a corrupted comment nobody notices for months.
_PYSCF_DATE = re.compile(
    r'^(?P<head>PySCF Version:\s*(?P<version>[0-9][0-9A-Za-z.\-+]*))\s+Date:\s*.*$')

CANONICAL_NOTE = 'Date: canonical (dirac: timestamp removed for addressing)'


def canonicalise(cube: str) -> str:
    """Make the bytes a function of the INPUT alone. Idempotent.

    A no-op for cubes this project writes itself (write_cube's comment is passed in and
    carries no clock), so it is safe to apply to every kind rather than only the
    quantum ones — a rule applied to some paths and not others is a rule somebody will
    forget on the next path.
    """
    if not cube:
        return cube
    lines = cube.split('\n', 2)
    if len(lines) < 2:
        return cube
    m = _PYSCF_DATE.match(lines[1].strip())
    if not m:
        return cube
    lines[1] = f"{m.group('head')}  {CANONICAL_NOTE}"
    return '\n'.join(lines)


def is_canonical(cube: str) -> bool:
    """Whether these bytes are already addressable — i.e. whether their digest is
    reproducible. Used by the gate and by artifact verification, so a non-canonical
    cube arriving from an older row can be identified rather than merely mismatch."""
    return canonicalise(cube) == cube


def timestamp_in(cube: str) -> str | None:
    """The timestamp that was removed, for provenance that wants to keep it.

    It is not garbage — "when was this computed" is a real question — it just cannot
    live in the addressed bytes. Callers put it in the response metadata instead, where
    it belongs: metadata describes the request, bytes describe the science.
    """
    lines = cube.split('\n', 2)
    if len(lines) < 2:
        return None
    raw = lines[1].strip()
    if raw.endswith(CANONICAL_NOTE):
        return None
    m = re.match(r'^PySCF Version:\s*\S+\s+Date:\s*(?P<date>.+)$', raw)
    return m.group('date').strip() if m else None
