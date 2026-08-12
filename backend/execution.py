"""Execution strategies for the invocation kernel.

An executor runs a callable; it never decides method semantics, cache identity, response
shape, or error vocabulary. Keeping that boundary explicit is what lets an inline call
become a worker/cluster submission without changing SDK, CLI, HTTP, or MCP adapters.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor as _ThreadPool
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
