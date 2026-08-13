"""The field cube cache, as something the kernel can be given.

WHY THIS FILE EXISTS AND WHY IT IS SEPARATE, which is the same reason artifacts_pg is
separate: the kernel must be able to run with no cache at all (an offline CLI, a test on a
bare interpreter), so the cache arrives as an INJECTED collaborator with a declared
interface — `lookup(method_id, payload) -> HandlerResult | None` — and the kernel neither
knows nor cares that a Postgres view and a molfile hash are involved.

WHAT THIS UNBLOCKS, and it is the whole point of PR-08: /field's 200-line orchestration
cannot be deleted while the kernel is missing what that orchestration does. The kernel had
no cache, so replacing the route with a kernel call would have turned every cache hit back
into a 6-minute SCF — a regression wearing a refactor's clothes. The route's behaviour has to
land in the kernel FIRST, and then the route becomes a thin adapter whose deletion the v1
golden can measure.

THE KEY IS THE ROUTE'S KEY, deliberately and not by coincidence: sha256 of the molfile bytes,
the kind, and the basis ('none' for classical). Anything else would mean a cube written by the
route could not be found by the kernel and vice versa, and the two would slowly fill the
table with each other's misses. Read through app.v_field_cube_servable — the method-currency
view — so a row produced by superseded source is invisible rather than wrong.

The SPIN CAVEAT is inherited on purpose: an explicit spin makes the SAME molfile give a
DIFFERENT field, and the durable key has no room for it. Spin-overridden requests therefore
bypass the cache in BOTH directions, exactly as the route does. Serving a high-spin heme to a
request that asked for the singlet is the failure this asymmetry prevents.
"""
from __future__ import annotations

import hashlib
import sys
import threading
from datetime import datetime, timezone
from typing import Any

from invocation import HandlerResult

# The methods this cache can serve, and the `kind` each maps to. Explicit rather than derived
# from the method_id's last segment: fields.region.* are NOT cacheable (no molfile exists to
# key them on) and a derivation would have silently included them.
CACHEABLE_KIND = {
    'fields.mep': 'mep',
    'fields.qm.homo': 'homo',
    'fields.qm.lumo': 'lumo',
    'fields.qm.density': 'density',
    'fields.qm.mep_qm': 'mep_qm',
}
# mlp is deliberately absent: it is not in app.field_kind's enum and costs ~0.03 s, so a row
# for it would be a schema violation bought for nothing. That was the route's decision and
# it is restated here rather than inherited silently.


