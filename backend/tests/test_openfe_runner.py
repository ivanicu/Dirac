from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import failures
from invocation import InvocationContext
from motif.openfe_runner import (
    _configure_analysis_environment, _digest, _gufe_component_types,
    _prepare_runtime_environment, _quantity,
    execute_openfe_edge)


class OpenFERunnerTests(unittest.TestCase):
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
        with self.assertRaises(failures.DiracInvalidParameters):
            execute_openfe_edge({
                "edge_id": "edge-1", "leg": "solvent", "transformation": {"x": 1},
                "transformation_digest": "sha256:" + "0" * 64,
            }, InvocationContext(method_id="physics.motif.openfe_edge"))

    def test_target_rbfe_leg_requires_explicit_cycle_context(self):
        with self.assertRaises(failures.DiracInvalidParameters):
            execute_openfe_edge({
                "edge_id": "edge-1", "leg": "complex", "transformation": {"x": 1},
            }, InvocationContext(method_id="physics.motif.openfe_edge"))

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
                output = execute_openfe_edge({
                    "edge_id": "edge-1", "leg": "solvent",
                    "target_ref": {"kind": "target",
                                   "id": "00000000-0000-4000-8000-000000000001"},
                    "thermodynamic_cycle_id": "00000000-0000-4000-8000-000000000002",
                    "transformation": {
                        "protocol": "fixture",
                        "component": {"__qualname__": "SolventComponent"}},
                    "resume": True,
                }, InvocationContext(method_id="physics.motif.openfe_edge"))
            self.assertEqual(output.result["estimate"], 1.5)
            self.assertEqual(output.result["uncertainty"], .25)
            self.assertEqual(output.result["scientific_status"],
                             "completed_unvalidated")
            self.assertEqual(output.result["thermodynamic_cycle_id"],
                             "00000000-0000-4000-8000-000000000002")
            self.assertEqual([role for role, _ in output.artifacts], [
                "rbfe.openfe.result", "rbfe.openfe.run_report", "rbfe.openfe.log"])
            report = json.loads(output.artifacts[1][1])
            self.assertTrue(output.provenance["physical_execution"])
            self.assertIn("not a validated RBFE claim", report["claim_boundary"])
            self.assertEqual(report["gufe_component_types"], ["SolventComponent"])
            self.assertEqual(report["analysis_bootstraps"], 1000)
            self.assertEqual(output.parameters_used["analysis_bootstraps"], 1000)

    def test_leg_metadata_must_match_serialized_physical_system(self):
        context = InvocationContext(method_id="physics.motif.openfe_edge")
        target = {"kind": "target",
                  "id": "00000000-0000-4000-8000-000000000001"}
        cycle = "00000000-0000-4000-8000-000000000002"
        with self.assertRaisesRegex(failures.DiracInvalidParameters,
                                    "must serialize a GUFE ProteinComponent"):
            execute_openfe_edge({
                "edge_id": "edge-1", "leg": "complex",
                "target_ref": target,
                "protein_structure_ref": {"kind": "protein_structure", "id": "pdb-1"},
                "thermodynamic_cycle_id": cycle,
                "transformation": {"__qualname__": "SolventComponent"},
            }, context)
        with self.assertRaisesRegex(failures.DiracInvalidParameters,
                                    "cannot contain a GUFE ProteinComponent"):
            execute_openfe_edge({
                "edge_id": "edge-1", "leg": "solvent",
                "target_ref": target, "thermodynamic_cycle_id": cycle,
                "transformation": {"__qualname__": "ProteinComponent"},
            }, context)


if __name__ == "__main__":
    unittest.main()
