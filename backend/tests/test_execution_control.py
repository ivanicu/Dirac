from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from artifact_backends.local_cas import LocalCAS
from execution_control.completion import validate_output_manifest
from execution_control.leases import InMemoryLeaseStore, StaleAttemptError
from execution_control.protocol import CancellationToken


class ExecutionControlTests(unittest.TestCase):
    def test_takeover_fences_late_worker(self):
        store = InMemoryLeaseStore()
        now = datetime(2026, 8, 12, tzinfo=timezone.utc)
        first = store.claim("job-1", "worker-a", lease_seconds=10, now=now)
        second = store.claim("job-1", "worker-b", lease_seconds=10, now=now + timedelta(seconds=11))
        self.assertGreater(second.fencing_token, first.fencing_token)
        with self.assertRaises(StaleAttemptError):
            store.complete(first, now=now + timedelta(seconds=12))
        self.assertEqual(store.complete(second, now=now + timedelta(seconds=12)).state, "succeeded")

    def test_live_lease_cannot_be_double_claimed(self):
        store = InMemoryLeaseStore()
        now = datetime(2026, 8, 12, tzinfo=timezone.utc)
        store.claim("job-1", "worker-a", lease_seconds=10, now=now)
        with self.assertRaisesRegex(RuntimeError, "live lease"):
            store.claim("job-1", "worker-b", lease_seconds=10, now=now)

    def test_cooperative_cancellation_is_idempotent(self):
        token = CancellationToken()
        self.assertTrue(token.request("chemist stopped run"))
        self.assertFalse(token.request("duplicate"))
        with self.assertRaisesRegex(Exception, "chemist stopped run"):
            token.check()

    def test_completion_requires_verified_declared_artifact_and_current_token(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalCAS(directory)
            blob = store.put_chunks([b"result"])
            manifest = {
                "schema_version": "1.0",
                "job_id": "00000000-0000-4000-8000-000000000001",
                "attempt_id": "00000000-0000-4000-8000-000000000002",
                "fencing_token": 3,
                "execution_digest": "sha256:" + "a" * 64,
                "artifacts": [{
                    "role": "motif.predictions", "sha256": blob.digest,
                    "size_bytes": blob.size_bytes, "media_type": "application/json",
                    "required": True,
                }],
                "result_summary": {}, "warnings": [],
                "started_at": "2026-08-12T00:00:00Z",
                "finished_at": "2026-08-12T00:01:00Z",
            }
            validate_output_manifest(
                manifest, expected_execution_digest=manifest["execution_digest"],
                expected_fencing_token=3, required_roles=["motif.predictions"],
                artifact_reader=store,
            )
            with self.assertRaisesRegex(Exception, "STALE_ATTEMPT_RESULT"):
                validate_output_manifest(
                    manifest, expected_execution_digest=manifest["execution_digest"],
                    expected_fencing_token=4, required_roles=["motif.predictions"],
                    artifact_reader=store,
                )
            with self.assertRaisesRegex(Exception, "missing"):
                validate_output_manifest(
                    manifest, expected_execution_digest=manifest["execution_digest"],
                    expected_fencing_token=3, required_roles=["motif.poses"],
                    artifact_reader=store,
                )


if __name__ == "__main__":
    unittest.main()