class FieldCubeCache:
    """Read and asynchronously write the durable cube cache.

    Both callbacks are injected, so this adapter has no database dependency. Writes reuse
    the daemon's existing background-safe writer and never sit inside the invocation's
    critical path; local short-lived processes use a non-daemon thread so a promised write
    is allowed to commit during interpreter shutdown.
    """

    def __init__(self, get_cube, *, put_cube=None, prepare_mol=None,
                 canonicalise=None) -> None:
        # `get_cube(molfile_sha: bytes, kind: str, basis: str) -> (cube, meta) | None`, passed
        # in rather than imported, so this module does not depend on field_server and can be
        # exercised with a stub.
        self._get_cube = get_cube
        self._put_cube = put_cube
        self._prepare_mol = prepare_mol
        self._canonicalise = canonicalise
        self.counters = {'looked_up': 0, 'hit': 0, 'miss': 0, 'skipped_spin': 0,
                         'skipped_uncacheable': 0, 'error': 0,
                         'write_queued': 0, 'write_ok': 0, 'write_error': 0}

    def lookup(self, method_id: str, payload: dict, *,
               execution_digest: str | None = None) -> HandlerResult | None:
        kind = CACHEABLE_KIND.get(method_id)
        if kind is None:
            self.counters['skipped_uncacheable'] += 1
            return None
        params = dict(payload.get('parameters') or {})
        if params.get('spin') is not None:
            # NOT a miss — a deliberate bypass, counted separately so the two cannot be
            # confused when someone asks why the hit rate dropped.
            self.counters['skipped_spin'] += 1
            return None
        content = ((payload.get('molecule') or {}).get('content') or '')
        if not content:
            return None
        basis = 'none' if kind == 'mep' else params.get('basis', 'sto-3g')
        molfile_sha = hashlib.sha256(content.encode()).digest()
        self.counters['looked_up'] += 1
        try:
            hit = self._get_cube(molfile_sha, kind, basis)
        except Exception as e:                                      # noqa: BLE001
            # A cache that raises must not fail the invocation: the science can still run.
            # Counted and LOUD, because a cache that silently stops working looks exactly
            # like a cache that is cold.
            self.counters['error'] += 1
            print(f'[cache] lookup failed for {method_id} ({type(e).__name__}: {e}) — '
                  f'computing instead', file=sys.stderr, flush=True)
            return None
        if hit is None:
            self.counters['miss'] += 1
            return None
        cube, meta = hit
        if self._canonicalise is not None:
            cube = self._canonicalise(cube)
        self.counters['hit'] += 1
        return self._as_handler_result(method_id, kind, basis, cube, meta)

    def store(self, method_id: str, payload: dict, out: HandlerResult, *,
              seconds: float, job_id: str | None = None,
              envelope: dict | None = None,
              execution_digest: str | None = None) -> None:
        """Queue a validated computed field for durable persistence.

        The invocation never waits for PostgreSQL. This preserves the existing interaction
        contract while moving ownership of the behaviour out of the HTTP route. The callback
        remains injected, so this module has no database or daemon dependency.
        """
        kind = CACHEABLE_KIND.get(method_id)
        if kind is None or self._put_cube is None or out.cache != 'computed':
            return
        params = dict(payload.get('parameters') or {})
        if params.get('spin') is not None:
            return
        content = ((payload.get('molecule') or {}).get('content') or '')
        if not content:
            return
        cube_entry = next((data for role, data in out.artifacts
                           if role == 'field.cube'), None)
        if cube_entry is None:
            return
        cube = cube_entry.decode()
        basis = 'none' if kind == 'mep' else params.get('basis', 'sto-3g')
        molfile_sha = hashlib.sha256(content.encode()).digest()
        meta = self._legacy_meta(kind, basis, out, seconds)
        self.counters['write_queued'] += 1

        def persist() -> None:
            try:
                mol = self._prepare_mol(content) if self._prepare_mol is not None else None
                self._put_cube(molfile_sha, kind, basis, cube, meta,
                               mol=mol, job_id=job_id)
                self.counters['write_ok'] += 1
            except Exception as e:                                  # noqa: BLE001
                self.counters['write_error'] += 1
                print(f'[cache] write failed for {method_id} '
                      f'({type(e).__name__}: {e}); the result was served but not persisted',
                      file=sys.stderr, flush=True)

        # Non-daemon is load-bearing for LocalTransport: a CLI is a short process, so a
        # daemon thread can be killed between printing the envelope and committing the row.
        # The HTTP daemon still returns immediately; only process shutdown waits for a write
        # it already promised to attempt.
        threading.Thread(target=persist, name=f'dirac-cache-{kind}', daemon=False).start()

    @staticmethod
    def _legacy_meta(kind: str, basis: str, out: HandlerResult,
                     seconds: float) -> dict:
        """Translate a validated canonical result into the existing DB writer codec."""
        result = out.result
        field = result.get('field') or {}
        grid = field.get('grid') or {}
        extrema = field.get('extrema') or {}
        box = field.get('box') or {}
        model = result.get('model') or {}
        wf = result.get('wavefunction') or {}
        raw = dict((out.cache_record or {}).get('meta') or {})
        raw.update({
            'kind': kind, 'basis': basis,
            'dims': grid.get('dimensions'),
            'spacing': grid.get('spacing_angstrom'),
            'vmin': extrema.get('min'), 'vmax': extrema.get('max'),
            'single_signed': field.get('single_signed'),
            'iso_fixed': box.get('iso_fixed'),
            'iso_sized_for': box.get('iso_sized_for'),
            'contour_closes_in_box': box.get('contour_closes_in_box'),
            'pad_used_angstrom': box.get('pad_angstrom'),
            'grid_capped': box.get('capped'), 'wall_max': box.get('wall_seconds'),
            'charges': model.get('charge_model'),
            'net_charge': model.get('net_charge'),
            'sigma_hole_representable': model.get('sigma_hole_representable'),
            'method': wf.get('method') or model.get('charge_model'),
            'converged': wf.get('converged'),
            'scf_energy_ha': wf.get('scf_energy_hartree'),
            'scf_cycles': wf.get('scf_cycles'),
            'nbasis': wf.get('n_basis_functions'),
            'homo_ev': wf.get('homo_ev'), 'lumo_ev': wf.get('lumo_ev'),
            'ecp': wf.get('ecp_elements') or out.provenance.get('ecp') or [],
            'natoms': out.provenance.get('n_atoms'),
            'charge': out.provenance.get('charge'),
            'spin': out.provenance.get('spin'),
            'scf_seconds': out.provenance.get('scf_seconds'),
            'cube_seconds': out.provenance.get('cube_seconds'),
            'toolkit_wrote_at': out.provenance.get('toolkit_wrote_at'),
            'computed_at': out.provenance.get('computed_at')
                           or datetime.now(timezone.utc).isoformat(),
            'total_seconds': seconds, 'cache': 'computed',
        })
        return {k: v for k, v in raw.items() if v is not None}

    @staticmethod
    def _as_handler_result(method_id: str, kind: str, basis: str, cube: str,
                           meta: dict) -> HandlerResult:
        """A cache hit must be INDISTINGUISHABLE from a computation, in shape and in values.

        This is where PR-03's whole lesson lands: the two paths agreed on the key set long
        before they agreed on the VALUES, and a hit that reports twelve fewer facts is a
        poorer answer served faster. So the hit is reshaped through the SAME projection the
        handler uses — imported from handlers rather than re-derived, because a second
        projection is a second home and this one would drift toward whichever path someone
        was debugging.
        """
        import catalog
        import handlers
        from invocation import InvocationContext
        spec = catalog.default_catalog().get(method_id)
        ctx = InvocationContext(method_id=method_id, spec=spec)
        return handlers.field_result(
            ctx, kind, basis, cube, meta,
            mol_n_atoms=meta.get('natoms') or meta.get('_n_atoms'),
            toolkit_wrote_at=meta.get('toolkit_wrote_at'),
            parameters_used={'basis': basis} if kind != 'mep' else {},
            cache='db')
