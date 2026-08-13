"""Exclusive single-GPU variant of LocalProcessAdapter."""
from __future__ import annotations

from threading import RLock
from typing import Any

from execution_control.protocol import AdmissionDecision
from executors.local_process import LocalProcessAdapter


class LocalGpuAdapter(LocalProcessAdapter):
    kind = "local_gpu"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._gpu_lock = RLock()
        self._gpu_allocation: str | None = None

    def admit(self, request: dict[str, Any]) -> AdmissionDecision:
        decision = super().admit(request)
        if decision.admitted and self._gpu_allocation is not None:
            return AdmissionDecision(False, "GPU_EXCLUSIVE_BUSY",
                                     "the local GPU already has a resident workload",
                                     decision.available)
        return decision

    def submit(self, request: dict[str, Any]):
        with self._gpu_lock:
            status = super().submit(request)
            if status.state not in {"succeeded", "failed", "cancelled", "unknown"}:
                self._gpu_allocation = status.allocation_id
            return status

    def inspect(self, allocation_id: str):
        status = super().inspect(allocation_id)
        if status.state in {"succeeded", "failed", "cancelled", "unknown"}:
            with self._gpu_lock:
                if self._gpu_allocation == allocation_id:
                    self._gpu_allocation = None
        return status
