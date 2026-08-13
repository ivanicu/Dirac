#!/usr/bin/env python3
"""Small real-CUDA Motif smoke. Submit only through gpu-run."""
from __future__ import annotations

import json

import torch

from motif.chemprop_adapter import (predict_chemprop_ensemble,
                                    train_chemprop_ensemble)


def main() -> None:
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable to the queued Motif worker")
    rows = []
    for index, (smiles, value) in enumerate([
            ("CCO", 1.0), ("CCN", 1.3), ("CCC", 1.6), ("CCCl", 1.8),
            ("CCBr", 1.9), ("c1ccccc1", 2.3)]):
        rows.append({"compound_id": f"gpu-{index}", "smiles": smiles,
                     "endpoint_key": "activity", "value": value,
                     "qualifier": "equal",
                     "split": "validation" if index == 5 else "train"})
    checkpoint, validation = train_chemprop_ensemble(
        rows, endpoint_keys=["activity"], ensemble_size=2, epochs=1,
        batch_size=4, hidden_dim=32, depth=2, ffn_hidden_dim=32,
        seed=71, accelerator="gpu")
    predictions = predict_chemprop_ensemble(
        checkpoint, ["CCO", "CCN"], accelerator="gpu")
    print(json.dumps({
        "ok": True, "device": torch.cuda.get_device_name(0),
        "torch": torch.__version__, "cuda_runtime": torch.version.cuda,
        "checkpoint_digest": checkpoint["digest"],
        "ensemble_size": checkpoint["ensemble_size"],
        "validation": validation, "predictions": predictions,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
