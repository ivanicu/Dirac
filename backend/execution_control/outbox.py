"""Idempotent transactional-outbox projection primitives."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable


@dataclass(frozen=True)
class OutboxEvent:
    sequence: int
    event_id: str
    aggregate_type: str
    aggregate_id: str
    event_type: str
    payload: dict[str, Any]


class InMemoryProjector:
    """Reference projector used to prove replay and duplicate delivery semantics."""

    def __init__(self, apply: Callable[[OutboxEvent], None]) -> None:
        self._apply = apply
        self.cursor = 0
        self._seen: set[str] = set()

    def replay(self, events: Iterable[OutboxEvent]) -> int:
        for event in sorted(events, key=lambda value: value.sequence):
            if event.sequence <= self.cursor or event.event_id in self._seen:
                continue
            self._apply(event)
            self._seen.add(event.event_id)
            self.cursor = event.sequence
        return self.cursor
