"""Strong deterministic tree baselines with safe, data-only checkpoints."""
from __future__ import annotations

import base64
import hashlib
import json
from typing import Any, Iterable


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _serialize_tree(tree) -> dict[str, Any]:
    return {
        "children_left": tree.children_left.astype(int).tolist(),
        "children_right": tree.children_right.astype(int).tolist(),
        "feature": tree.feature.astype(int).tolist(),
        "threshold": tree.threshold.astype(float).tolist(),
        "value": tree.value[:, 0, 0].astype(float).tolist(),
    }


def train_tree_models(features, values: Iterable[float], *, seed: int = 0,
                      rf_estimators: int = 256, xgb_estimators: int = 400,
                      max_depth: int = 8) -> dict[str, Any]:
    """Train RF and XGBoost on the identical feature matrix and labels."""
    import numpy as np
    from sklearn.ensemble import RandomForestRegressor
    from xgboost import XGBRegressor

    matrix = np.asarray(features, dtype=np.float32)
    labels = np.asarray(list(values), dtype=np.float32)
    if matrix.ndim != 2 or len(labels) != len(matrix) or len(labels) < 2:
        raise ValueError("tree baselines require a 2D feature matrix and matching labels")
    forest = RandomForestRegressor(
        n_estimators=rf_estimators, max_depth=max_depth, min_samples_leaf=2,
        max_features="sqrt", random_state=seed, n_jobs=1, bootstrap=True,
    ).fit(matrix, labels)
    xgb = XGBRegressor(
        n_estimators=xgb_estimators, max_depth=max_depth, learning_rate=0.03,
        subsample=0.8, colsample_bytree=0.8, reg_lambda=1.0,
        objective="reg:squarederror", tree_method="hist", random_state=seed,
        n_jobs=1,
    ).fit(matrix, labels, verbose=False)
    raw = bytes(xgb.get_booster().save_raw(raw_format="ubj"))
    model = {
        "schema_version": "1.0", "kind": "rf_xgboost_regression",
        "seed": seed, "feature_count": int(matrix.shape[1]),
        "random_forest": {
            "n_estimators": rf_estimators, "max_depth": max_depth,
            "trees": [_serialize_tree(item.tree_) for item in forest.estimators_],
        },
        "xgboost": {
            "n_estimators": xgb_estimators, "max_depth": max_depth,
            "raw_format": "ubj", "booster_b64": base64.b64encode(raw).decode("ascii"),
        },
    }
    model["digest"] = _digest(model)
    return model


def _predict_tree(tree: dict[str, Any], row) -> float:
    node = 0
    while tree["children_left"][node] != -1:
        node = (tree["children_left"][node]
                if row[tree["feature"][node]] <= tree["threshold"][node]
                else tree["children_right"][node])
    return float(tree["value"][node])


def predict_tree_models(model: dict[str, Any], features) -> dict[str, list[float]]:
    import numpy as np
    import xgboost as xgb

    material = dict(model)
    expected = material.pop("digest", None)
    if expected != _digest(material):
        raise ValueError("tree model digest mismatch")
    matrix = np.asarray(features, dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape[1] != model["feature_count"]:
        raise ValueError("tree feature dimension mismatch")
    trees = model["random_forest"]["trees"]
    forest = [sum(_predict_tree(tree, row) for tree in trees) / len(trees)
              for row in matrix]
    booster = xgb.Booster()
    booster.load_model(bytearray(base64.b64decode(model["xgboost"]["booster_b64"])))
    boosted = booster.predict(xgb.DMatrix(matrix)).astype(float).tolist()
    return {"random_forest": forest, "xgboost": boosted}


__all__ = ["predict_tree_models", "train_tree_models"]
