"""Versioned RDKit proposal strategies with explicit edit/reaction provenance."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable
from uuid import UUID, uuid5
from collections import defaultdict


NAMESPACE = UUID("f88d7051-2fa7-48ce-91e0-469d6dfd8b85")


def _toolkit():
    try:
        from rdkit import Chem
        from rdkit.Chem import rdChemReactions
    except ImportError as exc:
        raise RuntimeError("RDKit is required for Motif proposal generation") from exc
    return Chem, rdChemReactions


def local_edits(parents: Iterable[dict[str, str]], *, transforms: Iterable[dict[str, Any]],
                generator_release_id: str, strategy_release_id: str,
                identity_policy_release_id: str, root_seed: int,
                constraints: dict[str, Any], created_at: str,
                max_proposals: int = 50000) -> list[dict[str, Any]]:
    Chem, reactions = _toolkit()
    output: dict[str, dict[str, Any]] = {}
    transform_list = sorted((dict(item) for item in transforms),
                            key=lambda item: (item["transform_id"], item["version"]))
    for parent in sorted(parents, key=lambda item: item["id"]):
        molecule = Chem.MolFromSmiles(parent["smiles"])
        if molecule is None:
            raise ValueError(f"cannot parse parent {parent['id']}")
        parent_smiles = Chem.MolToSmiles(molecule, canonical=True, isomericSmiles=True)
        for transform in transform_list:
            reaction = reactions.ReactionFromSmarts(transform["reaction_smarts"])
            if reaction is None or reaction.GetNumReactantTemplates() != 1:
                raise ValueError(f"transform {transform['transform_id']} is not unary reaction SMARTS")
            for products in reaction.RunReactants((molecule,)):
                if not products:
                    continue
                product = products[0]
                try:
                    Chem.SanitizeMol(product)
                except Exception:
                    continue
                smiles = Chem.MolToSmiles(product, canonical=True, isomericSmiles=True)
                if smiles == parent_smiles:
                    continue
                key = Chem.MolToInchiKey(product) or hashlib.sha256(smiles.encode()).hexdigest()
                proposal = _proposal(
                    key, smiles, parent, generator_release_id, strategy_release_id,
                    identity_policy_release_id, root_seed, created_at,
                    chemistry_gate(product, constraints),
                    strategy="local_edit", trace={"edits": [{
                        "transform_id": transform["transform_id"],
                        "transform_version": transform["version"],
                        "atom_mapping": _atom_mapping(product),
                        "description": transform["description"],
                        "evidence_artifact_ids": list(transform.get("evidence_artifact_ids", ())),
                    }]})
                output.setdefault(key, proposal)
                if len(output) >= max_proposals:
                    return list(output.values())
    return list(output.values())


def reaction_enumerate(reactants: Iterable[dict[str, str]], *,
                       templates: Iterable[dict[str, Any]],
                       generator_release_id: str, strategy_release_id: str,
                       identity_policy_release_id: str, root_seed: int,
                       constraints: dict[str, Any], created_at: str,
                       max_proposals: int = 50000) -> list[dict[str, Any]]:
    Chem, reactions = _toolkit()
    molecules = []
    for block in sorted(reactants, key=lambda item: item["id"]):
        molecule = Chem.MolFromSmiles(block["smiles"])
        if molecule is None:
            raise ValueError(f"cannot parse reactant {block['id']}")
        molecules.append((dict(block), molecule))
    output: dict[str, dict[str, Any]] = {}
    for template in sorted(templates, key=lambda item: (item["template_id"], item["version"])):
        reaction = reactions.ReactionFromSmarts(template["reaction_smarts"])
        if reaction is None:
            raise ValueError(f"cannot parse reaction template {template['template_id']}")
        arity = reaction.GetNumReactantTemplates()
        if arity not in (1, 2):
            raise ValueError("reference enumerator supports unary or binary templates")
        combinations = [((block, molecule),) for block, molecule in molecules]
        if arity == 2:
            combinations = [((left, lmol), (right, rmol))
                            for left, lmol in molecules for right, rmol in molecules
                            if left["id"] != right["id"]]
        for combination in combinations:
            for products in reaction.RunReactants(tuple(item[1] for item in combination)):
                if not products:
                    continue
                product = products[0]
                try:
                    Chem.SanitizeMol(product)
                except Exception:
                    continue
                smiles = Chem.MolToSmiles(product, canonical=True, isomericSmiles=True)
                key = Chem.MolToInchiKey(product) or hashlib.sha256(smiles.encode()).hexdigest()
                proposal = _proposal(
                    key, smiles, combination[0][0], generator_release_id,
                    strategy_release_id, identity_policy_release_id, root_seed,
                    created_at,
                    chemistry_gate(product, constraints), strategy="reaction",
                    trace={"reaction": {
                        "template_id": template["template_id"],
                        "template_version": template["version"],
                        "reactants": [{
                            "role": (template.get("reactant_roles") or
                                     [f"reactant_{index + 1}" for index in range(arity)])[index],
                            "ref": {"kind": "building_block", "id": item[0]["id"]},
                        } for index, item in enumerate(combination)],
                        "atom_mapping": _atom_mapping(product),
                    }})
                output.setdefault(key, proposal)
                if len(output) >= max_proposals:
                    return list(output.values())
    return list(output.values())


def chemistry_gate(molecule: Any, constraints: dict[str, Any]) -> dict[str, Any]:
    Chem, _ = _toolkit()
    from rdkit.Chem import Crippen, Descriptors, Lipinski, rdMolDescriptors
    from rdkit.Chem.FilterCatalog import FilterCatalog, FilterCatalogParams
    reasons = []
    heavy_atoms = molecule.GetNumHeavyAtoms()
    charge = Chem.GetFormalCharge(molecule)
    properties = {
        "heavy_atoms": heavy_atoms, "formal_charge": charge,
        "molecular_weight": float(Descriptors.MolWt(molecule)),
        "clogp": float(Crippen.MolLogP(molecule)),
        "tpsa": float(rdMolDescriptors.CalcTPSA(molecule)),
        "hbd": int(Lipinski.NumHDonors(molecule)),
        "hba": int(Lipinski.NumHAcceptors(molecule)),
        "rotatable_bonds": int(Lipinski.NumRotatableBonds(molecule)),
        "ring_count": int(Lipinski.RingCount(molecule)),
    }
    if heavy_atoms > int(constraints.get("max_heavy_atoms", 1000000)):
        reasons.append("MAX_HEAVY_ATOMS")
    lower, upper = constraints.get("charge_range", [-1000, 1000])
    if charge < lower or charge > upper:
        reasons.append("FORMAL_CHARGE_RANGE")
    for key, property_key in (
            ("molecular_weight_range", "molecular_weight"),
            ("clogp_range", "clogp"), ("tpsa_range", "tpsa"),
            ("hbd_range", "hbd"), ("hba_range", "hba"),
            ("rotatable_bonds_range", "rotatable_bonds"),
            ("ring_count_range", "ring_count")):
        if key in constraints:
            minimum, maximum = constraints[key]
            if properties[property_key] < minimum or properties[property_key] > maximum:
                reasons.append(key.upper())
    for pattern in constraints.get("forbidden_smarts", ()):
        query = Chem.MolFromSmarts(pattern)
        if query is None:
            raise ValueError(f"cannot parse forbidden SMARTS {pattern!r}")
        if molecule.HasSubstructMatch(query):
            reasons.append("FORBIDDEN_SUBSTRUCTURE")
    for pattern in constraints.get("required_smarts", ()):
        query = Chem.MolFromSmarts(pattern)
        if query is None:
            raise ValueError(f"cannot parse required SMARTS {pattern!r}")
        if not molecule.HasSubstructMatch(query):
            reasons.append("REQUIRED_SUBSTRUCTURE_MISSING")
    if constraints.get("reject_unassigned_stereo", False):
        unassigned = [center for center, label in
                      Chem.FindMolChiralCenters(molecule, includeUnassigned=True)
                      if label == "?"]
        properties["unassigned_stereocenters"] = unassigned
        if unassigned:
            reasons.append("UNASSIGNED_STEREOCHEMISTRY")
    if constraints.get("pains", False):
        parameters = FilterCatalogParams()
        parameters.AddCatalog(FilterCatalogParams.FilterCatalogs.PAINS)
        match = FilterCatalog(parameters).GetFirstMatch(molecule)
        properties["pains_match"] = match.GetDescription() if match else None
        if match:
            reasons.append("PAINS_ALERT")
    reactive_patterns = {
        "acyl_halide": "[CX3](=[OX1])[F,Cl,Br,I]",
        "alkyl_halide": "[CX4][Cl,Br,I]",
        "isocyanate": "N=C=O", "aldehyde": "[CX3H1](=O)[#6]",
        "peroxide": "[OX2,OX1-][OX2,OX1-]",
        "azide": "[$([NX1-]=[NX2+]=[NX1-]),$([NX1]#[NX2+][NX1-])]",
    }
    matched_reactive = []
    for name in constraints.get("reactive_group_filters", ()):
        if name not in reactive_patterns:
            raise ValueError(f"unknown reactive-group filter {name!r}")
        if molecule.HasSubstructMatch(Chem.MolFromSmarts(reactive_patterns[name])):
            matched_reactive.append(name)
    properties["reactive_group_matches"] = matched_reactive
    if matched_reactive:
        reasons.append("REACTIVE_GROUP")
    return {"status": "refuse" if reasons else "pass",
            "reason_codes": sorted(set(reasons)),
            "details": properties}


def generator_metrics(proposals: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(proposals)
    smiles = [row["proposed_identity"]["canonical_smiles"] for row in rows]
    refused = sum(row["chemistry_gate"]["status"] == "refuse" for row in rows)
    return {"count": len(rows), "unique_count": len(set(smiles)),
            "validity": 1.0 if rows else 0.0,
            "uniqueness": len(set(smiles)) / len(rows) if rows else 0.0,
            "refused_fraction": refused / len(rows) if rows else 0.0,
            "novelty_is_not_success": True}


def apply_proposal_quotas(proposals: Iterable[dict[str, Any]], *,
                          max_per_parent: int, max_per_template: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Deterministically prevent a prolific parent/template from owning a cycle."""
    if max_per_parent < 1 or max_per_template < 1:
        raise ValueError("proposal quotas must be positive")
    parent_counts: dict[str, int] = defaultdict(int)
    template_counts: dict[str, int] = defaultdict(int)
    accepted, excluded = [], []
    for proposal in sorted(proposals, key=lambda row: row["proposal_id"]):
        parent = proposal["parents"][0]["id"]
        reaction = proposal["generation_trace"].get("reaction") or {}
        template = str(reaction.get("template_id", "local_edit"))
        reason = None
        if parent_counts[parent] >= max_per_parent:
            reason = "PARENT_QUOTA_REACHED"
        elif template_counts[template] >= max_per_template:
            reason = "TEMPLATE_QUOTA_REACHED"
        if reason:
            excluded.append({"proposal_id": proposal["proposal_id"], "reason_code": reason})
            continue
        accepted.append(proposal)
        parent_counts[parent] += 1
        template_counts[template] += 1
    return accepted, {"excluded": excluded, "parent_counts": dict(parent_counts),
                      "template_counts": dict(template_counts)}


