from __future__ import annotations

import unittest
from concurrent.futures import Future
from types import SimpleNamespace
from unittest import mock

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
        "runtime_lock_digest": sha256_digest("runtime"),
        "executor_adapter": "kubernetes",
        "checkpoint_digests": [sha256_digest("checkpoint")],
        "featurizer_digest": sha256_digest("features"),
        "dataset_snapshot_digests": [sha256_digest("dataset")],
        "calibration_digest": sha256_digest("calibration"),
        "policy_digest": sha256_digest("policy"),
        "parameter_digest": sha256_digest("parameters"),
        "hardware_compatibility_profile": f"kubernetes:gpu:blackwell:{16 << 30}",
        "numeric_mode": "bf16",
        "production": True,
    }
    values.update(changes)
    return ExecutionIdentity.build(**values)


class ExecutionIdentityTests(unittest.TestCase):
    def test_invocation_fallback_identity_covers_root_scientific_payload(self):
        from catalog import MethodCatalog
        from invocation import InvocationService
        catalog = MethodCatalog.load().bind_versions({"molecule.embed": "identity-test"})
        service = InvocationService(catalog)
        spec = catalog.get("molecule.embed")
        handler = spec.handler()
        ethanol = service._execution_identity(
            spec, {"smiles": "CCO", "seed": 1}, handler,
            execution_adapter="inline")
        ethylamine = service._execution_identity(
            spec, {"smiles": "CCN", "seed": 1}, handler,
            execution_adapter="inline")
        self.assertNotEqual(ethanol.digest, ethylamine.digest)

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

    def test_identity_vocabulary_rejects_fictional_adapter_numeric_and_hardware(self):
        with self.assertRaisesRegex(ValueError, "executor_adapter"):
            identity(executor_adapter="banana")
        with self.assertRaisesRegex(ValueError, "numeric_mode"):
            identity(numeric_mode="wishful")
        with self.assertRaisesRegex(ValueError, "hardware_compatibility_profile"):
            identity(hardware_compatibility_profile="banana:magic")
        with self.assertRaisesRegex(ValueError, "controller-cpu"):
            identity(
                executor_adapter="local_cpu",
                container_image=None,
                hardware_compatibility_profile=
                    f"local_cpu:gpu:blackwell:{16 << 30}",
                numeric_mode="fp32")
        with self.assertRaisesRegex(ValueError, "CPU hardware"):
            identity(
                executor_adapter="local_cpu",
                container_image=None,
                hardware_compatibility_profile="local_cpu:controller-cpu:x86_64:cpu",
                numeric_mode="fp32")

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

    def test_job_submission_snapshots_payload_and_reuses_one_identity(self):
        from catalog import MethodCatalog
        from invocation import InvocationService
        from jobs import MemoryJobStore

        class DeferredExecutor:
            kind = "thread"
            supports_submission = True

            def __init__(self):
                self.route_calls = 0

            def execution_adapter_for(self, _spec):
                self.route_calls += 1
                return "local_cpu" if self.route_calls == 1 else "kubernetes"

            def submit(self, fn, *args):
                self.fn, self.args = fn, args
                return Future()

            @staticmethod
            def execute(fn, *args):
                return fn(*args)

        calls = []

        def resolver(spec, request, *, execution_adapter):
            calls.append((request["selection_query"], execution_adapter))
            return ExecutionIdentity.build(
                method_id=spec.method_id,
                method_descriptor_digest=sha256_digest("descriptor"),
                handler_source_digest=sha256_digest("handler"),
                parameter_digest=sha256_digest(request["selection_query"]),
                executor_adapter=execution_adapter)

        executor = DeferredExecutor()
        service = InvocationService(
            MethodCatalog.load(), ledger=MemoryJobStore(), executor=executor,
            execution_identity_resolver=resolver)
        payload = {
            "selection_query": "SELECT governed rows",
            "endpoint_definitions": [{
                "endpoint_key": "potency", "version": "v1",
                "canonical_unit": "nM", "measurement_type": "IC50",
            }],
            "rows": [],
            "registration": {
                "program_ref": {"kind": "program", "id":
                    "00000000-0000-4000-8000-000000000001"},
                "campaign_ref": {"kind": "campaign", "id":
                    "00000000-0000-4000-8000-000000000002"},
                "identity_policy_release_id":
                    "00000000-0000-4000-8000-000000000003",
                "data_classification": "internal",
            },
        }
        admitted = service.submit("data.motif.snapshot", payload)
        payload["selection_query"] = "SELECT attacker mutation"

        self.assertEqual(executor.args[2]["selection_query"],
                         "SELECT governed rows")
        self.assertEqual(calls, [("SELECT governed rows", "local_cpu")])
        admitted_digest = admitted["meta"]["execution_digest"]

        observed = {}

        def capture_invoke(_method_id, queued_payload, **kwargs):
            observed.update(payload=queued_payload, kwargs=kwargs)
            return {"ok": True}

        with mock.patch.object(service, "invoke", side_effect=capture_invoke):
            executor.fn(*executor.args)

        self.assertEqual(observed["payload"]["selection_query"],
                         "SELECT governed rows")
        self.assertEqual(
            observed["kwargs"]["_precomputed_execution_identity"].digest,
            admitted_digest)
        self.assertEqual(
            observed["kwargs"]["_precomputed_execution_adapter"], "local_cpu")
        self.assertEqual(calls, [("SELECT governed rows", "local_cpu")])
        self.assertEqual(executor.route_calls, 1)

    def test_stateful_route_hook_is_called_once_per_admission(self):
        from catalog import MethodCatalog
        from invocation import InvocationService

        class StatefulExecutor:
            kind = "remote"
            supports_submission = False

            def __init__(self):
                self.calls = 0

            def execution_adapter_for(self, _spec):
                self.calls += 1
                return "inline" if self.calls == 1 else "kubernetes"

            @staticmethod
            def execute(fn, *args):
                return fn(*args)

        executor = StatefulExecutor()
        service = InvocationService(MethodCatalog.load(), executor=executor)
        result = service.invoke("molecule.embed", {"smiles": "CCO"})
        self.assertTrue(result["ok"])
        self.assertEqual(executor.calls, 1)
        self.assertEqual(result["meta"]["execution_identity"]["executor_adapter"],
                         "inline")

    def test_unknown_resource_and_missing_adapter_contracts_fail_closed(self):
        from catalog import MethodCatalog
        from invocation import InvocationService

        service = InvocationService(MethodCatalog.load())
        unknown_resource = SimpleNamespace(
            method_id="attack.unknown_resource",
            execution={"resource_class": "gup",
                       "supported_adapters": ["inline"]})
        with self.assertRaisesRegex(Exception, "cannot run"):
            service._require_executor_compatibility(unknown_resource)
        missing_adapters = SimpleNamespace(
            method_id="attack.missing_adapters",
            execution={"resource_class": "cpu"})
        with self.assertRaisesRegex(Exception, "cannot run"):
            service._require_executor_compatibility(missing_adapters)


if __name__ == "__main__":
    unittest.main()
