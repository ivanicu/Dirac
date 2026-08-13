"""Fixed entrypoint for official OpenFE ligand-network and mapping planning.

Executed by the pinned OpenFE runtime.  It accepts one bounded JSON document and emits
one JSON document; callers never supply a command or module name.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import openfe
from openfe.setup import ligand_network_planning
from rdkit import Chem
from rdkit.Chem import AllChem


def _component(row: dict, seed: int):
    molecule = Chem.MolFromSmiles(row["smiles"])
    if molecule is None:
        raise ValueError(f"cannot parse {row['id']!r}")
    molecule = Chem.AddHs(molecule)
    if AllChem.EmbedMolecule(molecule, randomSeed=seed) != 0:
        raise ValueError(f"3D embedding failed for {row['id']!r}")
    return openfe.SmallMoleculeComponent.from_rdkit(molecule, name=row["id"])


def _pairs(mapping) -> set[tuple[int, int]]:
    return {(int(left), int(right))
            for left, right in mapping.componentA_to_componentB.items()}


def plan(document: dict) -> dict:
    compounds = document["compounds"]
    if not 2 <= len(compounds) <= 256:
        raise ValueError("OpenFE network planning requires 2..256 compounds")
    components = [_component(row, int(document.get("seed", 0)) + index)
                  for index, row in enumerate(compounds)]
    lomap = openfe.LomapAtomMapper(time=int(document.get("lomap_timeout", 20)),
                                  threed=False)
    kartograf = openfe.KartografAtomMapper(atom_map_hydrogens=False,
                                           allow_bond_breaks=False)
    network = ligand_network_planning.generate_minimal_redundant_network(
        ligands=components, mappers=[lomap, kartograf],
        scorer=openfe.lomap_scorers.default_lomap_score,
        progress=False, mst_num=int(document.get("mst_num", 2)), n_processes=1)
    by_name = {component.name: component for component in components}
    edges = []
    for mapping in sorted(network.edges,
                          key=lambda edge: (edge.componentA.name, edge.componentB.name)):
        left, right = mapping.componentA.name, mapping.componentB.name
        proposals = {}
        for name, mapper in (("lomap", lomap), ("kartograf", kartograf)):
            proposed = list(mapper.suggest_mappings(by_name[left], by_name[right]))
            if proposed:
                proposals[name] = _pairs(proposed[0])
        names = sorted(proposals)
        disagreement = None
        if len(names) >= 2:
            union = proposals[names[0]] | proposals[names[1]]
            agreement = proposals[names[0]] & proposals[names[1]]
            disagreement = 1.0 - (len(agreement) / len(union) if union else 1.0)
        edges.append({
            "left_id": left, "right_id": right,
            "selected_atom_mapping": sorted([list(pair) for pair in _pairs(mapping)]),
            "mapping_score": float(openfe.lomap_scorers.default_lomap_score(mapping)),
            "mapping_methods": names,
            "mapping_disagreement_jaccard": disagreement,
            "mapping_proposals": {name: sorted([list(pair) for pair in pairs])
                                  for name, pairs in proposals.items()},
        })
    return {
        "schema_version": "1.0", "engine": "OpenFE",
        "engine_version": openfe.__version__,
        "planner": "generate_minimal_redundant_network",
        "mappers": ["LomapAtomMapper", "KartografAtomMapper"],
        "ligand_network": json.loads(network.to_json()),
        "edges": edges,
    }


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: openfe_network_planner.py INPUT.json OUTPUT.json")
    source, target = Path(sys.argv[1]), Path(sys.argv[2])
    if source.stat().st_size > 4 << 20:
        raise ValueError("planner input exceeds 4 MiB")
    document = json.loads(source.read_text())
    target.write_text(json.dumps(plan(document), sort_keys=True,
                                 separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
