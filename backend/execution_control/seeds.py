"""Stable hierarchical seeds for retries, shards and distributed workers."""
from __future__ import annotations

import hashlib
import json
from typing import Any


def _scope_bytes(scope: dict[str, Any]) -> bytes:
    return json.dumps(
        scope, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def seed_scope_digest(scope: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_scope_bytes(scope)).hexdigest()


def derive_seed(root_seed: int, scope: dict[str, Any]) -> int:
    if not isinstance(root_seed, int) or isinstance(root_seed, bool) or root_seed < 0:
        raise ValueError("root_seed must be a non-negative integer")
    material = str(root_seed).encode("ascii") + b"\x00" + _scope_bytes(scope)
    # Keep the result inside signed PostgreSQL bigint and common RNG ranges.
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big") & ((1 << 63) - 1)
