from __future__ import annotations

import unittest
from unittest import mock

from execution_control.allocation_store import DurableSchedulerAdapter, _database_state
from execution_control.protocol import AllocationStatus


class AllocationStoreTests(unittest.TestCase):
    def test_scheduler_states_map_fail_closed(self):
        self.assertEqual(_database_state("suspended"), "pending")
        self.assertEqual(_database_state("unknown"), "lost")
        with self.assertRaisesRegex(ValueError, "unknown scheduler state"):
            _database_state("made_up")

    def test_durable_adapter_persists_submission_and_reconcile(self):
        adapter = mock.Mock(kind="kubernetes")
        submitted = AllocationStatus("dirac-motif/job-1", "suspended", {"queue": "motif"})
        completed = AllocationStatus("dirac-motif/job-1", "succeeded", {"succeeded": 1})
        adapter.submit.return_value = submitted
        adapter.reconcile.return_value = completed
        store = mock.Mock()
        store.active_scheduler_ids.return_value = ("dirac-motif/job-1",)
        durable = DurableSchedulerAdapter(adapter, store)
        request = {"attempt_id": "attempt-1"}
        self.assertEqual(durable.submit(request), submitted)
        store.record_submission.assert_called_once_with(
            request, submitted, backend="kubernetes")
        self.assertEqual(durable.reconcile_active(), (completed,))
        store.update_status.assert_called_once_with(completed, backend="kubernetes")

    def test_submission_without_durable_record_cancels_scheduler_work(self):
        adapter = mock.Mock(kind="kubernetes")
        status = AllocationStatus("dirac-motif/job-1", "pending", {})
        adapter.submit.return_value = status
        store = mock.Mock()
        store.record_submission.side_effect = RuntimeError("database unavailable")
        durable = DurableSchedulerAdapter(adapter, store)
        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            durable.submit({"attempt_id": "attempt-1"})
        adapter.request_cancel.assert_called_once_with(status.allocation_id,
                                                       grace_seconds=0)

    def test_cancel_is_mirrored_before_job_disappears(self):
        adapter = mock.Mock(kind="kubernetes")
        store = mock.Mock()
        durable = DurableSchedulerAdapter(adapter, store)
        durable.request_cancel("dirac-motif/job-1", grace_seconds=30)
        adapter.request_cancel.assert_called_once_with("dirac-motif/job-1",
                                                       grace_seconds=30)
        persisted = store.update_status.call_args.args[0]
        self.assertEqual(persisted.state, "cancelled")


if __name__ == "__main__":
    unittest.main()
