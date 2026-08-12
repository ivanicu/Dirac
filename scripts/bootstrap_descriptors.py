#!/usr/bin/env python3
"""Emit the remaining method descriptors, DERIVED from the live registry.

Nine descriptors hand-written from memory would be nine chances to describe a
system that does not exist. `method_registry.UNITS` already carries, per method,
the functions and constants whose values decide numbers, plus the refusal keys —
so the mechanical half is derived from the code that runs, and only the parts the
registry CANNOT know are authored here:

  · execution policy   — UNITS has `exec_class: interactive`, a binary that cannot
                         describe a method costing 0.1 s or 226 s on one code path
  · refusal wording    — UNITS has keys like 'unconverged'; a descriptor needs the
                         CONDITION, the alternative, and the measurement
  · warnings           — scope limits on a SUCCESSFUL result; the registry has no
                         concept of these yet
  · exposure           — which surfaces may see the method

THE THREE PHYSICS METHODS ARE DECLARED BUT NOT REGISTERED. surface.mep,
surface.mep_at and torsion.strain live in a second daemon that has no method
registry at all: no method_id, no version, no descriptor. Per ADR-002 the git
manifest is the authority, so declaring them is correct and creates the pull for
registration — but the descriptor says so explicitly rather than implying a
registration that does not exist.

Run once (idempotent): python3 scripts/bootstrap_descriptors.py
Then:                  python3 scripts/gen_contracts.py
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / 'contracts' / 'methods'
sys.path.insert(0, str(ROOT / 'backend'))

MOLECULE_3D = {
    'type': 'object', 'additionalProperties': False,
    'required': ['kind', 'content', 'dimensionality'],
    'properties': {
        'kind': {'const': 'molfile'},
        'content': {'type': 'string', 'minLength': 40},
        'format': {'enum': ['mdl-v2000', 'mdl-v3000'], 'default': 'mdl-v2000'},
        'dimensionality': {
            'const': 3,
            'description': '3 ONLY. A 2D structure must not reach a 3D physics '
                           'method; molecule.embed is the explicit step that '
                           'produces 3D, and an implicit SMILES→3D would report '
                           'one method having run when two did.'},
        'coordinate_space': {'enum': ['molecular', 'scene'], 'default': 'molecular'},
        'coordinate_units': {'const': 'angstrom', 'default': 'angstrom'},
        'identity': {
            'type': 'object', 'additionalProperties': False,
            'properties': {
                'inchikey': {'type': ['string', 'null']},
                'compound_id': {'type': ['string', 'null']},
                'conformer_hash': {'type': ['string', 'null']}}},
    },
}

QM_BASIS = {
    'type': 'string', 'enum': ['sto-3g', '6-31g', '6-31g*', 'def2-svp'],
    'default': 'sto-3g',
    'description': 'METHOD-SPECIFIC, deliberately. The global Basis union was '
                   'already false: the physics daemon accepts def2-tzvp and the '
                   'fields daemon does not.',
}
PHYSICS_BASIS = dict(QM_BASIS, enum=['sto-3g', '6-31g', '6-31g*', 'def2-svp', 'def2-tzvp'])

QM_REFUSALS = [
    {'code': 'UNSUPPORTED',
     'condition': 'the requested basis does not describe every element present',
     'points_at': 'the same method with basis=def2-svp',
     'measured': '6-31g has no iodine ECP; without one iodine runs all-electron '
                 'and the potential is wrong by 58 kcal/mol with no error raised'},
    {'code': 'OPEN_SHELL_SPIN_REQUIRED',
     'condition': 'a transition metal is present and spin was not supplied',
     'points_at': None,
     'measured': 'electron parity implies a closed-shell singlet that is not the '
                 'ground state; collapsing this into UNSUPPORTED reads as "give up" '
                 'rather than "answer one question"'},
    {'code': 'UNCONVERGED',
     'condition': 'DIIS stalls and the second-order (newton) rescue also fails',
     'points_at': None,
     'measured': 'a field rendered from an unconverged wavefunction is visually '
                 'indistinguishable from a converged one'},
    {'code': 'BUDGET',
     'condition': 'the pre-flight estimate exceeds compute_budget_seconds, or the '
                  'in-loop watchdog fires',
     'points_at': None,
     'measured': 'estimate 2.8 x 5.9e-9 x nao^4.03 s, fitted over 47 molecules; the '
                 '2.8x factor is the measured underestimate mid-range'},
    {'code': 'TOO_LARGE', 'condition': 'more than 120 atoms including hydrogens',
     'points_at': None,
     'measured': 'beyond that, HF on this box is not interactive at any basis'},
]
QM_WARNINGS = [
    {'code': 'MINIMAL_BASIS_FRONTIER_NOT_QUOTABLE',
     'meaning': 'Orbital energies are not quantitatively quotable at this basis.',
     'scope': ['output.wavefunction.homo_ev', 'output.wavefunction.lumo_ev'],
     'measured': 'STO-3G ranks nitrobenzene as more electron-rich than benzene '
                 'while def2-SVP ranks it last; water\'s LUMO moves 12 eV'},
]

QM_EXEC = {
    'supported_modes': ['sync', 'job'], 'default_mode': 'auto',
    'inline_threshold_seconds': 2.0, 'resource_class': 'cpu-qm',
    'concurrency_class': 'scf', 'supports_cancellation': False,
    'deterministic': True, 'cacheable': True, 'side_effects': 'writes_cache',
}
CLASSICAL_EXEC = {
    'supported_modes': ['sync'], 'default_mode': 'sync',
    'inline_threshold_seconds': 5.0, 'resource_class': 'cpu-classical',
    'concurrency_class': 'classical', 'supports_cancellation': False,
    'deterministic': True, 'cacheable': True, 'side_effects': 'writes_cache',
}


def grid_output(kind: str, units: str, extra: dict | None = None) -> dict:
    props = {
        'field': {
            'type': 'object', 'additionalProperties': False,
            'required': ['kind', 'native_units', 'grid', 'extrema'],
            'properties': {
                'kind': {'const': kind},
                'native_units': {'const': units},
                'grid': {'type': 'object', 'additionalProperties': False,
                         'required': ['dimensions'],
                         'properties': {
                             'dimensions': {'type': 'array', 'minItems': 3,
                                            'maxItems': 3,
                                            'items': {'type': 'integer', 'minimum': 2}},
                             'spacing_angstrom': {'type': 'number',
                                                  'exclusiveMinimum': 0}}},
                'extrema': {'type': 'object', 'additionalProperties': False,
                            'required': ['min', 'max'],
                            'properties': {'min': {'type': 'number'},
                                           'max': {'type': 'number'}}},
                'contour_closes_in_box': {'type': 'boolean'},
            }}}
    if extra:
        props.update(extra)
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema',
            'type': 'object', 'additionalProperties': False,
            'required': ['field'], 'properties': props}


WAVEFUNCTION = {
    'type': 'object', 'additionalProperties': False,
    'required': ['converged', 'method', 'basis', 'n_basis_functions'],
    'properties': {
        'converged': {'const': True,
                      'description': 'Only true is representable in a SUCCESS '
                                     'output. An unconverged SCF is a refusal, not '
                                     'a result with a flag — a caller who has to '
                                     'check a boolean will eventually not check it.'},
        'method': {'type': 'string'}, 'basis': {'type': 'string'},
        'n_basis_functions': {'type': 'integer', 'minimum': 1},
        'scf_energy_hartree': {'type': ['number', 'null']},
        'homo_ev': {'type': ['number', 'null']},
        'lumo_ev': {'type': ['number', 'null']},
        'scf_cycles': {'type': ['integer', 'null']},
        'ecp_elements': {'type': 'array', 'items': {'type': 'string'}},
    }}

CUBE_ARTIFACT = [{'role': 'field.cube',
                  'media_type': 'application/vnd.dirac.gaussian-cube',
                  'typical_size_bytes': 1900000}]


def qm_descriptor(mid: str, kind: str, units: str, summary: str, unit: dict) -> dict:
    return {
        'schema_version': '1.0.0', 'method_id': mid, 'summary': summary,
        'implementation': {'module': 'backend.field_server',
                           'functions': sorted(unit['fns']),
                           'constants': sorted(unit.get('consts', ()))},
        'input': {'schema': {
            '$schema': 'https://json-schema.org/draft/2020-12/schema',
            'type': 'object', 'additionalProperties': False,
            'required': ['molecule'],
            'properties': {
                'molecule': MOLECULE_3D,
                'parameters': {
                    'type': 'object', 'additionalProperties': False,
                    'properties': {
                        'basis': QM_BASIS,
                        'spin': {'type': ['integer', 'null'], 'default': None,
                                 'description': 'Unpaired electrons; null means '
                                                'infer from parity, which is '
                                                'REFUSED for a transition metal.'}}}}}},
        'output': {'schema': grid_output(kind, units,
                                        {'wavefunction': WAVEFUNCTION}),
                   'artifacts': CUBE_ARTIFACT},
        'refusals': QM_REFUSALS, 'warnings': QM_WARNINGS,
        'execution': QM_EXEC,
        'exposure': {'sdk': True, 'cli': True, 'mcp': 'curated'},
    }


def main() -> int:
    import method_registry as mr

    OUT.mkdir(parents=True, exist_ok=True)
    written = []

    qm = {
        'fields.qm.lumo': ('lumo', 'amplitude',
                           'Lowest unoccupied molecular orbital amplitude on a grid, '
                           'from a real RHF/UHF wavefunction.'),
        'fields.qm.density': ('density', 'e/Bohr^3',
                              'Total electron density on a grid, from a real RHF/UHF '
                              'wavefunction.'),
        'fields.qm.mep_qm': ('mep_qm', 'Ha/e',
                             'Electrostatic potential from the QM density, rather '
                             'than from point charges.'),
    }
    for mid, (kind, units, summary) in qm.items():
        d = qm_descriptor(mid, kind, units, summary, mr.UNITS[mid])
        p = OUT / f'{mid}.method.json'
        p.write_text(json.dumps(d, indent=2, ensure_ascii=False) + '\n')
        written.append(p.name)

    # ── classical: mep ───────────────────────────────────────────────────────
    mep = mr.UNITS['fields.mep']
    d = {
        'schema_version': '1.0.0', 'method_id': 'fields.mep',
        'summary': 'Classical electrostatic potential from Gasteiger point charges, '
                   'on a grid sized to close its own contour.',
        'implementation': {'module': 'backend.field_server',
                           'functions': sorted(mep['fns']),
                           'constants': sorted(mep.get('consts', ()))},
        'input': {'schema': {
            '$schema': 'https://json-schema.org/draft/2020-12/schema',
            'type': 'object', 'additionalProperties': False,
            'required': ['molecule'],
            'properties': {'molecule': MOLECULE_3D, 'parameters': {
                'type': 'object', 'additionalProperties': False,
                'properties': {
                    'spacing_angstrom': {'type': 'number', 'exclusiveMinimum': 0,
                                         'default': 0.4},
                    'pad_angstrom': {'type': 'number', 'minimum': 0, 'default': 4.0,
                                     'description': 'Starting pad. The box GROWS '
                                                    'from here until the contour '
                                                    'closes, so the value used is '
                                                    'reported and may differ.'}}}}}},
        'output': {'schema': grid_output('mep', 'kcal/mol', {
            'charges': {'const': 'gasteiger'},
            'net_charge': {'type': ['number', 'null']},
            'sigma_hole_representable': {'type': 'boolean'},
        }), 'artifacts': CUBE_ARTIFACT},
        'refusals': [
            {'code': 'UNPARAMETERIZED',
             'condition': 'Gasteiger has no parameters for an element present '
                          '(hypervalent P, several metals)',
             'points_at': 'fields.qm.mep_qm',
             'measured': 'PF6- returns NaN charges; nan_to_num laundered them into a '
                         'flat zero field that was cached and served as a result'},
            {'code': 'UNPARAMETERIZED',
             'condition': 'every charge is zero, so the field carries no information',
             'points_at': 'fields.qm.mep_qm',
             'measured': 'a zero field is silence, not a measurement'}],
        'warnings': [
            {'code': 'SIGMA_HOLE_NOT_REPRESENTABLE',
             'meaning': 'A spherical point charge cannot represent a sigma-hole at '
                        'all, so this map cannot answer a halogen-bonding question.',
             'scope': ['output.field'],
             'measured': 'bromobenzene at the cap: Gasteiger -6.2 kcal/mol vs the QM '
                         'surface route +9.9 — opposite signs, ~16 kcal/mol apart'},
            {'code': 'CLASSICAL_MODEL_SCOPE',
             'meaning': 'Point charges: no lone-pair or sigma-hole anisotropy, and '
                        '~0.4x the QM molecular dipole. A qualitative map, not an '
                        'interaction energy.',
             'scope': ['output.field'], 'measured': '~0.4x the QM dipole'}],
        'execution': CLASSICAL_EXEC,
        'exposure': {'sdk': True, 'cli': True, 'mcp': 'curated'},
    }
    p = OUT / 'fields.mep.method.json'
    p.write_text(json.dumps(d, indent=2, ensure_ascii=False) + '\n')
    written.append(p.name)

    # ── classical: mlp ───────────────────────────────────────────────────────
    mlp = mr.UNITS['fields.mlp']
    d = {
        'schema_version': '1.0.0', 'method_id': 'fields.mlp',
        'summary': 'Molecular lipophilicity potential from Crippen atomic '
                   'contributions with a Fauchere distance kernel.',
        'implementation': {'module': 'backend.field_server',
                           'functions': sorted(mlp['fns']),
                           'constants': sorted(mlp.get('consts', ()))},
        'input': {'schema': {
            '$schema': 'https://json-schema.org/draft/2020-12/schema',
            'type': 'object', 'additionalProperties': False,
            'required': ['molecule'],
            'properties': {'molecule': MOLECULE_3D, 'parameters': {
                'type': 'object', 'additionalProperties': False,
                'properties': {'spacing_angstrom': {'type': 'number',
                                                    'exclusiveMinimum': 0,
                                                    'default': 0.4}}}}}},
        'output': {'schema': grid_output('mlp', 'MLP (Crippen/Fauchere)', {
            'total_logp': {'type': ['number', 'null']},
            'single_signed': {'type': 'boolean'}}),
            'artifacts': CUBE_ARTIFACT},
        'refusals': [
            {'code': 'UNPARAMETERIZED',
             'condition': 'Crippen has no contribution for an atom type present',
             'points_at': None,
             'measured': 'exotic elements have no Crippen parameters at all'}],
        'warnings': [],
        'execution': dict(CLASSICAL_EXEC, cacheable=False,
                          side_effects='none'),
        'exposure': {'sdk': True, 'cli': True, 'mcp': 'curated'},
    }
    p = OUT / 'fields.mlp.method.json'
    p.write_text(json.dumps(d, indent=2, ensure_ascii=False) + '\n')
    written.append(p.name)

    # ── molecule.embed ───────────────────────────────────────────────────────
    d = {
        'schema_version': '1.0.0', 'method_id': 'molecule.embed',
        'summary': 'SMILES or a 2D molfile to an ETKDG-embedded 3D conformer, '
                   'MMFF-optimised where MMFF can type it.',
        'description': 'The EXPLICIT step between a 2D input and any 3D physics '
                       'method. It exists as its own method so that a field '
                       'invocation never silently embeds: two methods ran, and the '
                       'provenance says so.',
        'implementation': {'module': 'backend.field_server',
                           'functions': ['embed_molecule'], 'constants': []},
        'input': {'schema': {
            '$schema': 'https://json-schema.org/draft/2020-12/schema',
            'type': 'object', 'additionalProperties': False,
            'properties': {
                'smiles': {'type': 'string', 'minLength': 1},
                'molfile': {'type': 'string', 'minLength': 40},
                'parameters': {'type': 'object', 'additionalProperties': False,
                               'properties': {
                                   'seed': {'type': 'integer', 'default': 42,
                                            'description': 'Determinism is part of '
                                                           'the contract: same '
                                                           'input and seed, same '
                                                           'coordinates.'},
                                   'optimize': {'type': 'boolean', 'default': True}}}},
            'oneOf': [{'required': ['smiles']}, {'required': ['molfile']}]}},
        'output': {'schema': {
            '$schema': 'https://json-schema.org/draft/2020-12/schema',
            'type': 'object', 'additionalProperties': False,
            'required': ['molecule'],
            'properties': {
                'molecule': MOLECULE_3D,
                'embedding': {'type': 'object', 'additionalProperties': False,
                              'properties': {
                                  'method': {'const': 'ETKDGv3'},
                                  'mmff_optimized': {'type': 'boolean'},
                                  'seed': {'type': 'integer'},
                                  'n_atoms_heavy': {'type': 'integer'}}}}},
            'artifacts': [{'role': 'molecule.molfile',
                           'media_type': 'chemical/x-mdl-molfile',
                           'typical_size_bytes': 4000, 'optional': True}]},
        'refusals': [
            {'code': 'PARSE', 'condition': 'the input cannot be read as a molecule',
             'points_at': None, 'measured': 'no retry on the same bytes can succeed'},
            {'code': 'UNPARAMETERIZED',
             'condition': 'MMFF cannot type the molecule and optimize was requested',
             'points_at': 'molecule.embed with optimize=false',
             'measured': 'MMFF cannot type PF6-, boronic acids or selenomethionine'}],
        'warnings': [],
        'execution': dict(CLASSICAL_EXEC, cacheable=False, side_effects='none',
                          resource_class='cpu-cheminformatics',
                          concurrency_class='classical'),
        'exposure': {'sdk': True, 'cli': True, 'mcp': 'curated'},
    }
    p = OUT / 'molecule.embed.method.json'
    p.write_text(json.dumps(d, indent=2, ensure_ascii=False) + '\n')
    written.append(p.name)

    # ── the three physics methods: DECLARED, NOT REGISTERED ──────────────────
    physics = {
        'surface.mep': ('Electrostatic potential on the molecular surface, from a '
                        'QM density — the route that can answer a sigma-hole '
                        'question.', 'surface.points'),
        'surface.mep_at': ('Potential evaluated at caller-supplied coordinates, so '
                           'the client can colour its own surface mesh.',
                           'surface.values'),
        'torsion.strain': ('Relaxed torsion scan around a rotatable bond: where the '
                           'given pose sits on the rotor energy curve.',
                           'torsion.profile'),
    }
    for mid, (summary, role) in physics.items():
        d = {
            'schema_version': '1.0.0', 'method_id': mid, 'summary': summary,
            'description': 'DECLARED HERE, NOT YET REGISTERED AT RUNTIME. This '
                           'method lives in the physics daemon, which has no method '
                           'registry: no method_id, no implementation version, no '
                           'descriptor, and an in-process job queue that does not '
                           'survive a restart. ADR-002 makes this manifest the '
                           'authority, so declaring it is correct — and saying that '
                           'it is unregistered is the difference between a plan and '
                           'a claim.',
            'implementation': {'module': 'backend.physics.server',
                               'functions': ['compute_surface_mep']
                               if mid != 'torsion.strain' else ['torsion_profile'],
                               'constants': []},
            'input': {'schema': {
                '$schema': 'https://json-schema.org/draft/2020-12/schema',
                'type': 'object', 'additionalProperties': False,
                'required': ['molecule'],
                'properties': {'molecule': MOLECULE_3D, 'parameters': {
                    'type': 'object', 'additionalProperties': False,
                    'properties': {'basis': PHYSICS_BASIS}}}}},
            'output': {'schema': {
                '$schema': 'https://json-schema.org/draft/2020-12/schema',
                'type': 'object', 'additionalProperties': False,
                'properties': {'summary': {'type': 'object'}}},
                'artifacts': [{'role': role,
                               'media_type': 'application/vnd.dirac.float32-xyz'
                               if role != 'torsion.profile'
                               else 'application/vnd.dirac.torsion-profile+json',
                               'typical_size_bytes': 400000}]},
            'refusals': QM_REFUSALS,
            'warnings': QM_WARNINGS,
            'execution': dict(QM_EXEC, default_mode='job'),
            'exposure': {'sdk': True, 'cli': True,
                         'mcp': 'curated' if mid != 'surface.mep_at' else 'generic-only'},
        }
        p = OUT / f'{mid}.method.json'
        p.write_text(json.dumps(d, indent=2, ensure_ascii=False) + '\n')
        written.append(p.name)

    print(f'{len(written)} descriptor(s) written: ' + ', '.join(sorted(written)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
