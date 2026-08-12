"""Execution strategies for the invocation kernel.

An executor runs a callable; it never decides method semantics, cache identity, response
shape, or error vocabulary. Keeping that boundary explicit is what lets an inline call
become a worker/cluster submission without changing SDK, CLI, HTTP, or MCP adapters.
"""
from __future__ import annotations

from concurrent.futures import (Future, ProcessPoolExecutor as _ProcessPool,
                                ThreadPoolExecutor as _ThreadPool)
from typing import Any, Callable


class InlineExecutor:
    kind = 'inline'
    supports_submission = False

    def execute(self, fn: Callable[..., Any], *args, **kwargs) -> Any:
        return fn(*args, **kwargs)


class ThreadExecutor:
    """A bounded in-process executor for reconnectable job-mode work.

    Cancellation is honest: a queued future may be cancelled; Python cannot interrupt a
    callable that is already running, so ``cancel`` returns false in that state.
    """

    kind = 'thread'
    supports_submission = True

    def __init__(self, max_workers: int = 1) -> None:
        self._pool = _ThreadPool(max_workers=max_workers,
                                 thread_name_prefix='dirac-job')

    def execute(self, fn: Callable[..., Any], *args, **kwargs) -> Any:
        return fn(*args, **kwargs)

    def submit(self, fn: Callable[..., Any], *args, **kwargs) -> Future:
        return self._pool.submit(fn, *args, **kwargs)

    @staticmethod
    def cancel(future: Future) -> bool:
        return future.cancel()

    def shutdown(self, *, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait)


class ProcessExecutor:
    """A bounded local process pool for picklable, isolation-worthy workloads.

    It deliberately does not claim that an arbitrary bound handler is picklable. The
    application selects this executor only for worker entrypoints designed to cross a
    process boundary; failure to pickle is surfaced by the returned Future.
    """

    kind = 'process'
    supports_submission = True

    def __init__(self, max_workers: int = 1) -> None:
        self._pool = _ProcessPool(max_workers=max_workers)

    def execute(self, fn: Callable[..., Any], *args, **kwargs) -> Any:
        return self._pool.submit(fn, *args, **kwargs).result()

    def submit(self, fn: Callable[..., Any], *args, **kwargs) -> Future:
        return self._pool.submit(fn, *args, **kwargs)

    @staticmethod
    def cancel(future: Future) -> bool:
        return future.cancel()

    def shutdown(self, *, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait)


class RemoteExecutor:
    """Adapter boundary for a queue or cluster scheduler.

    The injected callbacks own wire protocol and credentials. This class owns only the
    Executor contract, so replacing a thread pool with Slurm, Kubernetes, or a managed
    queue cannot leak transport logic into InvocationService.
    """

    kind = 'remote'
    supports_submission = True

    def __init__(self, submit: Callable[..., Future], *,
                 execute: Callable[..., Any] | None = None,
                 cancel: Callable[[Future], bool] | None = None) -> None:
        self._submit = submit
        self._execute = execute
        self._cancel = cancel

    def execute(self, fn: Callable[..., Any], *args, **kwargs) -> Any:
        if self._execute is not None:
            return self._execute(fn, *args, **kwargs)
        return self.submit(fn, *args, **kwargs).result()

    def submit(self, fn: Callable[..., Any], *args, **kwargs) -> Future:
        return self._submit(fn, *args, **kwargs)

    def cancel(self, future: Future) -> bool:
        return self._cancel(future) if self._cancel is not None else future.cancel()
