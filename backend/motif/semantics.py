"""Scientific semantics for Motif.

This module is deliberately independent of schedulers and numerical engines.  It
defines the meanings that those implementations must preserve: orthogonal state,
scientific object identity, typed evidence, compatibility, invalidation and
parent/state aggregation.  Keeping these rules here prevents a worker exit code or a
docking score from silently becoming a portfolio decision.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json
from typing import Any, Iterable, Mapping


class ExecutionState(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    ADMITTED = "admitted"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    LOST = "lost"


class ApplicabilityState(StrEnum):
    UNKNOWN = "unknown"
    APPLICABLE = "applicable"
    NOT_APPLICABLE = "not_applicable"
    UNSUPPORTED = "unsupported"
    OUTSIDE_VALIDATED_DOMAIN = "outside_validated_domain"


class ScientificState(StrEnum):
    NOT_ASSESSED = "not_assessed"
    ACCEPTED = "accepted"
    PROVISIONAL = "provisional"
    REJECTED = "rejected"


class DecisionDisposition(StrEnum):
    PENDING = "pending"
    SELECTED = "selected"
    RESERVE = "reserve"
    DEFERRED = "deferred"
    REJECTED = "rejected"
    REFUSED = "refused"


class ClaimEligibility(StrEnum):
    ELIGIBLE = "eligible"
    INELIGIBLE_TECHNICAL_SMOKE = "ineligible_technical_smoke"
    INELIGIBLE_UNVALIDATED_METHOD = "ineligible_unvalidated_method"
    INELIGIBLE_OUTSIDE_DOMAIN = "ineligible_outside_validated_domain"
    INELIGIBLE_PROVISIONAL_QUALITY = "ineligible_provisional_quality"
    INELIGIBLE_STALE = "ineligible_stale"
    INELIGIBLE_CONFLICT = "ineligible_conflict"
    INELIGIBLE_MISSING_DEPENDENCY = "ineligible_missing_dependency"


class EvidenceKind(StrEnum):
    SCALAR_ESTIMATE = "scalar_estimate"
    CENSORED_ESTIMATE = "censored_estimate"
    DISTRIBUTION = "distribution"
    POSE_ENSEMBLE = "pose_ensemble"
    TRAJECTORY = "trajectory"
    TRANSFORMATION = "transformation"
    NETWORK_ESTIMATE = "network_estimate"
    QUALITATIVE_GATE = "qualitative_gate"
    CONFLICT = "conflict"


EXECUTION_TRANSITIONS: dict[ExecutionState, frozenset[ExecutionState]] = {
    ExecutionState.CREATED: frozenset({ExecutionState.QUEUED, ExecutionState.CANCELLED}),
    ExecutionState.QUEUED: frozenset({ExecutionState.ADMITTED, ExecutionState.CANCELLED,
                                      ExecutionState.LOST}),
    ExecutionState.ADMITTED: frozenset({ExecutionState.RUNNING, ExecutionState.CANCELLED,
                                        ExecutionState.LOST}),
    ExecutionState.RUNNING: frozenset({ExecutionState.SUCCEEDED, ExecutionState.FAILED,
                                       ExecutionState.CANCELLED, ExecutionState.LOST}),
    ExecutionState.SUCCEEDED: frozenset(),
    ExecutionState.FAILED: frozenset(),
    ExecutionState.CANCELLED: frozenset(),
    ExecutionState.LOST: frozenset(),
}


def require_execution_transition(before: str, after: str) -> None:
    """Reject state jumps and retries that mutate an old terminal attempt."""
    source, target = ExecutionState(before), ExecutionState(after)
    if target not in EXECUTION_TRANSITIONS[source]:
        raise ValueError(f"invalid execution transition {source.value}->{target.value}; "
                         "a retry must create a new Attempt")


def canonical_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class ScientificIdentity:
    """The cache identity of a scientific claim, not an execution attempt."""

    object_kind: str
    input_digests: tuple[str, ...]
    method_release_digest: str
    parameter_digest: str
    condition_digest: str
    environment_digest: str
    numeric_contract_digest: str

    def digest(self) -> str:
        return canonical_digest({
            "object_kind": self.object_kind,
            "input_digests": sorted(self.input_digests),
            "method_release_digest": self.method_release_digest,
            "parameter_digest": self.parameter_digest,
            "condition_digest": self.condition_digest,
            "environment_digest": self.environment_digest,
            "numeric_contract_digest": self.numeric_contract_digest,
        })


@dataclass(frozen=True)
class OrthogonalState:
    execution: ExecutionState
    applicability: ApplicabilityState = ApplicabilityState.UNKNOWN
    scientific: ScientificState = ScientificState.NOT_ASSESSED
    disposition: DecisionDisposition = DecisionDisposition.PENDING
    claim_eligibility: ClaimEligibility = ClaimEligibility.INELIGIBLE_UNVALIDATED_METHOD
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.execution is ExecutionState.FAILED and self.scientific is ScientificState.REJECTED:
            raise ValueError("execution failure cannot itself reject a scientific hypothesis")
        if (self.applicability in {ApplicabilityState.UNSUPPORTED,
                                  ApplicabilityState.NOT_APPLICABLE}
                and self.claim_eligibility is ClaimEligibility.ELIGIBLE):
            raise ValueError("unsupported/not-applicable output cannot be claim eligible")
        if self.disposition is DecisionDisposition.REFUSED and not self.reason_codes:
            raise ValueError("a refused decision requires typed reason_codes")


@dataclass(frozen=True)
class MethodOutcome:
    outcome_id: str
    method_run_ref: Mapping[str, str]
    execution_state: ExecutionState
    artifact_refs: tuple[Mapping[str, str], ...] = ()
    telemetry_ref: Mapping[str, str] | None = None
    error_ref: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        if self.execution_state is ExecutionState.SUCCEEDED and self.error_ref is not None:
            raise ValueError("succeeded MethodOutcome cannot carry a terminal error")
        if self.execution_state is not ExecutionState.SUCCEEDED and self.artifact_refs:
            # Checkpoints/logs belong on Attempt.  Scientific output artifacts commit
            # only with the terminal success event.
            raise ValueError("non-success MethodOutcome cannot publish scientific artifacts")


@dataclass(frozen=True)
class EvidenceItem:
    evidence_id: str
    kind: EvidenceKind
    subject_ref: Mapping[str, str]
    condition_ref: Mapping[str, str]
    method_release_ref: Mapping[str, str]
    outcome_ref: Mapping[str, str]
    payload: Mapping[str, Any]
    applicability: ApplicabilityState
    scientific_state: ScientificState
    claim_eligibility: ClaimEligibility
    dependency_refs: tuple[Mapping[str, str], ...] = ()
    shared_assumption_refs: tuple[Mapping[str, str], ...] = ()
    supersedes: tuple[Mapping[str, str], ...] = ()
    stale: bool = False

    def __post_init__(self) -> None:
        if self.stale and self.claim_eligibility is ClaimEligibility.ELIGIBLE:
            raise ValueError("stale evidence cannot be claim eligible")
        if self.kind is EvidenceKind.CONFLICT and self.scientific_state is ScientificState.ACCEPTED:
            raise ValueError("ConflictEvidence cannot be scientifically accepted")
        if "failure_probability" in self.payload or "retry_count" in self.payload:
            raise ValueError("operational failure risk belongs to MethodOutcome/telemetry, not Evidence")
        required_payload_keys = {
            EvidenceKind.SCALAR_ESTIMATE: {"value", "unit"},
            EvidenceKind.CENSORED_ESTIMATE: {"qualifier", "bound", "unit"},
            EvidenceKind.DISTRIBUTION: {
                "artifact_ref", "distribution_family", "support", "summary",
                "calibration_population_ref",
            },
            EvidenceKind.POSE_ENSEMBLE: {
                "artifact_ref", "microstate_ref", "receptor_state_ref",
                "binding_site_ref", "pose_protocol_release_ref", "pose_count",
                "cluster_count", "score_semantics",
            },
            EvidenceKind.TRAJECTORY: {
                "artifact_ref", "simulation_run_ref", "analysis_protocol_ref",
                "repeat_index", "frame_count", "time_unit", "block_assessment_ref",
                "outcome",
            },
            EvidenceKind.TRANSFORMATION: {
                "artifact_ref", "transformation_ref", "edge_orientation",
                "sign_convention_ref", "complex_leg_ref", "solvent_leg_ref",
                "repeat_index", "estimate", "unit", "effective_samples",
            },
            EvidenceKind.NETWORK_ESTIMATE: {
                "artifact_ref", "network_ref", "reference_node_ref", "reference_value",
                "edge_estimates_ref", "edge_covariance_ref", "node_estimates_ref",
                "node_covariance_ref", "cycle_basis_ref", "cycle_residuals_ref",
                "failed_edge_refs", "sign_convention_ref",
            },
            EvidenceKind.QUALITATIVE_GATE: {"result", "reason_codes"},
            EvidenceKind.CONFLICT: {
                "evidence_refs", "conflict_kind", "resolution_state",
            },
        }
        missing = required_payload_keys[self.kind] - set(self.payload)
        if missing:
            raise ValueError(f"{self.kind.value} evidence payload is missing {sorted(missing)}")
        if (self.kind is EvidenceKind.NETWORK_ESTIMATE
                and self.payload.get("reference_value") != 0):
            raise ValueError("network estimate gauge must fix the reference node to zero")


def assemble_evidence_snapshot(items: Iterable[EvidenceItem], *,
                               required_condition_ref: Mapping[str, str],
                               valid_dependency_ids: set[str]) -> dict[str, Any]:
    """Assemble compatible, fresh evidence and make every exclusion explicit."""
    accepted: list[EvidenceItem] = []
    excluded: list[dict[str, str]] = []
    assumptions: dict[str, list[str]] = {}
    for item in items:
        reason = None
        if dict(item.condition_ref) != dict(required_condition_ref):
            reason = "CONDITION_INCOMPATIBLE"
        elif item.stale:
            reason = "EVIDENCE_STALE"
        elif any(ref.get("id") not in valid_dependency_ids for ref in item.dependency_refs):
            reason = "DEPENDENCY_INVALID"
        if reason:
            excluded.append({"evidence_id": item.evidence_id, "reason_code": reason})
            continue
        accepted.append(item)
        for ref in item.shared_assumption_refs:
            assumptions.setdefault(str(ref.get("id")), []).append(item.evidence_id)
    dependency_groups = [
        {"assumption_ref": {"kind": "assumption", "id": key}, "evidence_ids": ids}
        for key, ids in sorted(assumptions.items()) if len(ids) > 1
    ]
    return {
        "schema_version": "3.0",
        "condition_ref": dict(required_condition_ref),
        "evidence_ids": [item.evidence_id for item in accepted],
        "excluded": excluded,
        "dependency_groups": dependency_groups,
        "digest": canonical_digest({
            "condition_ref": dict(required_condition_ref),
            "evidence_ids": [item.evidence_id for item in accepted],
            "excluded": excluded,
            "dependency_groups": dependency_groups,
        }),
    }


def aggregate_state_values(states: Iterable[Mapping[str, Any]], *,
                           policy: str = "population_weighted") -> dict[str, Any]:
    """Aggregate microstates without allowing a best-state/receptor lottery.

    Every state must declare retained population and the same condition.  Missing
    population mass is reported and widens the result rather than being renormalized
    away without a trace.
    """
    rows = list(states)
    if not rows:
        raise ValueError("at least one microstate estimate is required")
    conditions = {canonical_digest(row["condition"]) for row in rows}
    if len(conditions) != 1:
        raise ValueError("microstate estimates have incompatible conditions")
    populations = [float(row["population"]) for row in rows]
    if any(value < 0 or value > 1 for value in populations):
        raise ValueError("microstate population must be in [0,1]")
    retained = sum(populations)
    if retained <= 0 or retained > 1.000001:
        raise ValueError("retained microstate population must be in (0,1]")
    values = [float(row["value"]) for row in rows]
    if policy == "population_weighted":
        estimate = sum(p * v for p, v in zip(populations, values)) / retained
    elif policy == "worst_case":
        direction = {row.get("adverse_direction", "high") for row in rows}
        if len(direction) != 1:
            raise ValueError("worst_case aggregation requires one adverse_direction")
        estimate = max(values) if direction.pop() == "high" else min(values)
    else:
        raise ValueError(f"unsupported parent aggregation policy {policy!r}")
    return {
        "policy": policy,
        "estimate": estimate,
        "retained_population_mass": retained,
        "discarded_population_mass": max(0.0, 1.0 - retained),
        "state_count": len(rows),
        "state_ids": [str(row["state_ref"]["id"]) for row in rows],
        "lottery_prevented": True,
    }


__all__ = [name for name in globals() if not name.startswith("_")]
