"""Pinned-runtime probes for GUFE inputs and OpenFE result diagnostics.

This file is executed by ``openfe-runtime-v2/bin/python``.  Keeping GUFE and
OpenFE deserialization inside the pinned environment avoids teaching the API
process a second, incomplete interpretation of their JSON formats.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import sys
from typing import Any

import numpy as np
import openfe
from gufe.tokenization import JSON_HANDLER


def _quantity(value: Any) -> tuple[float | None, str | None]:
    if value is None:
        return None, None
    magnitude = getattr(value, "m", getattr(value, "magnitude", None))
    units = getattr(value, "u", getattr(value, "units", None))
    if magnitude is not None:
        return float(magnitude), str(units) if units is not None else None
    if isinstance(value, dict):
        magnitude = value.get("magnitude", value.get("m"))
        unit = value.get("unit", value.get("units"))
        if magnitude is not None:
            return float(magnitude), str(unit) if unit is not None else None
    return None, None


def _component_types(transformation: Any) -> list[str]:
    names: set[str] = set()
    for state in (transformation.stateA, transformation.stateB):
        for component in state.components.values():
            names.add(type(component).__name__)
    return sorted(names)


def _numeric_array(value: Any) -> np.ndarray | None:
    try:
        array = np.asarray(value, dtype=float)
    except (TypeError, ValueError):
        return None
    return array if array.size and np.all(np.isfinite(array)) else None


def _numeric_arrays(value: Any):
    """Yield numeric arrays even when OpenFE wraps per-generation matrices."""
    array = _numeric_array(value)
    if array is not None:
        yield np.squeeze(array)
        return
    if isinstance(value, dict):
        for child in value.values():
            yield from _numeric_arrays(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _numeric_arrays(child)


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _walk(child)


def validate(input_path: Path) -> dict[str, Any]:
    request = json.loads(input_path.read_text())
    report: dict[str, Any] = {"engine": "OpenFE", "engine_version": openfe.__version__}
    for leg in ("complex", "solvent"):
        path = input_path.with_name(f"{leg}.json")
        path.write_text(json.dumps(request[f"{leg}_transformation"], sort_keys=True,
                                   separators=(",", ":")))
        transformation = openfe.Transformation.from_json(file=path)
        report[leg] = {
            "transformation_key": str(transformation.key),
            "component_types": _component_types(transformation),
        }
    return report


def diagnose(input_path: Path) -> dict[str, Any]:
    with input_path.open() as handle:
        result = json.load(handle, cls=JSON_HANDLER.decoder)
    estimate, estimate_unit = _quantity(result.get("estimate"))
    uncertainty, uncertainty_unit = _quantity(result.get("uncertainty"))
    overlaps: list[float] = []
    production_iterations: list[int] = []
    equilibration_iterations: list[int] = []
    for node in _walk(result):
        if "unit_mbar_overlap" in node:
            for array in _numeric_arrays(node["unit_mbar_overlap"]):
                matrices = ([array] if array.ndim == 2 else
                            list(array.reshape((-1, array.shape[-2], array.shape[-1])))
                            if array.ndim > 2 else [])
                for matrix in matrices:
                    if min(matrix.shape) <= 1:
                        continue
                    count = min(matrix.shape)
                    overlaps.extend(float(min(matrix[i, i + 1], matrix[i + 1, i]))
                                    for i in range(count - 1))
        for key, target in (("production_iterations", production_iterations),
                            ("equilibration_iterations", equilibration_iterations)):
            if key not in node:
                continue
            array = _numeric_array(node[key])
            if array is not None:
                target.extend(int(item) for item in array.ravel())
            elif isinstance(node[key], (int, float)) and math.isfinite(node[key]):
                target.append(int(node[key]))
    return {
        "engine": "OpenFE", "engine_version": openfe.__version__,
        "estimate": estimate, "uncertainty": uncertainty,
        "unit": estimate_unit or uncertainty_unit,
        "minimum_neighbor_overlap": min(overlaps) if overlaps else None,
        "overlap_window_count": len(overlaps),
        "production_iterations": max(production_iterations) if production_iterations else None,
        "equilibration_iterations": max(equilibration_iterations) if equilibration_iterations else None,
    }


def main() -> None:
    mode, source, target = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    output = validate(source) if mode == "validate" else diagnose(source)
    target.write_text(json.dumps(output, sort_keys=True, separators=(",", ":"),
                                 allow_nan=False))


if __name__ == "__main__":
    main()
