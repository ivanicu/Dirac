from __future__ import annotations

import unittest

import numpy as np

from motif.advanced_acquisition import (botorch_qehvi, greedy_diversity,
                                        information_value, selection_sensitivity)


class AdvancedAcquisitionTests(unittest.TestCase):
    def test_qehvi_voi_diversity_and_sensitivity_are_explicit(self):
        train_x = np.asarray([[0., 0.], [1., 0.], [0., 1.], [1., 1.], [.5, .5]])
        train_y = np.asarray([[0., 0.], [1., .2], [.2, 1.], [.8, .8], [.6, .7]])
        candidates = np.asarray([[.1, .9], [.9, .1], [.7, .7]])
        release = botorch_qehvi(train_x, train_y, candidates,
                                reference_point=[-0.1, -0.1], mc_samples=16, seed=3)
        self.assertEqual(len(release["scores"]), 3)
        self.assertTrue(all(value >= 0 for value in release["scores"]))
        voi = information_value(release["posterior_variance"], [1., 2., 1.])
        self.assertEqual(len(voi), 3)
        diversity = greedy_diversity(np.eye(3), order=[2, 0, 1])
        self.assertEqual(diversity[2], 1.0)
        rows = [{"proposal_id": str(index), "objectives": {"a": float(index),
                 "b": float(4 - index)}, "constraints": {}, "components": {}}
                for index in range(5)]
        sensitivity = selection_sensitivity(
            rows, objectives=[{"key": "a", "direction": "maximize"},
                              {"key": "b", "direction": "maximize"}],
            hard_constraints=[], capacity=2)
        self.assertGreaterEqual(len(sensitivity["scenarios"]), 3)
        self.assertIn("minimum_jaccard", sensitivity)


if __name__ == "__main__":
    unittest.main()
