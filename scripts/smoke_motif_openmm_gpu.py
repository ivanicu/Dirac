#!/usr/bin/env python3
"""Real CUDA OpenMM checkpoint/restart smoke. Submit only through gpu-run."""
from __future__ import annotations

import base64
import json

import openmm
from openmm import XmlSerializer, unit

from motif.physics import run_openmm_md


def main() -> None:
    system = openmm.System()
    system.addParticle(12 * unit.amu)
    system.addParticle(12 * unit.amu)
    force = openmm.HarmonicBondForce()
    force.addBond(0, 1, .15 * unit.nanometer,
                  1000 * unit.kilojoule_per_mole / unit.nanometer**2)
    system.addForce(force)
    pdb = ("ATOM      1  C1  UNK A   1       0.000   0.000   0.000  1.00  0.00           C  \n"
           "ATOM      2  C2  UNK A   1       1.500   0.000   0.000  1.00  0.00           C  \n"
           "CONECT    1    2\nEND\n")
    system_xml = XmlSerializer.serialize(system)
    first, artifacts = run_openmm_md(
        system_xml=system_xml, topology_pdb=pdb, steps=10,
        report_interval=5, seed=41, platform_name="CUDA")
    second, _ = run_openmm_md(
        system_xml=system_xml, topology_pdb=pdb, steps=5,
        report_interval=5, seed=41, platform_name="CUDA", minimize=False,
        checkpoint_base64=base64.b64encode(artifacts["md.checkpoint"]).decode())
    print(json.dumps({"ok": True, "first": first["digest"],
                      "second": second["digest"], "platform": second["platform"],
                      "resumed": second["resumed"],
                      "properties": second["platform_properties"]}, sort_keys=True))


if __name__ == "__main__":
    main()
