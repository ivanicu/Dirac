"""Exclusive host-GPU handoff between the resident local reasoner and Motif jobs."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import subprocess
import threading
import time
from typing import Callable, Iterator, Sequence

import failures


class SharedGpuCoordinator:
    """Stop the resident Qwen service while one admitted scientific GPU job runs.

    The process lock serializes controller threads; the file lock extends the same
    exclusion boundary across API processes.  The service is restarted only when
    this coordinator observed it active before the handoff.
    """

    def __init__(self, *, lock_path: Path, service: str = "dirac-qwen.service",
                 runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
                 sleep: Callable[[float], None] = time.sleep,
                 timeout_seconds: float = 120.0,
                 poll_seconds: float = 1.0) -> None:
        self.lock_path = lock_path.resolve()
        self.service = service
        self._runner = runner
        self._sleep = sleep
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.poll_seconds = max(.05, float(poll_seconds))
        self._thread_lock = threading.Lock()

    def _run(self, command: Sequence[str], *, check: bool = True
             ) -> subprocess.CompletedProcess:
        try:
            return self._runner(
                list(command), check=check, capture_output=True, text=True,
                timeout=min(30.0, self.timeout_seconds))
        except (OSError, subprocess.SubprocessError) as error:
            raise failures.DiracUnsupported(
                "shared GPU handoff command failed",
                details={"command": list(command),
                         "error": f"{type(error).__name__}: {error}"}) from error

    def _is_reasoner_active(self) -> bool:
        result = self._run(
            ["systemctl", "--user", "is-active", self.service], check=False)
        if result.returncode == 0 and result.stdout.strip() == "active":
            return True
        if result.stdout.strip() in {"inactive", "failed", "unknown"}:
            return False
        raise failures.DiracUnsupported(
            "cannot establish local reasoner service state before GPU handoff",
            details={"service": self.service, "returncode": result.returncode,
                     "stdout": result.stdout.strip(),
                     "stderr": result.stderr.strip()})

    def _free_memory_bytes(self) -> int:
        result = self._run([
            "nvidia-smi", "--query-gpu=memory.free",
            "--format=csv,noheader,nounits",
        ])
        try:
            return int(result.stdout.splitlines()[0].strip()) * (1 << 20)
        except (ValueError, IndexError) as error:
            raise failures.DiracUnsupported(
                "cannot parse free GPU memory during shared GPU handoff",
                details={"stdout": result.stdout[:256]}) from error

    def _wait_for_memory(self, required_bytes: int) -> None:
        deadline = time.monotonic() + self.timeout_seconds
        observed = 0
        while time.monotonic() <= deadline:
            observed = self._free_memory_bytes()
            if observed >= required_bytes:
                return
            self._sleep(self.poll_seconds)
        raise failures.DiracUnsupported(
            "local reasoner stopped but the scientific GPU memory requirement "
            "was not released before timeout",
            details={"required_bytes": required_bytes,
                     "observed_free_bytes": observed,
                     "timeout_seconds": self.timeout_seconds})

    @contextmanager
    def exclusive(self, *, required_bytes: int) -> Iterator[None]:
        if type(required_bytes) is not int or required_bytes < 1:
            raise failures.DiracInternal(
                "shared GPU handoff requires a positive byte capacity")
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self._thread_lock, self.lock_path.open("a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            reasoner_was_active = False
            body_error: BaseException | None = None
            try:
                reasoner_was_active = self._is_reasoner_active()
                if reasoner_was_active:
                    self._run(["systemctl", "--user", "stop", self.service])
                self._wait_for_memory(required_bytes)
                yield
            except BaseException as error:
                body_error = error
                raise
            finally:
                if reasoner_was_active:
                    try:
                        self._run(["systemctl", "--user", "start", self.service])
                    except BaseException:
                        if body_error is None:
                            raise
                        # Preserve the scientific execution failure as the primary
                        # exception; systemd records the independent restart failure.
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def coordinator_from_environment(repository: Path) -> SharedGpuCoordinator | None:
    """Build only under the operator's explicit same-GPU deployment grant."""
    if os.environ.get("DIRAC_ALLOW_SHARED_GPU_AI") != "1":
        return None
    return SharedGpuCoordinator(
        lock_path=Path(os.environ.get(
            "DIRAC_SHARED_GPU_LOCK",
            repository / ".runtime/shared-gpu/scientific-execution.lock")),
        service=os.environ.get("DIRAC_QWEN_SYSTEMD_SERVICE", "dirac-qwen.service"),
        timeout_seconds=float(os.environ.get(
            "DIRAC_SHARED_GPU_RELEASE_TIMEOUT_SECONDS", "120")),
    )
