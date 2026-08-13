"""Coupled chemical-state ensemble validation and method-scoped support."""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from motif.semantics import canonical_digest


def build_state_ensemble(*, chemical_entity_ref: Mapping[str, str],
                         condition: Mapping[str, Any], engine: Mapping[str, Any],
                         microstates: Iterable[Mapping[str, Any]],
                         maximum_states: int, minimum_population: float,
                         confidence: str,
                         parent_aggregation_policy: str = "population_weighted") -> dict[str, Any]:
    """Freeze a joint protonation/tautomer/stereo population ensemble."""
    rows = [dict(row) for row in microstates]
    if not rows:
        raise ValueError("chemical state engine returned no microstates")
    if len(rows) > maximum_states:
        raise ValueError("engine output exceeds declared maximum_states; truncate explicitly")
    keys, total = set(), 0.0
    for row in rows:
        required = {"microstate_ref", "protonation_key", "tautomer_key", "stereo_key",
                    "population", "population_uncertainty", "score"}
        if missing := required - set(row):
            raise ValueError(f"microstate misses {sorted(missing)}")
        key = (row["protonation_key"], row["tautomer_key"], row["stereo_key"])
        if key in keys:
            raise ValueError("duplicate coupled protonation/tautomer/stereo state")
        keys.add(key)
        population = float(row["population"])
        if not 0 <= population <= 1:
            raise ValueError("microstate population must be in [0,1]")
        row["retained"] = population >= minimum_population
        row["discard_reason_code"] = None if row["retained"] else "BELOW_POPULATION_THRESHOLD"
        if row["retained"]:
            total += population
    if total <= 0 or total > 1.000001:
        raise ValueError("retained population mass must be in (0,1]")
    document = {
        "chemical_entity_ref": dict(chemical_entity_ref), "condition": dict(condition),
        "enumeration_engine": dict(engine),
        "coupling_policy": "joint_protonation_tautomer_stereo_no_cartesian_product",
        "microstates": rows, "retained_population_mass": total,
        "discarded_population_mass": max(0.0, 1.0 - total),
        "truncation": {"maximum_states": maximum_states,
                       "minimum_population": minimum_population,
                       "reason_codes": (["POPULATION_MASS_DISCARDED"]
                                        if total < .999999 else [])},
        "confidence": confidence, "parent_aggregation_policy": parent_aggregation_policy,
    }
    document["digest"] = canonical_digest(document)
    return document


def assess_method_support(*, chemical_entity_ref: Mapping[str, str],
                          microstate_ref: Mapping[str, str], method_release_ref: Mapping[str, str],
                          system_type: str, capability_contract: Mapping[str, str]) -> dict[str, Any]:
    applicability = capability_contract.get(system_type, "unsupported")
    if applicability not in {"applicable", "unsupported", "not_applicable",
                             "outside_validated_domain"}:
        raise ValueError("invalid method capability value")
    return {
        "chemical_entity_ref": dict(chemical_entity_ref),
        "microstate_ref": dict(microstate_ref),
        "method_release_ref": dict(method_release_ref), "system_type": system_type,
        "applicability": applicability,
        "global_chemical_disposition_changed": False,
        "reason_codes": ([] if applicability == "applicable"
                         else ["METHOD_SYSTEM_TYPE_" + applicability.upper()]),
    }


__all__ = ["build_state_ensemble", "assess_method_support"]
