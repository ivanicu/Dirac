"""Attempt-scoped scratch quotas with explicit terminal cleanup policy."""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AttemptScratch:
    root: Path
    quota_bytes: int

    @classmethod
    def create(cls, base: Path, attempt_id: str, quota_bytes: int) -> "AttemptScratch":
        if not attempt_id or "/" in attempt_id or attempt_id in {".", ".."}:
            raise ValueError("unsafe attempt_id")
        base.mkdir(parents=True, exist_ok=True)
        resolved_base = base.resolve()
        root = (resolved_base / attempt_id).resolve()
        if resolved_base not in root.parents:
            raise ValueError("scratch path escapes base")
        if root.exists():
            raise FileExistsError(root)
        free = shutil.disk_usage(resolved_base).free
        if quota_bytes > free:
            raise OSError(f"scratch quota {quota_bytes} exceeds {free} free bytes")
        root.mkdir(parents=True, mode=0o700)
        return cls(root, quota_bytes)

    def usage_bytes(self) -> int:
        return sum(path.stat().st_size for path in self.root.rglob("*") if path.is_file())

    def check(self, additional_bytes: int = 0) -> None:
        if self.usage_bytes() + additional_bytes > self.quota_bytes:
            raise OSError("attempt scratch quota exceeded")

    def cleanup(self) -> None:
        # Never follows paths outside the already-validated attempt root.
        shutil.rmtree(self.root)
