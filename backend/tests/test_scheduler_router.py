from __future__ import annotations

import copy
import unittest
from unittest import mock

from backend.tests.test_motif_contracts import EXECUTION_REQUEST
from execution_control.protocol import AllocationStatus
from execution_control.router import SchedulerRouter


class SchedulerRouterTests(unittest.TestCase):
    def test_routes_only_to_declared_backend(self):
        adapter = mock.Mock(kind="kubernetes")
        expected = AllocationStatus("ns/job", "pending", {})
        adapter.submit.return_value = expected
        router = SchedulerRouter([adapter])
        request = copy.deepcopy(EXECUTION_REQUEST)
        request["placement"]["backend"] = "kubernetes"
        self.assertEqual(router.submit(request), expected)
        adapter.submit.assert_called_once_with(request)
        request["placement"]["backend"] = "slurm"
        with self.assertRaisesRegex(ValueError, "not configured"):
            router.submit(request)

    def test_duplicate_backend_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            SchedulerRouter([mock.Mock(kind="kubernetes"),
                             mock.Mock(kind="kubernetes")])


if __name__ == "__main__":
    unittest.main()
