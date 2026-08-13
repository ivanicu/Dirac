"""OpenMM execution adapter with restartable, content-addressed outputs.

The caller owns system construction and force-field provenance.  This adapter runs
only a serialized OpenMM System against an explicit PDB topology; it never guesses
missing parameters or silently changes the physical model.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import platform as host_platform
import tempfile
from pathlib import Path
from typing import Any


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _digest(value: Any) -> str:
    return _sha(json.dumps(value, sort_keys=True, separators=(",", ":"),
                           allow_nan=False).encode())


def run_openmm_md(*, system_xml: str, topology_pdb: str, steps: int,
                  temperature_kelvin: float = 300.0,
                  friction_per_ps: float = 1.0, timestep_fs: float = 2.0,
                  seed: int = 0, report_interval: int = 100,
                  checkpoint_base64: str | None = None,
                  minimize: bool = True, platform_name: str | None = None,
                  precision: str = "mixed") -> tuple[dict[str, Any], dict[str, bytes]]:
    """Run or resume a bounded OpenMM trajectory and return portable artifacts."""
    if steps < 1:
        raise ValueError("steps must be positive")
    if temperature_kelvin <= 0 or friction_per_ps <= 0 or timestep_fs <= 0:
        raise ValueError("temperature, friction and timestep must be positive")
    if report_interval < 1:
        raise ValueError("report_interval must be positive")

    import openmm
    from openmm import XmlSerializer, unit
    from openmm.app import (CheckpointReporter, DCDReporter, PDBFile, Simulation)

    try:
        system = XmlSerializer.deserialize(system_xml)
        pdb = PDBFile(io.StringIO(topology_pdb))
    except Exception as exc:
        raise ValueError(f"invalid OpenMM system/topology: {exc}") from exc
    if system.getNumParticles() != pdb.topology.getNumAtoms():
        raise ValueError(
            "OpenMM System particle count does not match PDB topology atom count")

    integrator = openmm.LangevinMiddleIntegrator(
        temperature_kelvin * unit.kelvin,
        friction_per_ps / unit.picosecond,
        timestep_fs * unit.femtosecond)
    integrator.setRandomNumberSeed(int(seed))
    properties: dict[str, str] = {}
    if platform_name:
        try:
            selected_platform = openmm.Platform.getPlatformByName(platform_name)
        except Exception as exc:
            raise ValueError(f"OpenMM platform {platform_name!r} is unavailable") from exc
        property_names = set(selected_platform.getPropertyNames())
        if "Precision" in property_names:
            properties["Precision"] = precision
        simulation = Simulation(pdb.topology, system, integrator,
                                selected_platform, properties)
    else:
        simulation = Simulation(pdb.topology, system, integrator)

    resumed = checkpoint_base64 is not None
    if resumed:
        try:
            simulation.context.loadCheckpoint(base64.b64decode(
                checkpoint_base64, validate=True))
        except Exception as exc:
            raise ValueError(f"checkpoint is invalid or incompatible: {exc}") from exc
    else:
        simulation.context.setPositions(pdb.positions)
        if minimize:
            simulation.minimizeEnergy()
        simulation.context.setVelocitiesToTemperature(
            temperature_kelvin * unit.kelvin, int(seed))

    with tempfile.TemporaryDirectory(prefix="motif-openmm-") as temp_dir:
        temp = Path(temp_dir)
        dcd_path, checkpoint_path = temp / "trajectory.dcd", temp / "state.chk"
        interval = min(report_interval, steps)
        simulation.reporters.append(DCDReporter(str(dcd_path), interval))
        simulation.reporters.append(CheckpointReporter(str(checkpoint_path), interval))
        simulation.step(int(steps))
        simulation.saveCheckpoint(str(checkpoint_path))
        trajectory = dcd_path.read_bytes()
        checkpoint = checkpoint_path.read_bytes()

    state = simulation.context.getState(
        getPositions=True, getVelocities=True, getEnergy=True)
    state_xml = XmlSerializer.serialize(state).encode()
    final_pdb_stream = io.StringIO()
    PDBFile.writeFile(pdb.topology, state.getPositions(), final_pdb_stream,
                      keepIds=True)
    final_pdb = final_pdb_stream.getvalue().encode()
    kinetic = float(state.getKineticEnergy().value_in_unit(
        unit.kilojoule_per_mole))
    potential = float(state.getPotentialEnergy().value_in_unit(
        unit.kilojoule_per_mole))
    used_platform = simulation.context.getPlatform()
    report = {
        "schema_version": "1.0", "kind": "openmm_md_run",
        "openmm_version": openmm.__version__,
        "host_platform": host_platform.platform(),
        "platform": used_platform.getName(),
        "platform_properties": {
            name: used_platform.getPropertyValue(simulation.context, name)
            for name in used_platform.getPropertyNames()
        },
        "system_sha256": _sha(system_xml.encode()),
        "topology_sha256": _sha(topology_pdb.encode()),
        "steps_this_invocation": steps, "resumed": resumed,
        "settings": {
            "temperature_kelvin": temperature_kelvin,
            "friction_per_ps": friction_per_ps, "timestep_fs": timestep_fs,
            "seed": seed, "report_interval": report_interval,
            "minimized_before_start": bool(minimize and not resumed),
        },
        "observables": {
            "potential_energy_kj_mol": potential,
            "kinetic_energy_kj_mol": kinetic,
            "total_energy_kj_mol": potential + kinetic,
        },
        "artifacts": {
            "trajectory_dcd": _sha(trajectory), "checkpoint": _sha(checkpoint),
            "state_xml": _sha(state_xml), "final_pdb": _sha(final_pdb),
        },
        "claim_boundary": (
            "A numerically executed trajectory under the caller-supplied System; "
            "not evidence that the force field, equilibration or sampling is adequate."),
    }
    report["digest"] = _digest(report)
    return report, {
        "md.trajectory": trajectory, "md.checkpoint": checkpoint,
        "md.state": state_xml, "md.final_structure": final_pdb,
    }


__all__ = ["run_openmm_md"]