ROUTE_TRANSITIONS = {
    "not_assessed": {"route_proposed", "unsupported"},
    "route_proposed": {"plausibility_assessed", "unsupported"},
    "plausibility_assessed": {"supported", "rejected", "needs_review"},
    "needs_review": {"supported", "rejected"},
    "supported": set(), "rejected": set(), "unsupported": set(),
}


def advance_route_assessment(before: str, after: str) -> None:
    if after not in ROUTE_TRANSITIONS.get(before, set()):
        raise ValueError(f"invalid route assessment transition {before}->{after}")


def _proposal(key: str, smiles: str, parent: dict[str, str], generator_release_id: str,
              strategy_release_id: str, identity_policy_release_id: str, root_seed: int,
              created_at: str, gate: dict[str, Any], *, strategy: str,
              trace: dict[str, Any]) -> dict[str, Any]:
    identifier = str(uuid5(NAMESPACE, f"{generator_release_id}:{strategy}:{key}"))
    return {
        "schema_version": "2.0", "proposal_id": identifier,
        "compound": {"kind": "compound", "id": f"proposed:{key}"},
        "proposed_identity": {
            "canonical_smiles": smiles, "inchi_key": key,
            "identity_policy_release_id": identity_policy_release_id,
            "stereochemistry_status": "complete" if "@" in smiles else "not_applicable"},
        "parents": [{"kind": "compound", "id": parent["id"]}],
        "lineage_depth": int(parent.get("lineage_depth", 0)) + 1,
        "duplicate_class": "novel_identity",
        "strategy": strategy, "generator_release_id": generator_release_id,
        "generation_trace": {
            "root_seed": root_seed, "strategy_release_id": strategy_release_id,
            **trace, "constraints_applied_during_generation": [],
            "constraints_applied_post_generation": ["identity", "chemistry"]},
        "synthesis": {
            "status": "plausible" if strategy == "reaction" else "route_unknown",
            "route_depth": 1 if strategy == "reaction" else None,
            "estimated_cost": None, "currency": None, "estimated_days": None,
            "reason_codes": ["TEMPLATE_ROUTE"] if strategy == "reaction" else ["ROUTE_UNKNOWN"]},
        "route_assessment": {
            "state": "route_proposed" if strategy == "reaction" else "not_assessed",
            "predicate_release_id": strategy_release_id,
            "checks": {"template_match": strategy == "reaction",
                       "all_reactant_roles_bound": strategy == "reaction"},
            "reason_codes": (["TEMPLATE_ROUTE_PROPOSED"] if strategy == "reaction"
                             else ["ROUTE_NOT_ASSESSED"]),
        },
        "identity_gate": {"status": "pass", "reason_codes": [],
                          "details": {"deduplicated_by": "InChIKey"}},
        "chemistry_gate": gate, "warnings": [], "created_at": created_at}


def _atom_mapping(molecule: Any) -> dict[str, int]:
    return {str(atom.GetIdx()): int(atom.GetAtomMapNum())
            for atom in molecule.GetAtoms() if atom.GetAtomMapNum()}


__all__ = ["local_edits", "reaction_enumerate", "chemistry_gate",
           "generator_metrics", "apply_proposal_quotas", "advance_route_assessment"]
