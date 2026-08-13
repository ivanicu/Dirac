"""Content-addressed directory finalization and reachability-safe retention."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


def build_directory_manifest(root: Path) -> dict[str, Any]:
    root = root.resolve()
    if not root.is_dir():
        raise ValueError("artifact root must be a directory")
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"artifact directory contains forbidden symlink: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        payload = path.read_bytes()
        files.append({"path": relative, "size_bytes": len(payload),
                      "sha256": hashlib.sha256(payload).hexdigest()})
    canonical = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    return {"schema_version": "1.0", "state": "finalized", "files": files,
            "file_count": len(files), "total_bytes": sum(row["size_bytes"] for row in files),
            "merkle_root": "sha256:" + hashlib.sha256(canonical).hexdigest(),
            "symlink_policy": "forbidden"}


def gc_plan(*, artifacts: Iterable[Mapping[str, Any]],
            active_root_ids: Iterable[str], free_bytes: int,
            low_watermark_bytes: int, high_watermark_bytes: int,
            emergency_watermark_bytes: int) -> dict[str, Any]:
    """Plan tombstones only; deletion is a separate delayed audited operation."""
    if not 0 <= emergency_watermark_bytes <= high_watermark_bytes <= low_watermark_bytes:
        raise ValueError("watermarks must satisfy emergency <= high <= low")
    rows = {str(row["artifact_id"]): dict(row) for row in artifacts}
    reachable = set(map(str, active_root_ids))
    queue = list(reachable)
    while queue:
        current = queue.pop()
        for child in rows.get(current, {}).get("dependency_ids", []):
            child = str(child)
            if child not in reachable:
                reachable.add(child)
                queue.append(child)
    pressure = ("emergency" if free_bytes <= emergency_watermark_bytes else
                "high" if free_bytes <= high_watermark_bytes else
                "low" if free_bytes <= low_watermark_bytes else "normal")
    eligible = []
    if pressure != "normal":
        tier_order = {"temporary": 0, "recomputable_cheap": 1,
                      "checkpoint_only": 2, "recomputable_expensive": 3,
                      "critical_immutable": 99}
        for row in rows.values():
            artifact_id = str(row["artifact_id"])
            if (artifact_id in reachable or row.get("pinned")
                    or row.get("retention_tier") == "critical_immutable"):
                continue
            eligible.append(row)
        eligible.sort(key=lambda row: (
            tier_order.get(row.get("retention_tier"), 50),
            row.get("last_accessed_at", ""), str(row["artifact_id"])))
    return {"schema_version": "1.0", "pressure": pressure,
            "reachable_ids": sorted(reachable),
            "tombstone_candidates": [str(row["artifact_id"]) for row in eligible],
            "deletion_performed": False, "delayed_deletion_required": True}


__all__ = ["build_directory_manifest", "gc_plan"]
