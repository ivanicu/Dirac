"""Versioned RDKit proposal strategies with explicit edit/reaction provenance."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable
from uuid import UUID, uuid5


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
                        "reactants": [{"kind": "building_block", "id": item[0]["id"]}
                                      for item in combination],
                        "atom_mapping": _atom_mapping(product),
                    }})
                output.setdefault(key, proposal)
                if len(output) >= max_proposals:
                    return list(output.values())
    return list(output.values())


def chemistry_gate(molecule: Any, constraints: dict[str, Any]) -> dict[str, Any]:
    Chem, _ = _toolkit()
    reasons = []
    heavy_atoms = molecule.GetNumHeavyAtoms()
    charge = Chem.GetFormalCharge(molecule)
    if heavy_atoms > int(constraints.get("max_heavy_atoms", 1000000)):
        reasons.append("MAX_HEAVY_ATOMS")
    lower, upper = constraints.get("charge_range", [-1000, 1000])
    if charge < lower or charge > upper:
        reasons.append("FORMAL_CHARGE_RANGE")
    for pattern in constraints.get("forbidden_smarts", ()):
        query = Chem.MolFromSmarts(pattern)
        if query is None:
            raise ValueError(f"cannot parse forbidden SMARTS {pattern!r}")
        if molecule.HasSubstructMatch(query):
            reasons.append("FORBIDDEN_SUBSTRUCTURE")
    return {"status": "refuse" if reasons else "pass",
            "reason_codes": sorted(set(reasons)),
            "details": {"heavy_atoms": heavy_atoms, "formal_charge": charge}}


def generator_metrics(proposals: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(proposals)
    smiles = [row["proposed_identity"]["canonical_smiles"] for row in rows]
    refused = sum(row["chemistry_gate"]["status"] == "refuse" for row in rows)
    return {"count": len(rows), "unique_count": len(set(smiles)),
            "validity": 1.0 if rows else 0.0,
            "uniqueness": len(set(smiles)) / len(rows) if rows else 0.0,
            "refused_fraction": refused / len(rows) if rows else 0.0,
            "novelty_is_not_success": True}


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
        "identity_gate": {"status": "pass", "reason_codes": [],
                          "details": {"deduplicated_by": "InChIKey"}},
        "chemistry_gate": gate, "warnings": [], "created_at": created_at}


def _atom_mapping(molecule: Any) -> dict[str, int]:
    return {str(atom.GetIdx()): int(atom.GetAtomMapNum())
            for atom in molecule.GetAtoms() if atom.GetAtomMapNum()}
