"""Local CPU adapter that runs a configured worker in an isolated process.

The ExecutionRequest is data. It cannot choose a Python callable or override the
configured worker command; this is the security boundary shared with remote adapters.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
from threading import Lock
from typing import Any, Sequence
from uuid import uuid4

from execution_control.admission import ResourceInventory, admit
from execution_control.protocol import AllocationStatus, EventPage, validate_execution_request


class LocalProcessAdapter:
    kind = "local_cpu"

    def __init__(self, *, worker_command: Sequence[str], scratch_root: Path,
                 inventory: ResourceInventory | None = None) -> None:
        if not worker_command:
            raise ValueError("worker_command must be fixed at adapter construction")
        self.worker_command = tuple(worker_command)
        self.scratch_root = scratch_root.resolve()
        self.scratch_root.mkdir(parents=True, exist_ok=True)
        self.inventory = inventory or ResourceInventory.local_cpu(
            scratch_path=str(self.scratch_root))
        self._allocations: dict[str, dict[str, Any]] = {}
        self._events: list[dict[str, Any]] = []
        self._lock = Lock()

    def admit(self, request: dict[str, Any]):
        validate_execution_request(request)
        decision = admit(request, self.inventory)
        if request["placement"]["backend"] != self.kind:
            return type(decision)(False, "PLACEMENT_MISMATCH",
                                  f"request targets {request['placement']['backend']}",
                                  decision.available)
        return decision

    def submit(self, request: dict[str, Any]) -> AllocationStatus:
        decision = self.admit(request)
        if not decision.admitted:
            raise RuntimeError(f"{decision.code}: {decision.reason}")
        allocation_id = str(uuid4())
        directory = self.scratch_root / allocation_id
        directory.mkdir(mode=0o700)
        request_path = directory / "execution-request.json"
        request_path.write_text(json.dumps(request, sort_keys=True), encoding="utf-8")
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "DIRAC_EXECUTION_REQUEST": str(request_path),
            "DIRAC_ALLOCATION_ID": allocation_id,
            **request.get("environment", {}),
        }
        stdout = (directory / "stdout.log").open("wb")
        stderr = (directory / "stderr.log").open("wb")
        try:
            process = subprocess.Popen(
                self.worker_command, cwd=directory, env=environment,
                stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                start_new_session=True,
            )
        finally:
            # Popen duplicates these descriptors for the child. The control plane
            # must not retain one descriptor per historical allocation.
            stdout.close()
            stderr.close()
        with self._lock:
            self._allocations[allocation_id] = {
                "process": process, "directory": directory, "suspended": False,
                "cancel_requested": False,
            }
            self._emit(allocation_id, "submitted", {"pid": process.pid})
        return self.inspect(allocation_id)

    def inspect(self, allocation_id: str) -> AllocationStatus:
        with self._lock:
            row = self._allocations.get(allocation_id)
            if row is None:
                return AllocationStatus(allocation_id, "unknown", {})
            code = row["process"].poll()
            if row["suspended"] and code is None:
                state = "suspended"
            elif code is None:
                state = "running"
            elif row["cancel_requested"]:
                state = "cancelled"
            else:
                state = "succeeded" if code == 0 else "failed"
            return AllocationStatus(allocation_id, state,
                                    {"pid": row["process"].pid, "exit_code": code})

    def request_cancel(self, allocation_id: str, *, grace_seconds: int) -> None:
        row = self._require(allocation_id)
        process = row["process"]
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
        with self._lock:
            row["cancel_requested"] = True
            self._emit(allocation_id, "cancel_requested", {"grace_seconds": grace_seconds})

    def suspend(self, allocation_id: str) -> None:
        row = self._require(allocation_id)
        if row["process"].poll() is None:
            os.killpg(row["process"].pid, signal.SIGSTOP)
            row["suspended"] = True

    def resume(self, allocation_id: str) -> None:
        row = self._require(allocation_id)
        if row["process"].poll() is None:
            os.killpg(row["process"].pid, signal.SIGCONT)
            row["suspended"] = False

    def collect_events(self, cursor: str | None) -> EventPage:
        offset = int(cursor or 0)
        events = tuple(self._events[offset:])
        return EventPage(events, str(len(self._events)))

    def reconcile(self, allocation_id: str) -> AllocationStatus:
        return self.inspect(allocation_id)

    def _require(self, allocation_id: str) -> dict[str, Any]:
        with self._lock:
            if allocation_id not in self._allocations:
                raise KeyError(allocation_id)
            return self._allocations[allocation_id]

    def _emit(self, allocation_id: str, state: str, details: dict[str, Any]) -> None:
        self._events.append({"allocation_id": allocation_id, "state": state,
                             "details": details})
