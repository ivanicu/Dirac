"""Scheduler adapters for fixed, versioned Dirac worker entrypoints."""

from .local_process import LocalProcessAdapter
from .kubernetes_invocation import KubernetesInvocationExecutor
from .kubernetes_kueue import (
    KubernetesKueueAdapter, KubernetesKueueConfig, StaticHostMount, StaticPvcMount)

__all__ = [
    "KubernetesInvocationExecutor", "KubernetesKueueAdapter",
    "KubernetesKueueConfig", "LocalProcessAdapter", "StaticHostMount",
    "StaticPvcMount",
]
