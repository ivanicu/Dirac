"""Fail-closed routing from one ExecutionRequest contract to scheduler adapters."""
from __future__ import annotations

from typing import Iterable

from execution_control.protocol import AllocationStatus, SchedulerAdapter, validate_execution_request


class SchedulerRouter:
    def __init__(self, adapters: Iterable[SchedulerAdapter]) -> None:
        self._adapters: dict[str, SchedulerAdapter] = {}
        for adapter in adapters:
            if adapter.kind in self._adapters:
                raise ValueError(f"duplicate scheduler backend {adapter.kind!r}")
            self._adapters[adapter.kind] = adapter
        if not self._adapters:
            raise ValueError("at least one scheduler adapter is required")

    @property
    def backends(self) -> tuple[str, ...]:
        return tuple(sorted(self._adapters))

    def submit(self, request: dict) -> AllocationStatus:
        validate_execution_request(request)
        adapter = self._require(request["placement"]["backend"])
        return adapter.submit(request)

    def inspect(self, backend: str, allocation_id: str) -> AllocationStatus:
        return self._require(backend).inspect(allocation_id)

    def cancel(self, backend: str, allocation_id: str, *, grace_seconds: int) -> None:
        self._require(backend).request_cancel(allocation_id, grace_seconds=grace_seconds)

    def suspend(self, backend: str, allocation_id: str) -> None:
        self._require(backend).suspend(allocation_id)

    def resume(self, backend: str, allocation_id: str) -> None:
        self._require(backend).resume(allocation_id)

    def reconcile_active(self) -> tuple[AllocationStatus, ...]:
        statuses: list[AllocationStatus] = []
        for adapter in self._adapters.values():
            reconcile = getattr(adapter, "reconcile_active", None)
            if reconcile is not None:
                statuses.extend(reconcile())
        return tuple(statuses)

    def _require(self, backend: str) -> SchedulerAdapter:
        try:
            return self._adapters[backend]
        except KeyError as error:
            raise ValueError(
                f"scheduler backend {backend!r} is not configured; available={self.backends}"
            ) from error
