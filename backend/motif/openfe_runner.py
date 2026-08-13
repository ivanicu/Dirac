"""Real OpenFE edge execution behind Dirac's governed worker boundary."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from typing import Any

import failures
from invocation import HandlerResult, InvocationContext


OPENFE_VERSION = "1.11.1"
OPENFE_INSTALLER_SHA256 = (
    "28be1bdd69c99d4224af45cc8b7fdfe081ebc2a5a1ded56a66b37df0f506dcd6")
POSIX_SHELL_SHA256 = "c626229526bb58ec2d0f585f3c3ae1412e6f973b4353385042d11c38d8426917"
_AMBER_WRAPPERS = (
    "am1bcc", "antechamber", "atomtype", "bondtype", "espgen", "match",
    "match_atomname", "parmcal", "parmchk2", "prepgen", "reduce", "residuegen",
    "respgen",
)
_ANALYSIS_OVERRIDE = """\
import os
from openfe.protocols.openmm_utils.multistate_analysis import MultistateEquilFEAnalysis

_original = MultistateEquilFEAnalysis._get_free_energy
_count = int(os.environ["DIRAC_OPENFE_ANALYSIS_BOOTSTRAPS"])

def _dirac_get_free_energy(analyzer, u_ln, N_l, bootstraps=1000, return_units=None):
    kwargs = {"bootstraps": _count}
    if return_units is not None:
        kwargs["return_units"] = return_units
    return _original(analyzer, u_ln, N_l, **kwargs)

MultistateEquilFEAnalysis._get_free_energy = staticmethod(_dirac_get_free_energy)
"""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _quantity(value: Any) -> tuple[float | None, str | None]:
    """Decode the JSON representation used by openff-units/GUFE."""
    if value is None:
        return None, None
    if isinstance(value, (int, float)):
        return float(value), None
    if isinstance(value, dict):
        magnitude = value.get("magnitude", value.get("m"))
        unit = value.get("unit")
        if isinstance(magnitude, (int, float)):
            return float(magnitude), str(unit) if unit is not None else None
        # JSON_HANDLER may encode quantities under tagged payloads.
        for child in value.values():
            decoded = _quantity(child)
            if decoded[0] is not None:
                return decoded
    return None, None


def _gufe_component_types(value: Any) -> set[str]:
    """Collect serialized GUFE type names without importing the OpenFE runtime."""
    found: set[str] = set()
    if isinstance(value, dict):
        qualname = value.get("__qualname__")
        if isinstance(qualname, str):
            found.add(qualname)
        for child in value.values():
            found.update(_gufe_component_types(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            found.update(_gufe_component_types(child))
    return found


def _extract_gufe_native_objects(value: Any) -> list[dict[str, Any]]:
    """Inventory native OpenFE/GUFE objects that must survive as artifacts."""
    required = {"LigandNetwork", "ChemicalSystem", "AlchemicalNetwork",
                "Transformation", "ProtocolUnit"}
    found: dict[tuple[str, str], dict[str, Any]] = {}
    def visit(node: Any) -> None:
        if isinstance(node, dict):
            kind = node.get("__qualname__")
            key = node.get(":gufe-key:") or node.get("key")
            if kind in required:
                serialized = _canonical(node)
                digest = "sha256:" + hashlib.sha256(serialized).hexdigest()
                found[(str(kind), str(key or digest))] = {
                    "kind": kind, "gufe_key": key, "digest": digest,
                    "serialized": node,
                }
            for child in node.values():
                visit(child)
        elif isinstance(node, (list, tuple)):
            for child in node:
                visit(child)
    visit(value)
    return [found[key] for key in sorted(found)]


def _terminate(process: subprocess.Popen, *, grace_seconds: float = 5.0) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=grace_seconds)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=grace_seconds)


def _prepare_runtime_environment(runtime: Path, work_root: Path,
                                 source: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Expose AmberTools without requiring Bash in the immutable worker image.

    AmberTools ships tiny Bash wrappers whose only job is to export the prefix
    environment and call ``bin/wrapped_progs/<name>``. The restricted worker
    intentionally has no Bash. Symlinks in writable attempt scratch select the
    same compiled programs while this function supplies the wrapper environment.
    """
    env = dict(source)
    amberhome = work_root / "amberhome"
    shim_dir = amberhome / "bin"
    shim_dir.mkdir(parents=True, exist_ok=True)
    for child in (runtime / "bin").iterdir():
        link = shim_dir / child.name
        if link.exists() or link.is_symlink():
            continue
        link.symlink_to(child)
    linked: list[str] = []
    wrapped = runtime / "bin" / "wrapped_progs"
    for name in _AMBER_WRAPPERS:
        target = wrapped / name
        if not target.is_file():
            continue
        link = shim_dir / name
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(target)
        linked.append(name)
    for name in ("AmberTools", "dat", "lib"):
        target = runtime / name
        link = amberhome / name
        if target.exists() and not (link.exists() or link.is_symlink()):
            link.symlink_to(target)
    env["AMBERHOME"] = str(amberhome)
    env["PATH"] = f"{shim_dir}:{runtime / 'bin'}:{env.get('PATH', '')}"
    env["LD_LIBRARY_PATH"] = (
        f"{runtime / 'lib'}:{env.get('LD_LIBRARY_PATH', '')}").rstrip(":")
    env["PERL5LIB"] = f"{runtime / 'lib/perl'}:{env.get('PERL5LIB', '')}".rstrip(":")
    env["PYTHONPATH"] = (
        f"{runtime / 'lib/python3.12/site-packages'}:{env.get('PYTHONPATH', '')}"
    ).rstrip(":")
    env["QUICK_BASIS"] = str(runtime / "AmberTools/src/quick/basis")
    return env, linked


