"""Fast regression tests for the whole surface wall-clock budget."""
from __future__ import annotations

import pathlib
import sys
import time
import unittest
from unittest import mock

import numpy as np

_BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from physics import mep_surface as ms  # noqa: E402


class SurfaceBudgetTests(unittest.TestCase):
    def test_zero_budget_refuses_before_molecule_preparation(self):
        with mock.patch.object(ms, '_prepare', side_effect=AssertionError(
                'zero-budget request reached molecule preparation')):
            with self.assertRaises(ms.PhysicsBudgetExceeded):
                ms.compute_surface_mep('unused', max_seconds=0)
            with self.assertRaises(ms.PhysicsBudgetExceeded):
                ms.mep_at_points('unused', [[0.0, 0.0, 3.0]], max_seconds=0)

    def test_surface_search_checks_the_same_expired_deadline(self):
        field = mock.Mock()
        with self.assertRaisesRegex(ms.PhysicsBudgetExceeded, 'surface search'):
            ms._outer_isosurface_points(
                field, np.array([[0.0, 0.0, 0.0]]), 0.001, 8,
                deadline=time.time() - 1, max_seconds=1)
        field.rho.assert_not_called()

    def test_potential_evaluation_checks_before_integrals(self):
        field = object.__new__(ms._Field)
        field.mol = mock.Mock()
        field.mol.nao = 1
        field.nao = 1
        field.dm = np.ones((1, 1))
        with self.assertRaisesRegex(ms.PhysicsBudgetExceeded,
                                   'potential evaluation'):
            field.mep(np.array([[0.0, 0.0, 3.0]]),
                      deadline=time.time() - 1, max_seconds=1)
        field.mol.intor.assert_not_called()

    def test_refused_job_records_elapsed_seconds(self):
        import catalog
        import invocation
        import jobs

        molfile = (_BACKEND.parent / 'contracts/golden/fixtures/o_3.mol').read_text()
        ledger = jobs.MemoryJobStore()
        service = invocation.InvocationService(catalog.MethodCatalog.load(), ledger=ledger)
        envelope = service.invoke('surface.mep', {
            'molecule': {'kind': 'molfile', 'content': molfile, 'dimensionality': 3},
            'parameters': {'basis': 'sto-3g'},
        }, budget_seconds=1e-9)
        self.assertFalse(envelope['ok'])
        self.assertEqual(envelope['error']['code'], 'BUDGET')
        job = ledger.get(envelope['meta']['job_id'])
        self.assertEqual(job['state'], 'failed')
        self.assertIsNotNone(job['seconds'])
        self.assertGreaterEqual(job['seconds'], 0)


if __name__ == '__main__':
    unittest.main()
