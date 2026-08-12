"""Dirac application layer: semantic commands over the transport-free kernel."""

from .dispatcher import CommandDispatcher
from .registry import CommandRegistry

__all__ = ['CommandDispatcher', 'CommandRegistry']
