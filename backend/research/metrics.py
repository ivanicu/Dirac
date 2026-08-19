"""Bounded-label observability for the durable research loop.

Identifiers belong in durable events, never metric labels.  This registry has a
closed metric vocabulary so a caller cannot accidentally introduce a run,
Campaign, compound, or Artifact cardinality explosion.
"""
from __future__ import annotations

import math
import threading
from collections import defaultdict
from typing import Mapping


_LABELS = {
    "dirac_research_loop_total": ("state", "stage"),
    "dirac_research_loop_transition_total": ("from_stage", "to_stage"),
    "dirac_research_loop_blocked_total": ("reason_code",),
    "dirac_research_loop_reasoner_seconds": ("profile_id", "model_family"),
    "dirac_research_loop_provider_http_total": ("profile_id", "status_class"),
    "dirac_research_loop_provider_attempts": ("profile_id",),
    "dirac_research_loop_proposal_validation_total": ("result",),
    "dirac_research_loop_stale_proposal_total": (),
    "dirac_research_loop_action_total": ("template_id", "result", "risk_class"),
    "dirac_research_loop_approval_seconds": ("template_id",),
    "dirac_research_loop_budget_remaining": ("resource",),
}


def model_family(configured_model: str) -> str:
    value = configured_model.strip().lower()
    if value.startswith("qwen"):
        return "qwen"
    return "other_openai_compatible"


def status_class(status: object) -> str:
    if isinstance(status, int) and 100 <= status <= 599:
        return f"{status // 100}xx"
    return "transport_error"


class BoundedMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple[str, ...]], float] = defaultdict(float)
        self._observations: dict[tuple[str, tuple[str, ...]], tuple[int, float]] = {}
        self._contributions: dict[
            tuple[str, tuple[str, ...]], dict[str, float]
        ] = defaultdict(dict)

    @staticmethod
    def _key(name: str, labels: Mapping[str, object] | None) -> tuple[str, tuple[str, ...]]:
        if name not in _LABELS:
            raise ValueError(f"unregistered research-loop metric: {name}")
        supplied = dict(labels or {})
        required = _LABELS[name]
        if set(supplied) != set(required):
            raise ValueError(
                f"{name} requires labels {required}, got {tuple(sorted(supplied))}"
            )
        values = tuple(str(supplied[label]) for label in required)
        if any(len(value) > 128 or "\n" in value for value in values):
            raise ValueError("metric label value is not bounded")
        return name, values

    def counter(self, name: str, labels: Mapping[str, object] | None = None,
                amount: float = 1.0) -> None:
        key = self._key(name, labels)
        value = float(amount)
        if not math.isfinite(value) or value < 0:
            raise ValueError("counter amount must be finite and non-negative")
        with self._lock:
            self._counters[key] += value

    def observe(self, name: str, value: float,
                labels: Mapping[str, object] | None = None) -> None:
        key = self._key(name, labels)
        number = float(value)
        if not math.isfinite(number) or number < 0:
            raise ValueError("observation must be finite and non-negative")
        with self._lock:
            count, total = self._observations.get(key, (0, 0.0))
            self._observations[key] = (count + 1, total + number)

    def contribute(self, name: str, identity: str, value: float,
                   labels: Mapping[str, object] | None = None) -> None:
        key = self._key(name, labels)
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("gauge contribution must be finite")
        with self._lock:
            self._contributions[key][str(identity)] = number

    def remove_contributions(self, name: str, identity: str) -> None:
        if name not in _LABELS:
            raise ValueError(f"unregistered research-loop metric: {name}")
        with self._lock:
            for key in tuple(self._contributions):
                if key[0] == name:
                    self._contributions[key].pop(str(identity), None)

    def render(self) -> str:
        def label_text(name: str, values: tuple[str, ...]) -> str:
            if not values:
                return ""
            escaped = [value.replace("\\", "\\\\").replace('"', '\\"')
                       for value in values]
            pairs = zip(_LABELS[name], escaped)
            return "{" + ",".join(f'{key}="{value}"' for key, value in pairs) + "}"

        with self._lock:
            counters = dict(self._counters)
            observations = dict(self._observations)
            contributions = {
                key: sum(rows.values()) for key, rows in self._contributions.items()
            }
        lines = ["# Dirac AI-native research-loop bounded metrics"]
        for (name, labels), value in sorted({**counters, **contributions}.items()):
            lines.append(f"{name}{label_text(name, labels)} {value:g}")
        for (name, labels), (count, total) in sorted(observations.items()):
            suffix = label_text(name, labels)
            lines.append(f"{name}_count{suffix} {count}")
            lines.append(f"{name}_sum{suffix} {total:g}")
        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._observations.clear()
            self._contributions.clear()


METRICS = BoundedMetrics()


def render_prometheus() -> str:
    return METRICS.render()
