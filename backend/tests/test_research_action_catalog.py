from __future__ import annotations

import copy
import json
import pathlib
import tempfile
import unittest

from research.action_catalog import ActionCatalogError, ResearchActionCatalog


ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCE = ROOT / "contracts/research/action-templates"


class ResearchActionCatalogTests(unittest.TestCase):
    def test_catalog_is_bounded_complete_and_model_projection_hides_commands(self):
        catalog = ResearchActionCatalog.load()
        self.assertEqual(set(catalog), {
            "fep.run_selected_edge.v1", "fep.prepare_selected_edge.v1",
            "fep.replan_network.v1", "fep.stop.v1",
            "fep.defer_for_experiment.v1",
        })
        self.assertTrue(catalog.digest.startswith("sha256:"))
        projection = json.dumps(catalog.to_model_catalog())
        self.assertNotIn("command_id", projection)
        self.assertNotIn("physics.rbfe", projection)
        resolved_commands = {
            template["execution"]["command_id"]
            for template in catalog.values()
            if template["execution"]["command_id"] is not None
        }
        self.assertEqual(resolved_commands, {
            "physics.rbfe-run.start",
            "physics.rbfe-system.prepare",
            "physics.rbfe-network",
        })
        self.assertFalse(any(command.startswith("program.")
                             for command in resolved_commands))

    def test_registry_refuses_unlisted_or_id_mismatched_templates(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = pathlib.Path(temporary)
            for path in SOURCE.glob("*.json"):
                (target / path.name).write_bytes(path.read_bytes())
            (target / "unlisted.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ActionCatalogError, "unlisted"):
                ResearchActionCatalog.load(target)
            (target / "unlisted.json").unlink()
            path = target / "fep.stop.v1.json"
            document = json.loads(path.read_text())
            document["template_id"] = "fep.changed.v1"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ActionCatalogError, "id_mismatch"):
                ResearchActionCatalog.load(target)


if __name__ == "__main__":
    unittest.main()
