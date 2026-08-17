"""Executable source-version contract for the governed RBFE path.

The descriptor is the only declaration of a Motif compute unit.  These tests
guard the two silent provenance failures that matter here: runtime hashing a
different function list, and a transitive scientific/runtime helper changing
without moving the registered source version.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import json
from pathlib import Path
import textwrap
from unittest import mock

import pytest

import method_registry as mr


METHOD_IDS = (
    "physics.motif.openfe_edge",
    "physics.motif.rbfe_network",
    "physics.motif.rbfe_system_prepare",
    "physics.motif.rbfe_aggregate",
)

REQUIRED_EXTERNAL_CONSTANTS = {
    "physics.motif.openfe_edge": {
        "OPENFE_VERSION",
        "OPENFE_INSTALLER_SHA256",
        "POSIX_SHELL_SHA256",
        "_AMBER_WRAPPERS",
        "_ANALYSIS_OVERRIDE",
    },
    "physics.motif.rbfe_network": {
        "_OPENFE_NETWORK_PLANNER",
        "_OPENFE_NETWORK_PLANNER_SHA256",
        "backend.motif.structure_methods:_RBFE_NETWORK_CAMPAIGN_KEYS",
        "backend.motif.structure_methods:_RBFE_NETWORK_ATTESTATION_KEY",
    },
    "physics.motif.rbfe_system_prepare": {
        "_RUNTIME",
        "_PROBE",
        "_PROBE_SHA256",
        "_SYSTEM_BUILDER",
        "_SYSTEM_BUILDER_SHA256",
        "_MAPPING_ATTESTATION_FIELDS",
        "backend.motif.openfe_runner:OPENFE_VERSION",
        "backend.motif.openfe_runner:OPENFE_INSTALLER_SHA256",
    },
    "physics.motif.rbfe_aggregate": {
        "_RUNTIME",
        "_PROBE",
        "_PROBE_SHA256",
        "_CONVERGENCE_POLICY",
        "backend.motif.openfe_runner:OPENFE_VERSION",
        "backend.motif.openfe_runner:OPENFE_INSTALLER_SHA256",
    },
}


def _descriptor(method_id: str) -> tuple[Path, dict]:
    path = mr._CONTRACTS / f"{method_id}.method.json"
    return path, json.loads(path.read_text(encoding="utf-8"))


def _runtime_module(descriptor: dict):
    name = descriptor["implementation"]["module"].removeprefix("backend.")
    return importlib.import_module(name)


def _version(method_id: str) -> tuple[str, bytes]:
    path, descriptor = _descriptor(method_id)
    implementation = descriptor["implementation"]
    return mr.unit_version(
        _runtime_module(descriptor),
        implementation["functions"],
        implementation.get("constants", ()),
        descriptor_path=str(path),
    )


def _function_body_tree(function) -> ast.Module:
    definition = ast.parse(textwrap.dedent(inspect.getsource(function))).body[0]
    assert isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef))
    # Signature annotations are provenance-neutral type declarations.  Only
    # body reads can change admission, runtime behaviour, or scientific output.
    return ast.Module(body=definition.body, type_ignores=[])


def _declaration(primary_module, source_module, member_name: str) -> str:
    if source_module.__name__ == primary_module.__name__:
        return member_name
    return f"backend.{source_module.__name__}:{member_name}"


def test_runtime_motif_units_are_loaded_verbatim_from_descriptors():
    """There is no second hard-coded runtime function/constant registry."""
    for path in sorted(mr._CONTRACTS.glob("*.method.json")):
        descriptor = json.loads(path.read_text(encoding="utf-8"))
        implementation = descriptor.get("implementation") or {}
        if not str(implementation.get("module") or "").startswith("backend.motif."):
            continue
        runtime = mr.UNITS[descriptor["method_id"]]
        assert runtime["module"] == implementation["module"].removeprefix("backend.")
        assert runtime["fns"] == implementation["functions"]
        assert runtime["consts"] == implementation.get("constants", [])


@pytest.mark.parametrize(
    "method_id",
    ("physics.motif.openfe_edge", "physics.motif.rbfe_network"),
)
def test_admission_hook_is_inside_the_versioned_source_identity(method_id: str):
    """A pre-cache gate may not change while its cache namespace stays fixed."""
    _, descriptor = _descriptor(method_id)
    admission_ref = descriptor["invocation"]["admission"]["handler"]
    module_name, member_name = admission_ref.split(":", 1)
    implementation = descriptor["implementation"]
    primary = implementation["module"].removeprefix("backend.")
    declaration = (member_name if module_name.removeprefix("backend.") == primary
                   else f"backend.{module_name.removeprefix('backend.')}:{member_name}")

    assert declaration in implementation["functions"]
    assert declaration in mr.UNITS[descriptor["method_id"]]["fns"]


@pytest.mark.parametrize("method_id", METHOD_IDS)
def test_every_transitive_motif_helper_is_declared(method_id: str):
    """A newly called project helper must enter the descriptor in the same edit."""
    _, descriptor = _descriptor(method_id)
    implementation = descriptor["implementation"]
    primary_module = _runtime_module(descriptor)
    declarations = set(implementation["functions"])
    missing: set[tuple[str, str]] = set()

    for declaration in declarations:
        source_module, member_name = mr._resolve_declared_member(
            primary_module, declaration)
        function = getattr(source_module, member_name)
        assert inspect.isfunction(function), f"{declaration} is not a function"
        for node in ast.walk(_function_body_tree(function)):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            candidate = getattr(source_module, node.func.id, None)
            if not (inspect.isfunction(candidate)
                    and candidate.__module__.startswith("motif.")):
                continue
            candidate_module = importlib.import_module(candidate.__module__)
            required = _declaration(
                primary_module, candidate_module, candidate.__name__)
            if required not in declarations:
                missing.add((declaration, required))

    assert not missing, (
        "transitive Motif helpers are absent from implementation.functions: "
        + ", ".join(f"{caller} -> {helper}" for caller, helper in sorted(missing)))


@pytest.mark.parametrize("method_id", METHOD_IDS)
def test_every_module_value_read_by_declared_helpers_is_versioned(method_id: str):
    """Scientific thresholds and admission constants cannot hide outside the hash."""
    _, descriptor = _descriptor(method_id)
    implementation = descriptor["implementation"]
    primary_module = _runtime_module(descriptor)
    function_declarations = set(implementation["functions"])
    constant_declarations = set(implementation.get("constants", ()))
    missing: set[tuple[str, str]] = set()

    for declaration in function_declarations:
        source_module, member_name = mr._resolve_declared_member(
            primary_module, declaration)
        function = getattr(source_module, member_name)
        parsed = ast.parse(textwrap.dedent(inspect.getsource(function))).body[0]
        assert isinstance(parsed, (ast.FunctionDef, ast.AsyncFunctionDef))
        body = ast.Module(body=parsed.body, type_ignores=[])
        local_names = {
            argument.arg
            for argument in (
                *parsed.args.posonlyargs,
                *parsed.args.args,
                *parsed.args.kwonlyargs,
            )
        }
        local_names.update(
            node.id for node in ast.walk(body)
            if isinstance(node, ast.Name)
            and isinstance(node.ctx, (ast.Store, ast.Del)))
        local_names.update(
            node.name for node in ast.walk(body)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))

        for node in ast.walk(body):
            if not (isinstance(node, ast.Name)
                    and isinstance(node.ctx, ast.Load)
                    and node.id not in local_names
                    and hasattr(source_module, node.id)):
                continue
            value = getattr(source_module, node.id)
            if (inspect.ismodule(value) or inspect.isfunction(value)
                    or inspect.isclass(value)):
                continue
            required = _declaration(primary_module, source_module, node.id)
            if required not in constant_declarations:
                missing.add((declaration, required))

    assert not missing, (
        "module values read by implementation helpers are absent from constants: "
        + ", ".join(f"{caller} -> {constant}" for caller, constant in sorted(missing)))
    assert REQUIRED_EXTERNAL_CONSTANTS[method_id] <= constant_declarations


@pytest.mark.parametrize(
    ("method_id", "helper"),
    (
        ("physics.motif.openfe_edge", "attest_openfe_edge_admission"),
        (
            "physics.motif.rbfe_network",
            "backend.motif.structure_methods:attest_rbfe_network_admission",
        ),
        ("physics.motif.rbfe_network", "_plan_with_openfe"),
        ("physics.motif.openfe_edge", "_prepare_runtime_environment"),
        (
            "physics.motif.rbfe_system_prepare",
            "backend.motif.rbfe_chemistry_evidence:mapping_direction_audit",
        ),
        (
            "physics.motif.rbfe_aggregate",
            "backend.motif.rbfe:_pair_legs_and_repeats",
        ),
    ),
)
def test_transitive_helper_source_change_moves_source_version(
        method_id: str, helper: str):
    """Positive control: mutate helper source, not the descriptor, and observe drift."""
    _, descriptor = _descriptor(method_id)
    primary_module = _runtime_module(descriptor)
    helper_module, helper_name = mr._resolve_declared_member(primary_module, helper)
    target = getattr(helper_module, helper_name)
    original_getsource = inspect.getsource
    before = _version(method_id)

    def source_with_synthetic_change(value):
        source = original_getsource(value)
        if value is target:
            return source + "\n# synthetic source-version positive control\n"
        return source

    with mock.patch.object(mr.inspect, "getsource",
                           side_effect=source_with_synthetic_change):
        after = _version(method_id)

    assert after[1] != before[1]
    assert after[0] != before[0]
    assert _version(method_id) == before


@pytest.mark.parametrize(
    ("method_id", "constant", "replacement"),
    (
        (
            "physics.motif.openfe_edge",
            "_ANALYSIS_OVERRIDE",
            lambda value: value + "\n# synthetic analysis policy change\n",
        ),
        (
            "physics.motif.rbfe_network",
            "backend.motif.structure_methods:_RBFE_NETWORK_ATTESTATION_KEY",
            lambda value: value + "_synthetic",
        ),
        (
            "physics.motif.rbfe_network",
            "_OPENFE_NETWORK_PLANNER_SHA256",
            lambda _value: "sha256:" + "2" * 64,
        ),
        (
            "physics.motif.rbfe_system_prepare",
            "_SYSTEM_BUILDER_SHA256",
            lambda _value: "sha256:" + "1" * 64,
        ),
        (
            "physics.motif.rbfe_system_prepare",
            "_MAPPING_ATTESTATION_FIELDS",
            lambda value: frozenset((*value, "synthetic_material_field")),
        ),
        (
            "physics.motif.rbfe_aggregate",
            "_CONVERGENCE_POLICY",
            lambda value: {**value, "minimum_neighbor_overlap": 0.031},
        ),
    ),
)
def test_material_constant_change_moves_source_version(
        method_id: str, constant: str, replacement):
    """Positive control for external programs and scientific policy values."""
    _, descriptor = _descriptor(method_id)
    primary_module = _runtime_module(descriptor)
    source_module, member_name = mr._resolve_declared_member(
        primary_module, constant)
    original = getattr(source_module, member_name)
    before = _version(method_id)
    setattr(source_module, member_name, replacement(original))
    try:
        after = _version(method_id)
    finally:
        setattr(source_module, member_name, original)

    assert after[1] != before[1]
    assert after[0] != before[0]
    assert _version(method_id) == before
