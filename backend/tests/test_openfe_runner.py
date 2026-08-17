from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import failures
from artifacts import MemoryArtifactStore
from catalog import MethodCatalog
from invocation import HandlerResult, InvocationContext, InvocationService
from motif.openfe_runner import (
    attest_openfe_edge_admission,
    _configure_analysis_environment, _digest, _extract_gufe_native_objects, _gufe_component_types,
    _prepare_runtime_environment, _quantity,
    execute_openfe_edge)


class _CurrentCampaign:
    def assert_campaign_generation(self, campaign_id, scientific_generation,
                                   scientific_digest, actor):
        assert actor == {"kind": "human", "id": "chemist-1"}
        if (campaign_id != "00000000-0000-4000-8000-000000000010"
                or scientific_generation != 1
                or scientific_digest != "sha256:" + "c" * 64):
            raise failures.DiracInvalidParameters("stale campaign")
        return {"scientific_generation": 1,
                "scientific_digest": scientific_digest}


class _MutableCampaign(_CurrentCampaign):
    def __init__(self):
        self.current_generation = 1
        self.calls = 0

    def assert_campaign_generation(self, campaign_id, scientific_generation,
                                   scientific_digest, actor):
        self.calls += 1
        if scientific_generation != self.current_generation:
            raise failures.DiracInvalidParameters("stale campaign")
        return super().assert_campaign_generation(
            campaign_id, scientific_generation, scientific_digest, actor)


class _PinnedResultCache:
    def __init__(self, result: HandlerResult):
        self.result = result
        self.lookups = 0

    def lookup(self, _method_id, _payload, *, execution_digest):
        assert execution_digest.startswith("sha256:")
        self.lookups += 1
        return self.result


class _CacheOnlyKubernetesExecutor:
    kind = "remote"
    adapter_kind = "kubernetes"
    supports_submission = False

    @staticmethod
    def execution_adapter_for(_spec):
        return "kubernetes"

    @staticmethod
    def execute(*_args, **_kwargs):
        raise AssertionError("a cache-hit admission test must never execute a worker")


