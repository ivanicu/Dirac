"""Immutable dataset manifests and explicit leakage diagnostics."""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from typing import Any, Iterable


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def create_snapshot(rows: Iterable[dict[str, Any]], *, selection_query: str,
                    endpoint_definitions: Iterable[dict[str, Any]],
                    split_key: str = "split") -> tuple[dict[str, Any], bytes]:
    """Freeze normalized rows; every row must retain Measurement and protocol lineage."""
    endpoints = {item["endpoint_key"]: item for item in endpoint_definitions}
    normalized = []
    for source in rows:
        row = dict(source)
        for required in ("measurement_id", "compound_id", "endpoint_key", "protocol_id"):
            if not row.get(required):
                raise ValueError(f"dataset row misses {required}")
        endpoint = endpoints.get(row["endpoint_key"])
        if endpoint is None:
            raise ValueError(f"unknown endpoint {row['endpoint_key']!r}")
        if row.get("unit") != endpoint["canonical_unit"]:
            raise ValueError(f"unit {row.get('unit')!r} is incompatible with endpoint")
        if row.get("measurement_type") != endpoint["measurement_type"]:
            raise ValueError("measurement_type is incompatible with endpoint")
        row.setdefault(split_key, "unassigned")
        normalized.append(row)
    normalized.sort(key=lambda row: (row["measurement_id"], row["compound_id"]))
    data = canonical_bytes(normalized)
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    query_digest = "sha256:" + hashlib.sha256(selection_query.encode()).hexdigest()
    report = leakage_report(normalized, split_key=split_key)
    manifest = {
        "schema_version": "2.0", "selection_query": selection_query,
        "selection_query_digest": query_digest, "row_count": len(normalized),
        "endpoint_keys": sorted({row["endpoint_key"] for row in normalized}),
        "endpoint_counts": dict(sorted(Counter(
            row["endpoint_key"] for row in normalized).items())),
        "split_counts": dict(sorted(Counter(row[split_key] for row in normalized).items())),
        "data_digest": digest, "leakage": report,
    }
    manifest["manifest_digest"] = "sha256:" + hashlib.sha256(canonical_bytes(manifest)).hexdigest()
    return manifest, data


def leakage_report(rows: Iterable[dict[str, Any]], *, split_key: str = "split") -> dict[str, Any]:
    """Detect exact identity, series, scaffold, time and protocol overlap between splits."""
    fields = ("compound_id", "series_id", "scaffold_id", "protocol_id")
    leaks: dict[str, list[dict[str, Any]]] = {field: [] for field in fields}
    values: dict[str, dict[str, set[str]]] = {
        field: defaultdict(set) for field in fields
    }
    for row in rows:
        split = str(row.get(split_key, "unassigned"))
        for field in fields:
            if row.get(field):
                values[field][str(row[field])].add(split)
    for field in fields:
        for value, splits in sorted(values[field].items()):
            if len(splits) > 1:
                leaks[field].append({"value": value, "splits": sorted(splits)})
    time_violations = []
    split_dates: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        if row.get("measured_at"):
            split_dates[str(row.get(split_key, "unassigned"))].append(row["measured_at"])
    if split_dates.get("train") and split_dates.get("test"):
        if max(split_dates["train"]) > min(split_dates["test"]):
            time_violations.append({"code": "TEMPORAL_ORDER_OVERLAP",
                                    "train_max": max(split_dates["train"]),
                                    "test_min": min(split_dates["test"])})
    counts = {field: len(items) for field, items in leaks.items()}
    valid = not any(counts.values()) and not time_violations
    return {"valid": valid, "counts": counts, "examples": leaks,
            "time_violations": time_violations}
