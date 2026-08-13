"""Scheduler adapters for fixed, versioned Dirac worker entrypoints."""

from .local_process import LocalProcessAdapter
from .kubernetes_kueue import KubernetesKueueAdapter, KubernetesKueueConfig

__all__ = ["KubernetesKueueAdapter", "KubernetesKueueConfig", "LocalProcessAdapter"]
