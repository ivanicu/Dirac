from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

import failures
from execution_control.shared_gpu import SharedGpuCoordinator


class CommandRunner:
    def __init__(self, *, active: bool = True, free_mib: list[int] | None = None,
                 ready: list[bool] | None = None):
        self.active = active
        self.free_mib = list(free_mib or [16_000])
        self.ready = list(ready or [True])
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, command, **_kwargs):
        command = tuple(command)
        self.commands.append(command)
        if command[:3] == ("systemctl", "--user", "is-active"):
            state = "active" if self.active else "inactive"
            return subprocess.CompletedProcess(command, 0 if self.active else 3,
                                               state + "\n", "")
        if command[:3] == ("systemctl", "--user", "stop"):
            self.active = False
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:3] == ("systemctl", "--user", "start"):
            self.active = True
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[0] == "nvidia-smi":
            value = self.free_mib.pop(0) if len(self.free_mib) > 1 else self.free_mib[0]
            return subprocess.CompletedProcess(command, 0, f"{value}\n", "")
        if command[0] == "curl":
            value = self.ready.pop(0) if len(self.ready) > 1 else self.ready[0]
            return subprocess.CompletedProcess(
                command, 0 if value else 7, "" if value else "", "")
        raise AssertionError(command)


def test_active_reasoner_is_stopped_before_gpu_work_and_restarted(tmp_path: Path):
    runner = CommandRunner(active=True, free_mib=[2_000, 15_000])
    coordinator = SharedGpuCoordinator(
        lock_path=tmp_path / "gpu.lock", runner=runner, sleep=lambda _value: None,
        timeout_seconds=5, poll_seconds=.01)

    with coordinator.exclusive(required_bytes=12_000 << 20):
        assert runner.active is False
        runner.commands.append(("scientific-gpu-work",))

    assert runner.active is True
    assert runner.commands.index(("systemctl", "--user", "stop",
                                  "dirac-qwen.service")) < runner.commands.index(
                                      ("scientific-gpu-work",))
    start = ("systemctl", "--user", "start", "dirac-qwen.service")
    assert start in runner.commands
    assert runner.commands.index(start) < next(
        index for index, command in enumerate(runner.commands)
        if command[0] == "curl")


def test_restart_waits_until_reasoner_http_is_ready(tmp_path: Path):
    runner = CommandRunner(active=True, ready=[False, False, True])
    sleeps: list[float] = []
    coordinator = SharedGpuCoordinator(
        lock_path=tmp_path / "gpu.lock", runner=runner,
        sleep=sleeps.append, timeout_seconds=5, poll_seconds=.01)

    with coordinator.exclusive(required_bytes=1 << 20):
        pass

    health_checks = [command for command in runner.commands
                     if command[0] == "curl"]
    assert len(health_checks) == 3
    assert sleeps == [.05, .05]


def test_reasoner_is_restarted_when_scientific_work_fails(tmp_path: Path):
    runner = CommandRunner(active=True)
    coordinator = SharedGpuCoordinator(
        lock_path=tmp_path / "gpu.lock", runner=runner)

    with pytest.raises(RuntimeError, match="scientific failure"):
        with coordinator.exclusive(required_bytes=1 << 20):
            raise RuntimeError("scientific failure")

    assert runner.active is True


def test_inactive_reasoner_is_not_started_by_the_coordinator(tmp_path: Path):
    runner = CommandRunner(active=False)
    coordinator = SharedGpuCoordinator(
        lock_path=tmp_path / "gpu.lock", runner=runner)

    with coordinator.exclusive(required_bytes=1 << 20):
        pass

    assert not any(command[:3] == ("systemctl", "--user", "start")
                   for command in runner.commands)


def test_insufficient_released_memory_fails_closed(tmp_path: Path, monkeypatch):
    runner = CommandRunner(active=True, free_mib=[100])
    ticks = iter([0.0, 0.0, 2.0, 2.0])
    monkeypatch.setattr("execution_control.shared_gpu.time.monotonic",
                        lambda: next(ticks, 2.0))
    coordinator = SharedGpuCoordinator(
        lock_path=tmp_path / "gpu.lock", runner=runner, sleep=lambda _value: None,
        timeout_seconds=1, poll_seconds=.01)

    with pytest.raises(failures.DiracUnsupported, match="was not released"):
        with coordinator.exclusive(required_bytes=10_000 << 20):
            raise AssertionError("must not execute")

    assert runner.active is True
