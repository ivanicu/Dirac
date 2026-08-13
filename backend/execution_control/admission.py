"""Fail-closed resource admission shared by local scheduler adapters."""
from __future__ import annotations

from dataclasses import dataclass
import shutil
from typing import Any

from execution_control.protocol import AdmissionDecision


@dataclass(frozen=True)
class ResourceInventory:
    cpu_cores: float
    memory_bytes_available: int
    scratch_bytes_available: int
    gpus: int = 0
    gpu_arch: str | None = None
    gpu_memory_bytes_available: int = 0
    swap_bytes_used: int = 0
    gpu_healthy: bool = False

    @classmethod
    def local_cpu(cls, *, scratch_path: str) -> "ResourceInventory":
        try:
            import os
            pages = os.sysconf("SC_AVPHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            memory = int(pages * page_size)
            cpus = float(os.cpu_count() or 1)
        except (ValueError, OSError):
            memory, cpus = 0, 1.0
        return cls(cpus, memory, shutil.disk_usage(scratch_path).free)


def admit(request: dict[str, Any], inventory: ResourceInventory,
          *, memory_headroom_fraction: float = 0.15,
          scratch_headroom_bytes: int = 1 << 30) -> AdmissionDecision:
    resources = request["resource_request"]
    available = {
        "cpu_cores": inventory.cpu_cores,
        "memory_bytes": inventory.memory_bytes_available,
        "scratch_bytes": inventory.scratch_bytes_available,
        "gpus": inventory.gpus,
        "gpu_arch": inventory.gpu_arch,
        "gpu_memory_bytes": inventory.gpu_memory_bytes_available,
        "gpu_healthy": inventory.gpu_healthy,
        "swap_bytes_used": inventory.swap_bytes_used,
    }
    memory_limit = int(inventory.memory_bytes_available * (1 - memory_headroom_fraction))
    checks = (
        (resources["cpu_cores"] <= inventory.cpu_cores, "CPU_CAPACITY", "CPU request exceeds capacity"),
        (resources["memory_bytes"] <= memory_limit, "MEMORY_CAPACITY", "request would consume memory safety headroom"),
        (resources["scratch_bytes"] + scratch_headroom_bytes <= inventory.scratch_bytes_available,
         "SCRATCH_CAPACITY", "scratch request exceeds safe free space"),
    )
    for ok, code, reason in checks:
        if not ok:
            return AdmissionDecision(False, code, reason, available)
    if resources["gpus"]:
        if not inventory.gpu_healthy:
            return AdmissionDecision(False, "GPU_UNHEALTHY", "GPU runtime health is not proven", available)
        if resources["gpus"] > inventory.gpus:
            return AdmissionDecision(False, "GPU_CAPACITY", "GPU request exceeds capacity", available)
        allowed = set(resources.get("gpu_arch") or ())
        if allowed and "any" not in allowed and inventory.gpu_arch not in allowed:
            return AdmissionDecision(False, "GPU_ARCH", "GPU architecture is not allowed", available)
        if resources.get("gpu_memory_bytes_min", 0) > inventory.gpu_memory_bytes_available:
            return AdmissionDecision(False, "GPU_MEMORY", "minimum VRAM is unavailable", available)
    return AdmissionDecision(True, "ADMITTED", "resource request fits declared capacity", available)
