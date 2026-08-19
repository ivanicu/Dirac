from __future__ import annotations

import unittest

from research.metrics import BoundedMetrics, model_family, status_class


class ResearchLoopMetricsTests(unittest.TestCase):
    def test_only_registered_bounded_labels_can_be_rendered(self):
        metrics = BoundedMetrics()
        metrics.counter("dirac_research_loop_transition_total", {
            "from_stage": "reason", "to_stage": "wait_job",
        })
        metrics.contribute("dirac_research_loop_budget_remaining", "run-internal", 3, {
            "resource": "reasoner_calls",
        })
        rendered = metrics.render()
        self.assertIn('from_stage="reason",to_stage="wait_job"', rendered)
        self.assertIn('resource="reasoner_calls"} 3', rendered)
        self.assertNotIn("run-internal", rendered)
        with self.assertRaises(ValueError):
            metrics.counter("dirac_research_loop_total", {
                "state": "active", "stage": "reason", "run_id": "forbidden",
            })
        with self.assertRaises(ValueError):
            metrics.counter("unknown_metric")

    def test_observations_emit_count_and_sum_and_models_are_bucketed(self):
        metrics = BoundedMetrics()
        metrics.observe("dirac_research_loop_reasoner_seconds", 1.25, {
            "profile_id": "qwen-cloud-primary", "model_family": "qwen",
        })
        rendered = metrics.render()
        self.assertIn("dirac_research_loop_reasoner_seconds_count", rendered)
        self.assertIn("dirac_research_loop_reasoner_seconds_sum", rendered)
        self.assertEqual(model_family("qwen-plus"), "qwen")
        self.assertEqual(model_family("custom-model-name"), "other_openai_compatible")
        self.assertEqual(status_class(429), "4xx")
        self.assertEqual(status_class("transport_error"), "transport_error")


if __name__ == "__main__":
    unittest.main()
