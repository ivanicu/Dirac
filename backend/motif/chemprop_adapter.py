"""A real Chemprop 2.x D-MPNN ensemble behind a data-only Motif checkpoint."""
from __future__ import annotations

import base64
import hashlib
import io
import json
from collections import defaultdict
from typing import Any, Iterable


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _imports():
    try:
        import lightning.pytorch as pl
        import numpy as np
        import torch
        from chemprop import data, featurizers, models, nn
    except ImportError as exc:  # pragma: no cover - dependency boundary
        raise RuntimeError(
            "Chemprop, Lightning and PyTorch are required for D-MPNN "
            f"({type(exc).__name__}: {exc})") from exc
    return pl, np, torch, data, featurizers, models, nn


def _group_rows(rows: Iterable[dict[str, Any]], endpoint_keys: list[str], split: str):
    _, np, _, data, *_ = _imports()
    grouped: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        if row.get("split", "train") != split or row.get("endpoint_key") not in endpoint_keys:
            continue
        qualifier = row.get("qualifier", "equal")
        if qualifier in {"not_tested", "invalid", "interval"} or row.get("value") is None:
            continue
        grouped[row["smiles"]][row["endpoint_key"]] = row
    points = []
    for smiles, endpoints in sorted(grouped.items()):
        y = np.full(len(endpoint_keys), np.nan, dtype=float)
        lt = np.zeros(len(endpoint_keys), dtype=bool)
        gt = np.zeros(len(endpoint_keys), dtype=bool)
        for index, endpoint in enumerate(endpoint_keys):
            row = endpoints.get(endpoint)
            if row is None:
                continue
            y[index] = float(row["value"])
            lt[index] = row["qualifier"] in {"less_than", "less_or_equal"}
            gt[index] = row["qualifier"] in {"greater_than", "greater_or_equal"}
        points.append(data.MoleculeDatapoint.from_smi(
            smiles, y=y, lt_mask=lt, gt_mask=gt, name=smiles))
    return points


def _build_model(config: dict[str, Any], scaler=None):
    _, _, _, _, _, models, nn = _imports()
    message_passing = nn.BondMessagePassing(
        d_h=config["hidden_dim"], depth=config["depth"],
        dropout=config["dropout"], undirected=config["undirected"])
    aggregation = nn.MeanAggregation()
    output_transform = nn.UnscaleTransform.from_standard_scaler(scaler) if scaler else None
    predictor = nn.RegressionFFN(
        n_tasks=len(config["endpoint_keys"]), input_dim=config["hidden_dim"],
        hidden_dim=config["ffn_hidden_dim"], n_layers=config["ffn_layers"],
        dropout=config["dropout"], criterion=nn.BoundedMSE(),
        output_transform=output_transform)
    return models.MPNN(
        message_passing, aggregation, predictor, batch_norm=config["batch_norm"],
        warmup_epochs=config["warmup_epochs"], init_lr=config["init_lr"],
        max_lr=config["max_lr"], final_lr=config["final_lr"])


def _loader(points, *, batch_size: int, shuffle: bool, seed: int):
    _, _, _, data, featurizers, *_ = _imports()
    dataset = data.MoleculeDataset(
        points, featurizers.SimpleMoleculeMolGraphFeaturizer(), n_workers=0)
    effective_batch = min(batch_size, max(1, len(points)))
    # Chemprop's molecular BatchNorm cannot train on a singleton final batch.
    # Prefer a slightly smaller batch that partitions into chunks >= 2 instead
    # of silently dropping a molecule from the governed Dataset Snapshot.
    while shuffle and len(points) % effective_batch == 1 and effective_batch > 2:
        effective_batch -= 1
    return dataset, data.build_dataloader(
        dataset, batch_size=effective_batch, num_workers=0,
        shuffle=shuffle, seed=seed, drop_last=False)