class OpenFERunnerTests(unittest.TestCase):
    @staticmethod
    def authorized(payload: dict) -> tuple[dict, InvocationContext]:
        value = dict(payload)
        value.setdefault("target_ref", {
            "kind": "target", "id": "00000000-0000-4000-8000-000000000001"})
        value.setdefault("protein_structure_ref", {
            "kind": "protein_structure", "id": "00000000-0000-4000-8000-000000000003"})
        value.setdefault("thermodynamic_cycle_id",
                         "00000000-0000-4000-8000-000000000002")
        value.setdefault("repeat_index", 1)
        value.setdefault("seed", 12345)
        transformation_digest = _digest(value["transformation"])
        campaign_binding = {
            "schema_version": "rbfe-campaign-binding.v2",
            "campaign_id": "00000000-0000-4000-8000-000000000010",
            "campaign_scientific_generation": 1,
            "campaign_scientific_digest": "sha256:" + "c" * 64,
            "prepared_system_id": "00000000-0000-4000-8000-000000000011",
            "network_digest": "sha256:" + "d" * 64,
            "verdict": "CONFIRMED",
        }
        campaign_binding["digest"] = _digest(campaign_binding)
        spec = {
            "schema_version": "1.0", "kind": "rbfe_edge_execution_spec",
            "edge_id": value["edge_id"], "target_ref": value["target_ref"],
            "protein_structure_ref": value["protein_structure_ref"],
            "thermodynamic_cycle_id": value["thermodynamic_cycle_id"],
            "ligand_charge_digest": value["ligand_charge_digest"],
            "complex_transformation_digest": transformation_digest,
            "solvent_transformation_digest": transformation_digest,
            "campaign_binding": campaign_binding,
            "execution_matrix": [{
                "leg": value["leg"], "repeat_index": value["repeat_index"],
                "orchestration_seed": value["seed"]}],
        }
        spec["digest"] = _digest(spec)
        store = MemoryArtifactStore()
        artifact = store.put(json.dumps(
            spec, sort_keys=True, separators=(",", ":")).encode(),
            role="rbfe.edge_spec", media_type="application/json")
        value["edge_spec_ref"] = {
            "kind": "artifact", "id": artifact.id,
            "sha256": "sha256:" + artifact.sha256}
        return value, InvocationContext(
            method_id="physics.motif.openfe_edge", artifact_reader=store,
            actor={"kind": "human", "id": "chemist-1"},
            rbfe_reference_resolver=_CurrentCampaign())

    def test_ambertools_shims_do_not_require_worker_bash(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            wrapped = runtime / "bin" / "wrapped_progs"
            wrapped.mkdir(parents=True)
            (wrapped / "antechamber").write_bytes(b"compiled")
            env, linked = _prepare_runtime_environment(runtime, root / "work", {})
            shim = root / "work" / "amberhome" / "bin" / "antechamber"
            self.assertTrue(shim.is_symlink())
            self.assertEqual(linked, ["antechamber"])
            self.assertEqual(env["AMBERHOME"], str(root / "work" / "amberhome"))
            self.assertTrue(env["PATH"].startswith(str(shim.parent)))

    def test_official_gufe_graph_array_has_stable_digest(self):
        graph = [["Transformation-key", {":gufe-key:": "Transformation-key"}]]
        self.assertEqual(_digest(graph), _digest(json.loads(json.dumps(graph))))

    def test_gufe_component_types_are_read_from_graph_array(self):
        graph = [["ProteinComponent-key", {
            "__qualname__": "ProteinComponent", "nested": {
                "__qualname__": "ChemicalSystem"}}]]
        self.assertEqual(_gufe_component_types(graph),
                         {"ProteinComponent", "ChemicalSystem"})

    def test_native_openfe_objects_are_inventory_artifacts(self):
        graph = [{"__qualname__": "LigandNetwork", ":gufe-key:": "ln-1",
                  "nested": {"__qualname__": "Transformation", ":gufe-key:": "t-1"}}]
        objects = _extract_gufe_native_objects(graph)
        self.assertEqual([row["kind"] for row in objects], ["LigandNetwork", "Transformation"])
        self.assertTrue(all(row["digest"].startswith("sha256:") for row in objects))

    def test_quantity_decoder_handles_gufe_shapes(self):
        self.assertEqual(_quantity({"magnitude": -1.25, "unit": "kilocalorie_per_mole"}),
                         (-1.25, "kilocalorie_per_mole"))
        self.assertEqual(_quantity({"tag": {"m": .2, "unit": "kcal/mol"}}),
                         (.2, "kcal/mol"))

    def test_nondefault_analysis_bootstraps_are_explicit_in_child_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = {"PYTHONPATH": "/runtime"}
            _configure_analysis_environment(env, root, 20)
            self.assertEqual(env["DIRAC_OPENFE_ANALYSIS_BOOTSTRAPS"], "20")
            self.assertEqual(env["PYTHONPATH"], f"{root}:/runtime")
            self.assertIn("MultistateEquilFEAnalysis", (root / "sitecustomize.py").read_text())

    def test_digest_mismatch_refuses_before_process(self):
        payload, context = self.authorized({
            "edge_id": "edge-1", "leg": "solvent", "transformation": {"x": 1},
            "ligand_charge_digest": "sha256:" + "1" * 64,
            "charge_invariant": {"edge_id": "edge-1", "digest": "sha256:" + "1" * 64},
        })
        payload["transformation_digest"] = "sha256:" + "0" * 64
        with self.assertRaises(failures.DiracInvalidParameters):
            execute_openfe_edge(payload, context)

    def test_target_leg_cannot_bypass_server_owned_preflight(self):
        with self.assertRaisesRegex(failures.DiracInvalidParameters,
                                    "server-owned rbfe.edge_spec"):
            execute_openfe_edge({
                "edge_id": "edge-1", "leg": "solvent", "transformation": {"x": 1},
                "ligand_charge_digest": "sha256:" + "1" * 64,
                "charge_invariant": {"edge_id": "edge-1", "digest": "sha256:" + "1" * 64},
            }, InvocationContext(method_id="physics.motif.openfe_edge"))

    def test_target_rbfe_leg_requires_explicit_cycle_context(self):
        payload, context = self.authorized({
            "edge_id": "edge-1", "leg": "complex", "transformation": {"x": 1},
            "ligand_charge_digest": "sha256:" + "1" * 64,
            "charge_invariant": {"edge_id": "edge-1", "digest": "sha256:" + "1" * 64},
        })
        payload.pop("target_ref")
        payload.pop("thermodynamic_cycle_id")
        with self.assertRaises(failures.DiracInvalidParameters):
            execute_openfe_edge(payload, context)

    def test_api_admission_attests_exact_campaign_generation(self):
        payload, context = self.authorized({
            "edge_id": "edge-1", "leg": "solvent",
            "transformation": {"__qualname__": "SolventComponent"},
            "ligand_charge_digest": "sha256:" + "1" * 64,
            "charge_invariant": {
                "edge_id": "edge-1", "digest": "sha256:" + "1" * 64},
        })
        attestation = attest_openfe_edge_admission(payload, context)
        self.assertEqual(attestation["verdict"], "CONFIRMED")
        self.assertEqual(attestation["actor"],
                         {"kind": "human", "id": "chemist-1"})
        self.assertEqual(attestation["edge_spec_sha256"],
                         payload["edge_spec_ref"]["sha256"])

    def test_stale_campaign_refuses_before_replaying_old_generation_cache(self):
        payload, authorized = self.authorized({
            "edge_id": "edge-1", "leg": "solvent",
            "transformation": {"__qualname__": "SolventComponent"},
            "ligand_charge_digest": "sha256:" + "1" * 64,
            "charge_invariant": {
                "edge_id": "edge-1", "digest": "sha256:" + "1" * 64},
        })
        loaded = MethodCatalog.load().get("physics.motif.openfe_edge")
        descriptor = json.loads(json.dumps(loaded.descriptor))
        # The production descriptor intentionally does not cache long-running
        # OpenFE attempts today. This exercises the generic boundary so enabling
        # caching later cannot re-open the historical-generation bypass.
        descriptor["execution"]["cacheable"] = True
        catalog = MethodCatalog({loaded.method_id: type(loaded)(
            method_id=loaded.method_id, summary=loaded.summary,
            descriptor=descriptor, handler_ref=loaded.handler_ref,
            estimate_ref=loaded.estimate_ref, artifacts=loaded.artifacts,
            version=loaded.version)})
        transformation_digest = _digest(payload["transformation"])
        cached = HandlerResult(result={
            "edge_id": payload["edge_id"], "leg": payload["leg"],
            "engine": "OpenFE", "engine_version": "1.11.1",
            "transformation_digest": transformation_digest,
            "ligand_charge_digest": payload["ligand_charge_digest"],
            "target_ref": payload["target_ref"],
            "protein_structure_ref": payload["protein_structure_ref"],
            "thermodynamic_cycle_id": payload["thermodynamic_cycle_id"],
            "repeat_index": payload["repeat_index"],
            "estimate": -1.0, "uncertainty": 0.2, "unit": "kcal/mol",
            "scientific_status": "completed_unvalidated",
            "result_digest": "sha256:" + "9" * 64,
        })
        cache = _PinnedResultCache(cached)
        resolver = _MutableCampaign()
        service = InvocationService(
            catalog, cache=cache, executor=_CacheOnlyKubernetesExecutor(),
            artifact_reader=authorized.artifact_reader,
            rbfe_reference_resolver=resolver)
        actor = {"kind": "human", "id": "chemist-1"}

        first = service.invoke(
            loaded.method_id, payload, actor=actor,
            _preopened_job_id="job-old-generation")
        self.assertTrue(first["ok"])
        self.assertEqual(first["meta"]["cache"], "db")
        self.assertEqual(cache.lookups, 1)
        self.assertEqual(resolver.calls, 1)

        resolver.current_generation = 2
        replay = service.invoke(
            loaded.method_id, payload, actor=actor,
            _preopened_job_id="job-replay")
        self.assertFalse(replay["ok"])
        self.assertEqual(replay["error"]["code"], "INVALID_PARAMETERS")
        self.assertEqual(cache.lookups, 1, "stale replay reached cache.lookup")
        self.assertEqual(resolver.calls, 2)
        self.assertEqual(service.counters["cache_hit"], 1)

    def test_missing_campaign_resolver_refuses_before_cache_lookup(self):
        payload, authorized = self.authorized({
            "edge_id": "edge-1", "leg": "solvent",
            "transformation": {"__qualname__": "SolventComponent"},
            "ligand_charge_digest": "sha256:" + "1" * 64,
            "charge_invariant": {
                "edge_id": "edge-1", "digest": "sha256:" + "1" * 64},
        })
        loaded = MethodCatalog.load().get("physics.motif.openfe_edge")
        descriptor = json.loads(json.dumps(loaded.descriptor))
        descriptor["execution"]["cacheable"] = True
        catalog = MethodCatalog({loaded.method_id: type(loaded)(
            method_id=loaded.method_id, summary=loaded.summary,
            descriptor=descriptor, handler_ref=loaded.handler_ref,
            estimate_ref=loaded.estimate_ref, artifacts=loaded.artifacts,
            version=loaded.version)})
        cache = mock.Mock()
        service = InvocationService(
            catalog, cache=cache, executor=_CacheOnlyKubernetesExecutor(),
            artifact_reader=authorized.artifact_reader,
            rbfe_reference_resolver=None)

        response = service.invoke(
            loaded.method_id, payload,
            actor={"kind": "human", "id": "chemist-1"},
            _preopened_job_id="job-no-resolver")

        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "UNSUPPORTED")
        cache.lookup.assert_not_called()

    def test_isolated_worker_requires_exact_server_attestation(self):
        payload, controller = self.authorized({
            "edge_id": "edge-1", "leg": "solvent",
            "transformation": {"__qualname__": "SolventComponent"},
            "ligand_charge_digest": "sha256:" + "1" * 64,
            "charge_invariant": {
                "edge_id": "edge-1", "digest": "sha256:" + "1" * 64},
        })
        attestation = attest_openfe_edge_admission(payload, controller)
        worker = InvocationContext(
            method_id="physics.motif.openfe_edge",
            artifact_reader=controller.artifact_reader,
            actor={"kind": "human", "id": "chemist-1"})
        with self.assertRaisesRegex(failures.DiracUnsupported,
                                    "server-sealed Campaign"):
            execute_openfe_edge(payload, worker)
        worker.server_attestations["rbfe_campaign_generation"] = {
            **attestation, "campaign_scientific_generation": 99}
        with self.assertRaisesRegex(failures.DiracUnsupported,
                                    "server-sealed Campaign"):
            execute_openfe_edge(payload, worker)
        worker.server_attestations["rbfe_campaign_generation"] = attestation
        with self.assertRaisesRegex(failures.DiracInternal,
                                    "fenced Motif worker attempt"):
            execute_openfe_edge(payload, worker)

    def test_pinned_quickrun_result_becomes_typed_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "runtime" / "bin" / "openfe"
            executable.parent.mkdir(parents=True)
            executable.write_text(
                "#!/usr/bin/env python3\n"
                "import json,sys\n"
                "out=sys.argv[sys.argv.index('-o')+1]\n"
                "json.dump({'estimate':{'magnitude':1.5,'unit':'kcal/mol'},"
                "'uncertainty':{'magnitude':.25,'unit':'kcal/mol'},"
                "'protocol_result':{},'unit_results':{}},open(out,'w'))\n"
                "print('fake quickrun completed')\n")
            executable.chmod(0o755)
            attempt = root / "attempt"
            with mock.patch.dict(os.environ, {
                    "DIRAC_MOTIF_ATTEMPT_DIR": str(attempt),
                    "DIRAC_OPENFE_EXECUTABLE": str(executable)}, clear=False):
                payload, context = self.authorized({
                    "edge_id": "edge-1", "leg": "solvent",
                    "target_ref": {"kind": "target",
                                   "id": "00000000-0000-4000-8000-000000000001"},
                    "thermodynamic_cycle_id": "00000000-0000-4000-8000-000000000002",
                    "ligand_charge_digest": "sha256:" + "1" * 64,
                    "charge_invariant": {"edge_id": "edge-1", "digest": "sha256:" + "1" * 64},
                    "transformation": {
                        "protocol": "fixture",
                        "component": {"__qualname__": "SolventComponent"}},
                    "resume": True,
                })
                output = execute_openfe_edge(payload, context)
            self.assertEqual(output.result["estimate"], 1.5)
            self.assertEqual(output.result["uncertainty"], .25)
            self.assertEqual(output.result["scientific_status"],
                             "completed_unvalidated")
            self.assertEqual(output.result["thermodynamic_cycle_id"],
                             "00000000-0000-4000-8000-000000000002")
            self.assertEqual([role for role, _ in output.artifacts], [
                "rbfe.openfe.result", "rbfe.openfe.run_report",
                "rbfe.openfe.native_objects", "rbfe.openfe.log"])
            report = json.loads(output.artifacts[1][1])
            self.assertTrue(output.provenance["physical_execution"])
            self.assertIn("not a validated RBFE claim", report["claim_boundary"])
            self.assertEqual(report["gufe_component_types"], ["SolventComponent"])
            self.assertEqual(report["analysis_bootstraps"], 1000)
            self.assertEqual(output.parameters_used["analysis_bootstraps"], 1000)

    def test_leg_metadata_must_match_serialized_physical_system(self):
        target = {"kind": "target",
                  "id": "00000000-0000-4000-8000-000000000001"}
        cycle = "00000000-0000-4000-8000-000000000002"
        complex_payload, complex_context = self.authorized({
            "edge_id": "edge-1", "leg": "complex", "target_ref": target,
            "protein_structure_ref": {"kind": "protein_structure", "id": "pdb-1"},
            "thermodynamic_cycle_id": cycle,
            "ligand_charge_digest": "sha256:" + "1" * 64,
            "charge_invariant": {"edge_id": "edge-1", "digest": "sha256:" + "1" * 64},
            "transformation": {"__qualname__": "SolventComponent"},
        })
        with self.assertRaisesRegex(failures.DiracInvalidParameters,
                                    "must serialize a GUFE ProteinComponent"):
            execute_openfe_edge(complex_payload, complex_context)
        solvent_payload, solvent_context = self.authorized({
            "edge_id": "edge-1", "leg": "solvent", "target_ref": target,
            "thermodynamic_cycle_id": cycle,
            "ligand_charge_digest": "sha256:" + "1" * 64,
            "charge_invariant": {"edge_id": "edge-1", "digest": "sha256:" + "1" * 64},
            "transformation": {"__qualname__": "ProteinComponent"},
        })
        with self.assertRaisesRegex(failures.DiracInvalidParameters,
                                    "cannot contain a GUFE ProteinComponent"):
            execute_openfe_edge(solvent_payload, solvent_context)


if __name__ == "__main__":
    unittest.main()
