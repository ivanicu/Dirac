from __future__ import annotations

import unittest

from motif.features import fit_feature_release, transform
from motif.labels import (fit_censored_tobit, fit_pairwise_ranker, interval_from_row,
                          predict_censored_tobit)
from motif.tree_models import predict_tree_models, train_tree_models
from motif.uncertainty import (assess_domain, ensemble_summary,
                               fit_conditional_conformal, fit_domain)
from motif.validation import specification_curve


SMILES = ["CCO", "CCN", "CCC", "CCCl", "CCBr", "CC(=O)O", "c1ccccc1", "c1ccncc1"]


class PredictorMeshTests(unittest.TestCase):
    def test_full_mesh_round_trip_keeps_compact_domain(self):
        from motif.mesh import predict_predictor_mesh, train_predictor_mesh
        smiles = ["CCO", "CCN", "CCC", "CCCl", "CCBr", "CCCO", "CCCC"]
        rows = [{"compound_id": f"c-{index}", "smiles": value,
                 "endpoint_key": "activity", "qualifier": "equal",
                 "value": float(index),
                 "split": "validation" if index == 6 else "train"}
                for index, value in enumerate(smiles)]
        checkpoint, validation = train_predictor_mesh(
            rows, endpoint_key="activity", n_bits=128, include_chemprop=False,
            bootstrap_samples=20, seed=5)
        assert len(checkpoint["domain_release"]["precision"]) == 10
        prediction = predict_predictor_mesh(checkpoint, ["CCO"])[0]
        assert set(prediction["models"]) >= {"ridge", "random_forest", "xgboost",
                                              "censored_tobit"}
        assert validation["cell_count"] > 0

    @classmethod
    def setUpClass(cls):
        cls.release = fit_feature_release(SMILES, n_bits=128)
        cls.features, cls.canonical = transform(SMILES, cls.release)

    def test_feature_release_is_label_free_digest_verified_and_chiral(self):
        self.assertEqual(self.release["label_access"], "forbidden_by_interface")
        self.assertEqual(self.features.shape, (8, 138))
        bad = dict(self.release)
        bad["fit_count"] += 1
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            transform(["CCO"], bad)

    def test_censored_tobit_uses_bounds_without_point_coercion(self):
        rows = [
            {"qualifier": "equal", "value": 1.0},
            {"qualifier": "less_than", "value": 2.0},
            {"qualifier": "greater_than", "value": 2.5},
            {"qualifier": "interval", "lower": 1.5, "upper": 3.0},
            {"qualifier": "not_tested", "value": None},
            {"qualifier": "equal", "value": 3.0},
            {"qualifier": "equal", "value": 3.5},
            {"qualifier": "equal", "value": 4.0},
        ]
        self.assertEqual(interval_from_row(rows[1]), (float("-inf"), 2.0))
        model = fit_censored_tobit(self.features, rows, l2=10.0, max_iter=1000)
        self.assertEqual(model["censored_count"], 3)
        self.assertEqual(model["excluded_count"], 1)
        self.assertEqual(len(predict_censored_tobit(model, self.features)), 8)

    def test_tree_checkpoints_round_trip_without_pickle(self):
        values = [1.0, 1.5, 2.0, 2.3, 2.6, 3.0, 4.0, 4.4]
        model = train_tree_models(self.features, values, seed=7,
                                  rf_estimators=12, xgb_estimators=12, max_depth=3)
        predictions = predict_tree_models(model, self.features)
        self.assertEqual(set(predictions), {"random_forest", "xgboost"})
        self.assertEqual(len(predictions["xgboost"]), 8)
        self.assertNotIn("pickle", str(model).lower())

    def test_rank_domain_ensemble_conformal_and_specification_curve(self):
        values = [1.0, 1.5, 2.0, 2.3, 2.6, 3.0, 4.0, 4.4]
        ranker = fit_pairwise_ranker(self.features, values, l2=10.0)
        self.assertGreater(ranker["pair_count"], 0)
        domain = fit_domain(self.features)
        states = assess_domain(domain, self.features)
        self.assertTrue(all(row["status"] in {"in_domain", "borderline"} for row in states))
        members = {"a": values, "b": [value + .2 for value in values]}
        summary = ensemble_summary(members)
        self.assertGreater(summary[0]["epistemic_std"], 0)
        calibration = fit_conditional_conformal(
            values, [value + .1 for value in values],
            [row["status"] for row in states], min_group=2)
        self.assertGreater(calibration["global_width"], 0)
        report = specification_curve([
            {"model": model, "endpoint": "ic50", "split": split,
             "prediction": value, "observation": value + .1}
            for model in ("ridge", "xgboost")
            for split in ("scaffold", "temporal")
            for value in (1.0, 2.0, 3.0)
        ], bootstrap_samples=20)
        self.assertEqual(report["cell_count"], 4)


if __name__ == "__main__":
    unittest.main()