def train_chemprop_ensemble(rows: Iterable[dict[str, Any]], *,
                            endpoint_keys: Iterable[str], ensemble_size: int = 5,
                            epochs: int = 30, batch_size: int = 64,
                            hidden_dim: int = 300, depth: int = 3,
                            ffn_hidden_dim: int = 300, ffn_layers: int = 1,
                            dropout: float = 0.1, seed: int = 0,
                            accelerator: str = "auto") -> tuple[dict[str, Any], dict[str, Any]]:
    """Train bounded-label multitask D-MPNNs and return an immutable JSON checkpoint."""
    pl, np, torch, *_ = _imports()
    source = [dict(row) for row in rows]
    endpoints = sorted(set(endpoint_keys))
    if not endpoints:
        raise ValueError("Chemprop training requires at least one endpoint")
    train_points = _group_rows(source, endpoints, "train")
    validation_points = _group_rows(source, endpoints, "validation")
    if len(train_points) < 2:
        raise ValueError("Chemprop training requires at least two train molecules")
    config = {
        "endpoint_keys": endpoints, "hidden_dim": hidden_dim, "depth": depth,
        "ffn_hidden_dim": ffn_hidden_dim, "ffn_layers": ffn_layers,
        "dropout": dropout, "undirected": False, "batch_norm": True,
        "warmup_epochs": min(2, max(1, epochs // 10)),
        "init_lr": 1e-4, "max_lr": 1e-3, "final_lr": 1e-4,
        "batch_size": batch_size, "epochs": epochs,
    }
    members, validation_predictions = [], []
    for member in range(ensemble_size):
        member_seed = seed + member * 1009
        pl.seed_everything(member_seed, workers=True, verbose=False)
        train_dataset, train_loader = _loader(
            train_points, batch_size=batch_size, shuffle=True, seed=member_seed)
        scaler = train_dataset.normalize_targets()
        validation_loader = None
        if validation_points:
            validation_dataset, validation_loader = _loader(
                validation_points, batch_size=batch_size, shuffle=False, seed=member_seed)
            validation_dataset.normalize_targets(scaler)
        model = _build_model(config, scaler)
        trainer = pl.Trainer(
            max_epochs=epochs, accelerator=accelerator, devices=1,
            logger=False, enable_checkpointing=False, enable_progress_bar=False,
            deterministic=True, num_sanity_val_steps=0,
        )
        trainer.fit(model, train_loader, validation_loader)
        buffer = io.BytesIO()
        torch.save({key: value.detach().cpu() for key, value in model.state_dict().items()}, buffer)
        members.append({"seed": member_seed,
                        "state_dict_b64": base64.b64encode(buffer.getvalue()).decode("ascii")})
        if validation_points:
            predicted = trainer.predict(model, validation_loader)
            validation_predictions.append(torch.cat(predicted).detach().cpu().numpy())
    checkpoint = {
        "schema_version": "1.0", "algorithm": "chemprop_dpmpnn_ensemble",
        "chemprop_version": __import__("chemprop").__version__,
        "torch_version": torch.__version__, "config": config,
        "target_scaler": {"mean": scaler.mean_.astype(float).tolist(),
                          "scale": scaler.scale_.astype(float).tolist()},
        "ensemble_size": ensemble_size, "members": members,
        "training": {"molecule_count": len(train_points),
                     "validation_molecule_count": len(validation_points),
                     "bounded_loss": "chemprop.nn.BoundedMSE",
                     "interval_labels": "handled_by_tobit_head_not_dpmpnn"},
    }
    checkpoint["digest"] = _digest(checkpoint)
    validation = {"available": bool(validation_predictions),
                  "validation_molecule_count": len(validation_points)}
    if validation_predictions:
        stack = np.stack(validation_predictions)
        validation.update({"ensemble_prediction_mean": np.nanmean(stack, axis=0).tolist(),
                           "ensemble_prediction_std": np.nanstd(stack, axis=0).tolist()})
    return checkpoint, validation


def predict_chemprop_ensemble(checkpoint: dict[str, Any], smiles: Iterable[str], *,
                              accelerator: str = "auto") -> list[dict[str, Any]]:
    pl, np, torch, data, featurizers, *_ = _imports()
    material = dict(checkpoint)
    expected = material.pop("digest", None)
    if expected != _digest(material):
        raise ValueError("Chemprop checkpoint digest mismatch")
    values = list(smiles)
    if not values:
        return []
    points = [data.MoleculeDatapoint.from_smi(value, name=value) for value in values]
    dataset = data.MoleculeDataset(points, featurizers.SimpleMoleculeMolGraphFeaturizer())
    loader = data.build_dataloader(dataset, batch_size=min(
        checkpoint["config"]["batch_size"], len(points)), shuffle=False, drop_last=False)
    from sklearn.preprocessing import StandardScaler
    scaler = StandardScaler()
    scaler.mean_ = np.asarray(checkpoint["target_scaler"]["mean"])
    scaler.scale_ = np.asarray(checkpoint["target_scaler"]["scale"])
    scaler.var_ = scaler.scale_ ** 2
    scaler.n_features_in_ = len(scaler.mean_)
    scaler.n_samples_seen_ = checkpoint["training"]["molecule_count"]
    member_predictions = []
    for member in checkpoint["members"]:
        model = _build_model(checkpoint["config"], scaler)
        state = torch.load(io.BytesIO(base64.b64decode(member["state_dict_b64"])),
                           map_location="cpu", weights_only=True)
        model.load_state_dict(state, strict=True)
        trainer = pl.Trainer(
            accelerator=accelerator, devices=1, logger=False, enable_checkpointing=False,
            enable_progress_bar=False)
        member_predictions.append(torch.cat(trainer.predict(model, loader)).cpu().numpy())
    stack = np.stack(member_predictions)
    means, deviations = np.mean(stack, axis=0), np.std(stack, axis=0, ddof=1)
    endpoints = checkpoint["config"]["endpoint_keys"]
    return [{"smiles": value, "endpoints": {
        endpoint: {"mean": float(means[row, column]),
                   "member_dispersion_std": float(deviations[row, column]),
                   "dispersion_claim":
                   "ensemble_member_dispersion_not_calibrated_epistemic_uncertainty"}
        for column, endpoint in enumerate(endpoints)}}
        for row, value in enumerate(values)]


__all__ = ["predict_chemprop_ensemble", "train_chemprop_ensemble"]