def _configure_analysis_environment(env: dict[str, str], work_root: Path,
                                    bootstraps: int) -> None:
    if bootstraps == 1000:
        return
    (work_root / "sitecustomize.py").write_text(_ANALYSIS_OVERRIDE)
    env["DIRAC_OPENFE_ANALYSIS_BOOTSTRAPS"] = str(bootstraps)
    env["PYTHONPATH"] = f"{work_root}:{env.get('PYTHONPATH', '')}".rstrip(":")


def execute_openfe_edge(payload: dict[str, Any], ctx: InvocationContext) -> HandlerResult:
    """Execute one serialized OpenFE Transformation with resumable quickrun.

    A completed process is evidence that the declared physical protocol ran. It
    is deliberately not called a converged RBFE result; convergence and the
    complex-minus-solvent thermodynamic cycle are downstream scientific gates.
    """
    leg = payload["leg"]
    target_ref = payload.get("target_ref")
    structure_ref = payload.get("protein_structure_ref")
    cycle_id = payload.get("thermodynamic_cycle_id")
    if leg in {"complex", "solvent"} and (not target_ref or not cycle_id):
        raise failures.DiracInvalidParameters(
            "target_ref and thermodynamic_cycle_id are required for target RBFE legs")
    if leg == "complex" and not structure_ref:
        raise failures.DiracInvalidParameters(
            "protein_structure_ref is required for an OpenFE complex leg")
    transformation = payload["transformation"]
    component_types = _gufe_component_types(transformation)
    native_objects = _extract_gufe_native_objects(transformation)
    has_protein = "ProteinComponent" in component_types
    has_solvent = "SolventComponent" in component_types
    if leg == "complex" and not has_protein:
        raise failures.DiracInvalidParameters(
            "an OpenFE complex leg must serialize a GUFE ProteinComponent")
    if leg in {"solvent", "vacuum"} and has_protein:
        raise failures.DiracInvalidParameters(
            f"an OpenFE {leg} leg cannot contain a GUFE ProteinComponent")
    if leg == "solvent" and not has_solvent:
        raise failures.DiracInvalidParameters(
            "an OpenFE solvent leg must serialize a GUFE SolventComponent")
    if leg == "vacuum" and has_solvent:
        raise failures.DiracInvalidParameters(
            "an OpenFE vacuum leg cannot contain a GUFE SolventComponent")
    transformation_digest = _digest(transformation)
    charge_digest = payload.get("ligand_charge_digest")
    if not isinstance(charge_digest, str) or not charge_digest.startswith("sha256:"):
        raise failures.DiracInvalidParameters(
            "ligand_charge_digest is required and must freeze charges across legs/repeats")
    invariant = payload.get("charge_invariant") or {}
    if invariant.get("digest") != charge_digest or invariant.get("edge_id") != payload["edge_id"]:
        raise failures.DiracInvalidParameters(
            "charge_invariant must bind the declared charge digest to this edge")
    declared = payload.get("transformation_digest")
    if declared is not None and declared != transformation_digest:
        raise failures.DiracInvalidParameters(
            "transformation_digest does not match canonical Transformation JSON",
            details={"expected": transformation_digest, "received": declared})

    attempt_root_text = os.environ.get("DIRAC_MOTIF_ATTEMPT_DIR")
    if not attempt_root_text:
        raise failures.DiracInternal(
            "OpenFE execution requires the fenced Motif worker attempt directory")
    attempt_root = Path(attempt_root_text).resolve()
    work_root = attempt_root / "openfe" / transformation_digest.removeprefix("sha256:")
    work_root.mkdir(parents=True, exist_ok=True)
    transformation_path = work_root / "transformation.json"
    result_path = work_root / "result.json"
    log_path = work_root / "quickrun.log"
    transformation_path.write_bytes(_canonical(transformation))

    executable = Path(os.environ.get(
        "DIRAC_OPENFE_EXECUTABLE",
        "/home/ivan/dirac/openfe-runtime-v2/bin/openfe"))
    if not executable.is_file():
        raise failures.DiracUnsupported(
            "the pinned OpenFE runtime is not installed in this worker",
            details={"expected_executable": str(executable),
                     "required_version": OPENFE_VERSION})
    shell_path = Path("/bin/sh")
    shell_digest = hashlib.sha256(shell_path.read_bytes()).hexdigest()
    if shell_digest != POSIX_SHELL_SHA256:
        raise failures.DiracUnsupported(
            "worker POSIX shell does not match the audited OpenFE runtime companion",
            details={"expected_sha256": "sha256:" + POSIX_SHELL_SHA256,
                     "received_sha256": "sha256:" + shell_digest})

    command = [str(executable), "quickrun", str(transformation_path),
               "-d", str(work_root), "-o", str(result_path)]
    if payload.get("resume", True):
        command.append("--resume")
    runtime = executable.parent.parent
    env, amber_shims = _prepare_runtime_environment(runtime, work_root, dict(os.environ))
    analysis_bootstraps = int(payload.get("analysis_bootstraps", 1000))
    if analysis_bootstraps != 1000:
        # OpenFE 1.11.1 hard-codes 1000 MBAR bootstraps in its analysis helper
        # and exposes no protocol setting for it. Python's documented
        # sitecustomize hook keeps the official quickrun path while making this
        # expensive estimator explicit and provenance-bearing for smoke/QC runs.
        _configure_analysis_environment(env, work_root, analysis_bootstraps)
    started = datetime.now(timezone.utc)
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            command, stdout=log, stderr=subprocess.STDOUT, env=env,
            cwd=work_root, start_new_session=True)
        try:
            while process.poll() is None:
                if ctx.cancellation_token.requested:
                    _terminate(process)
                    raise failures.DiracCancelled(
                        "OpenFE quickrun cancelled; resumable cache was retained",
                        details={"work_digest": transformation_digest})
                ctx.check_budget()
                time.sleep(.25)
        except BaseException:
            _terminate(process)
            raise
    log_bytes = log_path.read_bytes()
    if process.returncode != 0:
        raise failures.DiracFailure(
            "INTERNAL", "OpenFE quickrun failed",
            details={"returncode": process.returncode,
                     "transformation_digest": transformation_digest,
                     "log_tail": log_bytes.decode("utf-8", "replace")[-4000:]},
            hint={"action": "inspect rbfe.openfe.log and resume the same "
                            "transformation/work identity"})
    if not result_path.is_file():
        raise failures.DiracInternal("OpenFE completed without its result JSON")
    result_bytes = result_path.read_bytes()
    result = json.loads(result_bytes)
    estimate, estimate_unit = _quantity(result.get("estimate"))
    uncertainty, uncertainty_unit = _quantity(result.get("uncertainty"))
    unit = estimate_unit or uncertainty_unit
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    report = {
        "schema_version": "1.0", "engine": "OpenFE",
        "engine_version": OPENFE_VERSION,
        "installer_sha256": "sha256:" + OPENFE_INSTALLER_SHA256,
        "posix_shell_sha256": "sha256:" + POSIX_SHELL_SHA256,
        "edge_id": payload["edge_id"], "leg": leg,
        "target_ref": target_ref, "protein_structure_ref": structure_ref,
        "thermodynamic_cycle_id": cycle_id,
        "repeat_index": payload.get("repeat_index", 0),
        "ligand_charge_digest": charge_digest,
        "analysis_bootstraps": analysis_bootstraps,
        "gufe_component_types": sorted(component_types),
        "gufe_native_objects": [{key: value for key, value in row.items()
                                  if key != "serialized"} for row in native_objects],
        "ambertools_compiled_shims": amber_shims,
        "transformation_digest": transformation_digest,
        "result_sha256": "sha256:" + hashlib.sha256(result_bytes).hexdigest(),
        "elapsed_seconds": elapsed, "returncode": process.returncode,
        "resume_enabled": bool(payload.get("resume", True)),
        "work_identity": transformation_digest,
        "scientific_status": "completed_unvalidated",
        "claim_boundary": (
            "A real OpenFE protocol execution completed. This edge is not a "
            "validated RBFE claim until paired complex/solvent legs, independent "
            "repeats, convergence diagnostics and cycle-closure policy pass."),
    }
    report_bytes = _canonical(report)
    native_bytes = _canonical({
        "schema_version": "1.0", "edge_id": payload["edge_id"],
        "transformation_digest": transformation_digest,
        "objects": native_objects,
    })
    return HandlerResult(
        result={
            "edge_id": payload["edge_id"], "leg": leg,
            "target_ref": target_ref, "protein_structure_ref": structure_ref,
            "thermodynamic_cycle_id": cycle_id,
            "repeat_index": payload.get("repeat_index", 0),
            "ligand_charge_digest": charge_digest,
            "engine": "OpenFE", "engine_version": OPENFE_VERSION,
            "transformation_digest": transformation_digest,
            "estimate": estimate, "uncertainty": uncertainty, "unit": unit,
            "scientific_status": "completed_unvalidated",
            "result_digest": report["result_sha256"],
        },
        artifacts=[("rbfe.openfe.result", result_bytes),
                   ("rbfe.openfe.run_report", report_bytes),
                   ("rbfe.openfe.native_objects", native_bytes),
                   ("rbfe.openfe.log", log_bytes)],
        provenance={"engine": "OpenFE", "engine_version": OPENFE_VERSION,
                    "installer_sha256": "sha256:" + OPENFE_INSTALLER_SHA256,
                    "posix_shell_sha256": "sha256:" + POSIX_SHELL_SHA256,
                    "physical_execution": True,
                    "ambertools_wrapper_mode": "compiled_program_shims",
                    "work_identity": transformation_digest},
        warnings=[{"code": "RBFE_EDGE_UNVALIDATED",
                   "message": report["claim_boundary"]}],
        parameters_used={"resume": bool(payload.get("resume", True)),
                         "leg": leg, "thermodynamic_cycle_id": cycle_id,
                         "repeat_index": payload.get("repeat_index", 0),
                         "analysis_bootstraps": analysis_bootstraps})


def openfe_edge_handler(payload: dict[str, Any], ctx: InvocationContext) -> HandlerResult:
    try:
        return execute_openfe_edge(payload, ctx)
    except failures.DiracFailure:
        raise
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        raise failures.DiracInvalidParameters(str(error)) from error


def openfe_edge_estimate(payload: dict[str, Any]) -> dict[str, Any]:
    return {"available": True, "resource_class": "gpu", "checkpointable": True,
            "estimated_seconds": None,
            "reason": "walltime is encoded by the supplied OpenFE Transformation"}


__all__ = ["OPENFE_INSTALLER_SHA256", "OPENFE_VERSION", "POSIX_SHELL_SHA256", "_digest",
           "_configure_analysis_environment",
           "_extract_gufe_native_objects",
           "_gufe_component_types",
           "_prepare_runtime_environment",
           "execute_openfe_edge", "openfe_edge_estimate", "openfe_edge_handler"]
