from __future__ import annotations

import unittest

from execution_control.identity import ExecutionIdentity, sha256_digest
from execution_control.seeds import derive_seed, seed_scope_digest


def identity(**changes):
    values = {
        "method_id": "ml.motif.predict",
        "method_descriptor_digest": sha256_digest("descriptor"),
        "handler_source_digest": sha256_digest("handler"),
        "repository_commit": "a" * 40,
        "container_image": "registry.local/motif@sha256:" + "b" * 64,
        "dependency_lock_digest": sha256_digest("lock"),
        "checkpoint_digests": [sha256_digest("checkpoint")],
        "featurizer_digest": sha256_digest("features"),
        "dataset_snapshot_digests": [sha256_digest("dataset")],
        "calibration_digest": sha256_digest("calibration"),
        "policy_digest": sha256_digest("policy"),
        "parameter_digest": sha256_digest("parameters"),
        "hardware_compatibility_profile": "cuda-13-blackwell",
        "numeric_mode": "bf16",
        "production": True,
    }
    values.update(changes)
    return ExecutionIdentity.build(**values)


class ExecutionIdentityTests(unittest.TestCase):
    def test_order_does_not_change_identity_or_cache_key(self):
        first = identity(
            checkpoint_digests=[sha256_digest("b"), sha256_digest("a")],
            dataset_snapshot_digests=[sha256_digest("d2"), sha256_digest("d1")],
        )
        second = identity(
            checkpoint_digests=[sha256_digest("a"), sha256_digest("b")],
            dataset_snapshot_digests=[sha256_digest("d1"), sha256_digest("d2")],
        )
        self.assertEqual(first.digest, second.digest)
        self.assertEqual(
            first.cache_key({"b": 2, "a": 1}, seed_scope_digest=None),
            second.cache_key({"a": 1, "b": 2}, seed_scope_digest=None),
        )

    def test_each_scientific_release_invalidates_identity(self):
        baseline = identity().digest
        for field in (
            "featurizer_digest",
            "calibration_digest",
            "policy_digest",
            "parameter_digest",
        ):
            with self.subTest(field=field):
                self.assertNotEqual(baseline, identity(**{field: sha256_digest(field)}).digest)
        self.assertNotEqual(
            baseline,
            identity(checkpoint_digests=[sha256_digest("new-checkpoint")]).digest,
        )

    def test_production_rejects_mutable_or_incomplete_runtime(self):
        with self.assertRaisesRegex(ValueError, "immutable OCI"):
            identity(container_image="registry.local/motif:latest")
        with self.assertRaisesRegex(ValueError, "misses"):
            identity(repository_commit=None)

    def test_optional_components_are_explicit_nulls(self):
        candidate = ExecutionIdentity.build(
            method_id="fields.mep",
            method_descriptor_digest=sha256_digest("descriptor"),
            handler_source_digest=sha256_digest("handler"),
        )
        self.assertIn("calibration_digest", candidate.to_dict())
        self.assertIsNone(candidate.to_dict()["calibration_digest"])

    def test_seed_depends_on_stable_scope_not_iteration_order(self):
        one = {"cycle": "c1", "shard_key": "series:A", "replicate": 0}
        reordered = {"replicate": 0, "shard_key": "series:A", "cycle": "c1"}
        self.assertEqual(seed_scope_digest(one), seed_scope_digest(reordered))
        self.assertEqual(derive_seed(1729, one), derive_seed(1729, reordered))
        self.assertNotEqual(
            derive_seed(1729, one),
            derive_seed(1729, {**one, "shard_key": "series:B"}),
        )


if __name__ == "__main__":
    unittest.main()
