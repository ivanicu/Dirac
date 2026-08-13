"""Evidence-based ModelRelease lifecycle gates.

Small fixtures can prove wiring.  They cannot validate a scientific model.  Every
gate is policy-versioned and reports all unmet criteria rather than promoting from a
single flattering metric.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping


class ReleaseLifecycle(StrEnum):
    TECHNICAL_SMOKE = "technical_smoke"
    CANDIDATE_UNVALIDATED = "candidate_unvalidated"
    SCIENTIFIC_CANDIDATE = "scientific_candidate"
    VALIDATED_RELEASE = "validated_release"
    PROMOTED_RELEASE = "promoted_release"


@dataclass(frozen=True)
class LifecycleThreshold:
    minimum_independent_compounds: int
    minimum_series: int
    minimum_split_groups: int
    minimum_effective_sample_size: float
    maximum_censoring_fraction: float
    maximum_label_noise: float
    minimum_domain_coverage: float


@dataclass(frozen=True)
class ValidationPolicy:
    release_id: str
    thresholds: Mapping[ReleaseLifecycle, LifecycleThreshold]


def assess_release(metrics: Mapping[str, Any], policy: ValidationPolicy,
                   requested: ReleaseLifecycle) -> dict[str, Any]:
    threshold = policy.thresholds[requested]
    checks = {
        "INDEPENDENT_COMPOUNDS": int(metrics.get("independent_compounds", 0))
        >= threshold.minimum_independent_compounds,
        "INDEPENDENT_SERIES": int(metrics.get("independent_series", 0))
        >= threshold.minimum_series,
        "SPLIT_GROUPS": int(metrics.get("split_groups", 0))
        >= threshold.minimum_split_groups,
        "EFFECTIVE_SAMPLE_SIZE": float(metrics.get("effective_sample_size", 0))
        >= threshold.minimum_effective_sample_size,
        "CENSORING_FRACTION": float(metrics.get("censoring_fraction", 1))
        <= threshold.maximum_censoring_fraction,
        "LABEL_NOISE": float(metrics.get("label_noise", float("inf")))
        <= threshold.maximum_label_noise,
        "DOMAIN_COVERAGE": float(metrics.get("domain_coverage", 0))
        >= threshold.minimum_domain_coverage,
        "INDEPENDENT_HOLDOUT": bool(metrics.get("independent_holdout", False))
        or requested in {ReleaseLifecycle.TECHNICAL_SMOKE,
                         ReleaseLifecycle.CANDIDATE_UNVALIDATED},
        "SPECIFICATION_CURVE": bool(metrics.get("specification_curve_complete", False))
        or requested is ReleaseLifecycle.TECHNICAL_SMOKE,
    }
    passed = all(checks.values())
    return {
        "policy_release_id": policy.release_id,
        "requested_lifecycle": requested.value,
        "eligible": passed,
        "granted_lifecycle": requested.value if passed else ReleaseLifecycle.CANDIDATE_UNVALIDATED.value,
        "claim_eligible": passed and requested in {
            ReleaseLifecycle.VALIDATED_RELEASE, ReleaseLifecycle.PROMOTED_RELEASE},
        "checks": checks,
        "unmet_reason_codes": [code for code, value in checks.items() if not value],
        "sample_size_is_smoke_only": int(metrics.get("independent_compounds", 0)) <= 3,
    }


__all__ = ["ReleaseLifecycle", "LifecycleThreshold", "ValidationPolicy", "assess_release"]
