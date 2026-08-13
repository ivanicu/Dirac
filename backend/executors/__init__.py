"""Scheduler adapters for fixed, versioned Dirac worker entrypoints."""

from .local_process import LocalProcessAdapter

__all__ = ["LocalProcessAdapter"]
