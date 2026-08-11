#!/usr/bin/env python3
"""Physics contracts for the Dirac fields backend — the positive-control suite.

`backend/field_server.py` owns pyscf, the ECP path, the Gasteiger path, the cube
writer and the wall-clock deadline, and until this file existed it contained
ZERO assertions (`grep -c "assert " backend/field_server.py` == 0). Splitting it
into a library without these tests would be negligent: every check below is the
scar of a defect that SHIPPED, and every one of those defects passed the
backend's own honesty gates while it was wrong.

The four incidents, and the tests that would have caught them:

  1. Iodine ran ALL-ELECTRON under def2-svp, because pyscf does not auto-attach
     the ECP that def2 requires from Rb (Z=37) up. SCF converged, charge
     balanced, the far field decayed to zero — and the sigma-hole came out
     58 kcal/mol wrong WITH THE WRONG SIGN. Fixed by `ecp_for()`.
     -> test_ecp_attached_for_iodine_under_def2svp
     -> test_ecp_absent_for_bromine_under_def2svp
     -> test_sto3g_defines_no_ecp_for_iodine_which_is_why_it_must_not_be_used
     -> test_an_ecp_claim_must_mean_core_electrons_were_replaced   [FINDING]
     -> test_only_def2svp_covers_iodine_among_the_allowed_bases
     -> test_an_uncovered_element_is_refused_as_chemistry_...       [FINDING]
     -> test_iodobenzene_homo_matches_the_experimental_ionisation_potential

  2. That fix landed on ONE of TWO paths, because the HTTP layer kept its own
     copy of the basis default (commit 07f703b). Two homes for one constant.
     -> test_basis_default_has_exactly_one_home
     -> test_positive_control_the_basis_literal_scanner_can_fire
     -> test_allowed_basis_equals_the_db_check_set
     -> test_positive_control_the_db_check_parser_can_fire

  3. PF6- produced a uniformly ZERO classical MEP and shipped as a normal
     result (Gasteiger yields NaN on hypervalent P; nan_to_num laundered
     silence into a field).
     -> test_pf6_classical_mep_refuses_and_names_the_elements
     -> test_positive_control_benzene_classical_mep_has_resolution

  4. A 43-heavy-atom molecule (HEM) held 22 cores for 36 minutes. HF is
     O(nao^4) per ITERATION and the iteration count is unbounded, so an atom
     cap cannot bound the clock. Now a deadline checked INSIDE the SCF loop.
     -> test_deadline_fires_from_inside_the_scf_loop
     -> test_http_path_clamps_non_finite_max_seconds
     -> test_run_scf_cannot_be_disabled_by_a_non_finite_budget   [FINDING]

Two verdict kinds beyond PASS/FAIL, both deliberate:

  FINDING (xfail) — a CONTRACT the code currently violates. The test is written
  against the property, not against the behaviour, and is marked
  `@known_defect`. It is loud, it does not fail the suite, and it flips to a
  HARD FAILURE the day someone fixes the code without deleting the marker
  (strict xfail). This is the opposite of weakening a test until it passes.

  SKIP — with a stated reason, printed. Never silent.

Run either way:
    backend/env/bin/python backend/tests/test_physics_contracts.py
    backend/env/bin/pytest backend/tests/test_physics_contracts.py    # if present

Environment switches:
    DIRAC_TESTS_SKIP_SLOW=1   skip the two real-SCF tests (~25 s together)
"""
from __future__ import annotations

import ast
import json
import math
import os
import pathlib
import re
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request

_HERE = pathlib.Path(__file__).resolve().parent
_BACKEND = _HERE.parent
_REPO = _BACKEND.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import numpy as np                                        # noqa: E402
import field_server as fs                                 # noqa: E402

MIGRATIONS = _REPO / 'backend' / 'db' / 'migrations'

# Iodobenzene, first vertical ionisation potential, gas phase photoelectron
# spectroscopy: 8.685-8.72 eV depending on the compilation (NIST WebBook lists
# 8.72; Koopmans' theorem equates -HOMO with it). This is the only number in
# this file that comes from OUTSIDE our own chain, which makes it the strongest
# check here: nothing we compute, cache, write or report can bend it.
IODOBENZENE_IP_EV = 8.7
IODOBENZENE_IP_TOLERANCE_EV = 1.5     # Koopmans-level agreement at HF/def2-svp

SKIP_SLOW = os.environ.get('DIRAC_TESTS_SKIP_SLOW') == '1'


# ── dual-mode plumbing (pytest is NOT installed in backend/env) ─────────────

try:
    import pytest
    _HAVE_PYTEST = True
except ImportError:                                       # the normal case here
    pytest = None
    _HAVE_PYTEST = False


class Skipped(Exception):
    """Raised by skip(); the standalone runner reports it as SKIP + reason."""


def skip(reason: str):
    if _HAVE_PYTEST:
        pytest.skip(reason)
    raise Skipped(reason)


def known_defect(reason: str):
    """Mark a test that asserts a CONTRACT the code currently violates.

    Under pytest this is a STRICT xfail: if the test starts passing, pytest
    fails it, forcing whoever fixed the code to come here and delete the
    marker. The standalone runner does the same. A known defect that quietly
    heals is a lesson nobody learns.
    """
    def deco(fn):
        fn.__known_defect__ = reason
        if _HAVE_PYTEST:
            return pytest.mark.xfail(strict=True, reason=reason)(fn)
        return fn
    return deco


# ── shared fixtures, memoised: embedding is cheap but not free ──────────────

_MOL_CACHE: dict[str, tuple[str, dict]] = {}


def embedded(smiles: str) -> tuple[str, dict]:
    """(molblock, meta) for a SMILES, through the real /embed code path."""
    if smiles not in _MOL_CACHE:
        _MOL_CACHE[smiles] = fs.embed_molecule(smiles, None)
    return _MOL_CACHE[smiles]


def prepared(smiles: str):
    molblock, _ = embedded(smiles)
    return fs.prepare_mol(molblock)


def _source_and_tree() -> tuple[str, ast.Module]:
    src = pathlib.Path(fs.__file__).read_text()
    return src, ast.parse(src)


# ════════════════════════════════════════════════════════════════════════════
# INCIDENT 1 — the ECP that pyscf will not attach for you
# ════════════════════════════════════════════════════════════════════════════

def test_module_imports_without_a_database():
    """The library must be importable with no Postgres and no side effects.

    `db_init()` is called from `__main__` only. If a refactor moves it to
    import time, every test in this file becomes a test of Ivan's database
    instead of the physics, and the suite starts passing or failing for
    reasons that have nothing to do with the backend.
    """
    assert fs._db_ok is False, 'importing field_server opened a DB connection'
    assert fs._producer_id is None, 'importing field_server registered a producer'
    assert callable(fs.ecp_for) and callable(fs.run_scf) and callable(fs.field_mep)


def test_ecp_attached_for_iodine_under_def2svp():
    """The exact call that fixes incident 1.

    def2 bases replace the core from Rb (Z=37) up, and pyscf will NOT do it for
    you: `gto.M(basis='def2-svp')` with no `ecp=` builds iodine all-electron and
    converges happily. Verified here against pyscf itself, not against a memory
    of pyscf.
    """
    from pyscf import gto

    ecp = fs.ecp_for(['I', 'C', 'H'], 'def2-svp')
    assert 'I' in ecp, f'no ECP attached for iodine under def2-svp: {ecp!r}'
    assert ecp['I'] == 'def2-svp', f'ECP for I points at {ecp["I"]!r}, not the basis'
    assert 'C' not in ecp and 'H' not in ecp, f'ECP attached to a light atom: {ecp!r}'

    # ...and the attachment is load-bearing, not cosmetic: 28 core electrons go.
    with_ecp = gto.M(atom='I 0 0 0; H 0 0 1.6', basis='def2-svp',
                     ecp={'I': 'def2-svp'}, verbose=0)
    without = gto.M(atom='I 0 0 0; H 0 0 1.6', basis='def2-svp',
                    ecp=None, verbose=0)
    assert without.nelectron == 54, f'I+H should be 54 e-, got {without.nelectron}'
    assert with_ecp.nelectron == 26, (
        f'the def2-svp ECP should replace 28 core electrons (54 -> 26); '
        f'got {with_ecp.nelectron}')
    assert int(with_ecp.atom_charges()[0]) == 25, (
        'the iodine effective charge should be 53 - 28 = 25')


def test_ecp_absent_for_bromine_under_def2svp():
    """def2-svp defines NO ECP for bromine, so none may be attached.

    Verified against pyscf rather than taken on trust: `load_ecp('def2-svp','Br')`
    returns an EMPTY list. Note the mechanism, because it is not the one the
    docstring in field_server.py claims — Br is Z=35, below ECP_FROM_Z=37, so it
    never reaches `load_ecp` at all. The right answer for a reason that is one
    element wide: Se (Z=34) is also below the cut, but Rb (Z=37) and everything
    above it goes through the try/except, which cannot fail. See
    test_an_ecp_claim_must_mean_core_electrons_were_replaced.
    """
    from pyscf import gto
    from rdkit import Chem

    assert gto.basis.load_ecp('def2-svp', 'Br') == [], (
        'pyscf now defines a def2-svp ECP for Br — this test encoded the '
        'opposite fact about the world and must be re-derived, not deleted')
    ecp = fs.ecp_for(['Br', 'C'], 'def2-svp')
    assert 'Br' not in ecp, f'an ECP was invented for bromine: {ecp!r}'
    assert ecp == {}, f'expected no ECP at all for CBr under def2-svp, got {ecp!r}'

    pt = Chem.GetPeriodicTable()
    assert pt.GetAtomicNumber('Br') < fs.ECP_FROM_Z <= pt.GetAtomicNumber('Rb'), (
        f'ECP_FROM_Z={fs.ECP_FROM_Z} no longer sits between Br and Rb; the '
        f'def2 series starts replacing cores at Rb')


def test_sto3g_defines_no_ecp_for_iodine_which_is_why_it_must_not_be_used():
    """The pairing, asserted as one fact: sto-3g has no ECP for iodine, and
    therefore an iodine calculation under sto-3g is ALL-ELECTRON — the exact
    regime that produced a sigma-hole 58 kcal/mol wrong with the wrong sign.

    sto-3g is `DEFAULT_BASIS`, so this is not a hypothetical: a /field request
    that omits `basis` lands here. The refusal this test wants does not exist
    yet (there is no basis-vs-element guard in the backend) — what IS asserted
    is that the physics claim is true and visible, so the next person cannot
    read "sto-3g is fine, ecp_for handles iodine" out of the code.
    """
    from pyscf import gto
    from rdkit import Chem

    pt = Chem.GetPeriodicTable()
    assert gto.basis.load_ecp('sto-3g', 'I') == [], (
        'sto-3g now carries an ECP for iodine — re-derive this test')
    all_electron = gto.M(atom='I 0 0 0; H 0 0 1.6', basis='sto-3g',
                         ecp=None, verbose=0)
    z_total = pt.GetAtomicNumber('I') + pt.GetAtomicNumber('H')
    assert all_electron.nelectron == z_total == 54, (
        f'sto-3g iodine must be all-electron ({z_total} e-), '
        f'got {all_electron.nelectron}')
    assert fs.DEFAULT_BASIS == 'sto-3g', (
        f'DEFAULT_BASIS moved to {fs.DEFAULT_BASIS!r}; if it is now a def2 '
        f'basis this test is obsolete in the good way — re-derive it')
    assert 'def2-svp' in fs.ALLOWED_BASIS, (
        'the basis that DOES carry an iodine ECP must remain reachable, or '
        'heavy halogens have no correct path at all')


# FIXED 2026-08-11 (this test is why). Was: ecp_for() detects a missing ECP with try/except, but pyscf's load_ecp  RETURNS AN EMPTY LIST instead of raising -- so the except branch can never  fir...
# The marker is removed because the defect is gone; the test stays as the
# regression witness. A @known_defect that starts passing FAILS the suite,
# which is how this file told me the fixes had landed.
def test_an_ecp_claim_must_mean_core_electrons_were_replaced():
    """CONTRACT: if `ecp_for` names an element, that element's core really is
    replaced in the Mole that gets built. Written against the property, so it
    holds for any future basis added to ALLOWED_BASIS.
    """
    from pyscf import gto
    from rdkit import Chem

    pt = Chem.GetPeriodicTable()
    z_total = pt.GetAtomicNumber('I') + pt.GetAtomicNumber('H')
    violations = []
    for basis in fs.ALLOWED_BASIS:
        ecp = fs.ecp_for(['I'], basis)
        if 'I' not in ecp:
            continue
        try:
            mol = gto.M(atom='I 0 0 0; H 0 0 1.6', basis=basis,
                        ecp={'I': ecp['I']}, verbose=0)
        except Exception as e:                                  # noqa: BLE001
            violations.append(
                f'{basis}: ecp_for claims {ecp!r} but the molecule cannot even '
                f'be built ({type(e).__name__}: {str(e).splitlines()[0]})')
            continue
        if mol.nelectron >= z_total:
            violations.append(
                f'{basis}: ecp_for claims {ecp!r} but the molecule still has '
                f'all {mol.nelectron} electrons')
    assert not violations, (
        'an ECP was claimed without any core being replaced:\n  '
        + '\n  '.join(violations))


def test_only_def2svp_covers_iodine_among_the_allowed_bases():
    """Which (basis, element) pairs exist at all — an EXTERNAL fact, read out
    of pyscf, not out of our code.

    The whitelist validates the basis NAME; nothing validates the PAIR. Measured
    2026-08-11: 6-31g defines neither I nor Br, 6-31g* defines Br but not I, and
    def2-svp is the only allowed basis that covers iodine with its ECP. sto-3g
    covers iodine but ALL-ELECTRON, which is incident 1 itself. So for a heavy
    halogen there is exactly one correct choice on the whitelist, and a chemist
    picking any other gets either a wrong number or a crash.
    """
    from pyscf import gto

    def covered(basis: str, el: str) -> bool:
        try:
            gto.basis.load(basis, el)
            return True
        except Exception:                                       # noqa: BLE001
            return False

    assert covered('def2-svp', 'I'), 'def2-svp no longer covers iodine'
    assert bool(gto.basis.load_ecp('def2-svp', 'I')), 'def2-svp lost its iodine ECP'
    assert covered('sto-3g', 'I') and not gto.basis.load_ecp('sto-3g', 'I'), (
        'sto-3g must still be the all-electron iodine trap this suite warns about')
    assert not covered('6-31g', 'I'), '6-31g now covers iodine — re-derive'
    assert not covered('6-31g*', 'I'), '6-31g* now covers iodine — re-derive'
    # the positive control for `covered`: it must be able to return True
    assert covered('6-31g', 'C') and covered('sto-3g', 'C'), 'the probe is blind'
    with_ecp = [b for b in fs.ALLOWED_BASIS
                if covered(b, 'I') and gto.basis.load_ecp(b, 'I')]
    assert with_ecp == ['def2-svp'], (
        f'the set of allowed bases that handle iodine correctly changed: '
        f'{with_ecp} — the UI must offer at least one, and only these are safe')


# FIXED 2026-08-11 (this test is why). Was: A (basis, element) pair the basis does not define is refused as an  INTERNAL error, not as a chemistry problem. pyscf raises  BasisNotFoundError, whos...
# The marker is removed because the defect is gone; the test stays as the
# regression witness. A @known_defect that starts passing FAILS the suite,
# which is how this file told me the fixes had landed.
def test_an_uncovered_element_is_refused_as_chemistry_not_as_an_internal_error():
    """CONTRACT: 'this basis does not describe this element' is a chemistry
    refusal (reason='unsupported'), never an internal error. Cheap — it fails
    while building the Mole, before any SCF runs."""
    src, _ = _source_and_tree()
    assert "'unsupported' if isinstance(e, ValueError) else 'internal'" in src, (
        'the handler no longer types refusals by ValueError; re-derive this '
        'test against the new mapping')
    mol = prepared('c1ccccc1I')
    try:
        fs.run_scf(mol, '6-31g', max_seconds=60.0)
    except Exception as e:                                      # noqa: BLE001
        assert isinstance(e, ValueError), (
            f'6-31g on iodobenzene raised {type(e).__name__} '
            f'({" -> ".join(c.__name__ for c in type(e).__mro__[:3])}), so the '
            f"HTTP layer reports reason='internal' for a chemistry problem: "
            f'{str(e).splitlines()[0]}')
        assert 'I' in str(e) and '6-31g' in str(e)
        return
    raise AssertionError('6-31g on iodobenzene did not refuse at all')


def test_iodobenzene_homo_matches_the_experimental_ionisation_potential():
    """End-to-end, and the only check anchored OUTSIDE our own chain.

    Embed -> ECP -> RHF/def2-svp -> cube. Koopmans: -HOMO should land near the
    measured first vertical IP of iodobenzene, 8.7 eV. If the ECP silently came
    off, the iodine core reappears, the whole valence spectrum shifts and this
    number moves by far more than the tolerance — which is precisely the
    failure that shipped, and precisely what no internal consistency gate
    (converged / charge balanced / far field -> 0) could see.
    """
    if SKIP_SLOW:
        skip('DIRAC_TESTS_SKIP_SLOW=1 (this test runs a real def2-svp SCF, ~19 s)')

    t0 = time.time()
    mol = prepared('c1ccccc1I')
    cube, meta = fs.field_quantum(mol, 'homo', 'def2-svp', max_seconds=300.0)
    elapsed = time.time() - t0

    assert meta['ecp'] == ['I'], (
        f'the iodine ECP is not in the reported meta: ecp={meta["ecp"]!r} — '
        f'this is the 58 kcal/mol wrong-sign regime')
    assert meta['converged'] is True
    assert meta['basis'] == 'def2-svp' and meta['method'] == 'RHF'
    assert meta['natoms'] == 12, f'iodobenzene is C6H5I = 12 atoms, got {meta["natoms"]}'
    # 135 basis functions with the ECP; ~180 without it. A drifting count is the
    # cheapest tell that the ECP came off, independent of any energy.
    assert 120 <= meta['nbasis'] <= 150, (
        f'nbasis={meta["nbasis"]} is outside the def2-svp+ECP range for '
        f'iodobenzene — an ECP change or a basis change')

    homo_ev = meta['homo_ev']
    assert homo_ev < 0, f'an occupied orbital cannot have energy {homo_ev} eV'
    ip_koopmans = -homo_ev
    err = abs(ip_koopmans - IODOBENZENE_IP_EV)
    assert err <= IODOBENZENE_IP_TOLERANCE_EV, (
        f'Koopmans IP {ip_koopmans:.2f} eV vs experiment {IODOBENZENE_IP_EV} eV '
        f'-> off by {err:.2f} eV (tolerance {IODOBENZENE_IP_TOLERANCE_EV})')
    assert meta['lumo_ev'] is not None and meta['lumo_ev'] > homo_ev
    assert cube.count('\n') > 100 and 'nan' not in cube.lower(), 'malformed cube'
    assert elapsed < 120.0, f'took {elapsed:.0f} s — no longer an interactive path'


# ════════════════════════════════════════════════════════════════════════════
# INCIDENT 2 — one constant, two homes; and the Python/SQL join
# ════════════════════════════════════════════════════════════════════════════

# A basis name may appear in a module-level constant whose NAME declares that it
# is classifying bases — MINIMAL_BASES is the live example: "which bases are too
# small for a frontier energy to be quotable" is a DIFFERENT FACT from "the
# default" and "the whitelist", and a different fact is entitled to its own home.
#
# The scanner convicted MINIMAL_BASES the hour it was added, which is the right
# instinct applied to the wrong property: its rule was "the string 'sto-3g'
# appears once", and the property is "the DEFAULT and the ALLOWED SET have one
# home each". Narrowing by NAME rather than by line keeps the conviction power
# where it belongs — a literal inside a FUNCTION BODY is still a stray, and that
# is where the original incident lived (the HTTP layer's own 'sto-3g' default,
# 07f703b).
_BASIS_CONSTANT_HOMES = ('DEFAULT_BASIS', 'ALLOWED_BASIS')


def _basis_literal_homes(tree: ast.Module) -> set[int]:
    """Line numbers ALLOWED to contain a basis-name literal: the DEFAULT_BASIS
    assignment, the ALLOWED_BASIS whitelist, and any module-level constant whose
    name ends in _BASES (a declared classification of bases). Everything else —
    every literal in every function body — is a second home."""
    allowed: set[int] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        names = {t.id for t in node.targets if isinstance(t, ast.Name)}
        classifies = any(n.endswith('_BASES') for n in names)
        if names & set(_BASIS_CONSTANT_HOMES) or classifies:
            allowed.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return allowed


def _stray_basis_literals(tree: ast.Module, names: set[str]) -> list[tuple[int, str]]:
    allowed = _basis_literal_homes(tree)
    hits = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and node.value in names and node.lineno not in allowed):
            hits.append((node.lineno, node.value))
    return sorted(hits)


def test_basis_default_has_exactly_one_home():
    """Incident 2 as a regression guard.

    The ECP fix landed in the physics path while the HTTP layer kept its own
    literal `'sto-3g'` default, so /field with no `basis` skipped the fix
    entirely (07f703b). Asserted three ways, over the AST rather than over a
    grep, so comments and docstrings cannot fake a pass:
      a) DEFAULT_BASIS is a single module-level string assignment,
      b) the request path resolves its default through the NAME DEFAULT_BASIS,
         never through a literal,
      c) no basis-name literal appears anywhere else in the file.
    """
    src, tree = _source_and_tree()

    defaults = [n for n in tree.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == 'DEFAULT_BASIS'
                        for t in n.targets)]
    assert len(defaults) == 1, f'DEFAULT_BASIS assigned {len(defaults)} times'
    assert isinstance(defaults[0].value, ast.Constant), 'DEFAULT_BASIS is not a literal'
    assert defaults[0].value.value == fs.DEFAULT_BASIS

    # (b) every `.get('basis', X)` in the file must default through the name.
    getters = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'get' and len(node.args) == 2
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == 'basis'):
            getters.append(node)
    assert getters, "no `.get('basis', ...)` found — the request path moved; " \
                    "re-derive this test against the new shape"
    for node in getters:
        fallback = node.args[1]
        assert isinstance(fallback, ast.Name), (
            f'line {node.lineno}: the request-handling basis default is a '
            f'{type(fallback).__name__}, not the name DEFAULT_BASIS — this is '
            f'incident 2 recurring')
        assert fallback.id == 'DEFAULT_BASIS', (
            f'line {node.lineno}: defaults through {fallback.id!r}')
        assert getattr(fs, fallback.id) == fs.DEFAULT_BASIS

    # (c) the module-level default and the request default resolve equal.
    assert fs.DEFAULT_BASIS in fs.ALLOWED_BASIS, (
        f'DEFAULT_BASIS={fs.DEFAULT_BASIS!r} is not in ALLOWED_BASIS — the '
        f'default request would be refused by the whitelist')

    strays = _stray_basis_literals(tree, set(fs.ALLOWED_BASIS))
    assert not strays, (
        'basis-name literals outside DEFAULT_BASIS/ALLOWED_BASIS (a second '
        'home for the constant): ' + ', '.join(f'line {ln}: {v!r}' for ln, v in strays))


def test_positive_control_the_basis_literal_scanner_can_fire():
    """A `0 found` from an instrument that has never returned non-zero is
    silence, not an acquittal. Plant a second home and require detection."""
    src, _ = _source_and_tree()
    # Planted INSIDE A FUNCTION, because a module-level constant named *_BASES
    # is now a legitimate home and planting one there would test the exemption
    # rather than the scanner. A literal buried in a function body is exactly the
    # shape of the original incident.
    planted_src = src + ("\ndef _planted_second_home():\n"
                         "    return '6-31g'\n")
    planted_tree = ast.parse(planted_src)
    hits = _stray_basis_literals(planted_tree, set(fs.ALLOWED_BASIS))
    assert hits, 'the scanner did not see a planted second home — it is blind'
    assert any(v == '6-31g' for _, v in hits)
    # and it must not fire on the real file for the same reason (see the test above)
    assert not _stray_basis_literals(ast.parse(src), set(fs.ALLOWED_BASIS))


def _db_basis_check_set(sql: str) -> set[str]:
    """Extract the set from `... basis IN ('a','b',...)`. Returns the LAST
    occurrence in the text — a later migration overrides an earlier one."""
    matches = re.findall(r"basis\s+IN\s*\(([^)]*)\)", sql, re.IGNORECASE | re.DOTALL)
    if not matches:
        return set()
    return set(re.findall(r"'([^']*)'", matches[-1]))


def _db_basis_check_from_migrations() -> tuple[set[str], pathlib.Path]:
    found: tuple[set[str], pathlib.Path] | None = None
    for path in sorted(MIGRATIONS.glob('*.sql')):
        s = _db_basis_check_set(path.read_text())
        if s:
            found = (s, path)          # later migration wins
    if found is None:
        raise AssertionError(f'no basis CHECK found in any migration under {MIGRATIONS}')
    return found


def test_allowed_basis_equals_the_db_check_set():
    """The Python whitelist and the SQL CHECK are one fact in two languages.

    They have drifted before. A basis the backend accepts but the schema
    rejects means the field computes, the blob is written, and the field_cube
    row is refused — an orphan blob and a request that paid for nothing. The
    join is asserted against the MIGRATION CHAIN (the source of truth in git),
    not against a live database, so the gate runs with Postgres down.
    """
    db_set, path = _db_basis_check_from_migrations()
    assert db_set, f'parsed an empty basis set from {path}'
    assert 'none' in db_set, (
        f"the classical-MEP key 'none' is missing from the DB CHECK in "
        f"{path.name}: {sorted(db_set)}")
    assert set(fs.ALLOWED_BASIS) == db_set - {'none'}, (
        f'ALLOWED_BASIS={sorted(fs.ALLOWED_BASIS)} != DB CHECK minus none='
        f'{sorted(db_set - {"none"})}  (from {path.name})')
    assert 'none' not in fs.ALLOWED_BASIS, (
        "'none' is the classical cache key, never a requestable basis")
    assert len(fs.ALLOWED_BASIS) == len(set(fs.ALLOWED_BASIS)), 'duplicate in ALLOWED_BASIS'

    # The 'none' mapping itself has one home, in the request path. If it moves,
    # a classical mep row starts violating the schema's classical/quantum pairing.
    src, _ = _source_and_tree()
    assert "'none' if kind == 'mep'" in src, (
        "the classical basis_key mapping (mep -> 'none') is no longer written "
        "as `'none' if kind == 'mep'`; re-derive this assertion against the "
        'new shape rather than deleting it')


def test_positive_control_the_db_check_parser_can_fire():
    """The parser must distinguish 'found nothing' from 'found something'."""
    assert _db_basis_check_set(
        "basis text NOT NULL CHECK (basis IN ('aaa','bbb','none')),") == {
        'aaa', 'bbb', 'none'}
    assert _db_basis_check_set('CREATE TABLE t (x int);') == set()
    # a later definition must override an earlier one
    assert _db_basis_check_set(
        "basis IN ('old') ... ADD CONSTRAINT c CHECK (basis IN ('new','none'))"
    ) == {'new', 'none'}
    real, path = _db_basis_check_from_migrations()
    assert len(real) >= 2, f'suspiciously small real parse from {path}: {real}'


# ════════════════════════════════════════════════════════════════════════════
# INCIDENT 3 — a zero field is silence, not a measurement
# ════════════════════════════════════════════════════════════════════════════

def test_pf6_classical_mep_refuses_and_names_the_elements():
    """PF6- must RAISE, name what cannot be parameterized, and return no field.

    Gasteiger yields NaN on hypervalent phosphorus. `nan_to_num` turned that
    into a uniformly zero potential well that rendered as a normal result: a
    picture of nothing, indistinguishable from a molecule with no electrostatics.
    """
    mol = prepared('F[P-](F)(F)(F)(F)F')
    try:
        cube, meta = fs.field_mep(mol)
    except ValueError as e:
        msg = str(e)
        assert 'P' in re.findall(r'[A-Z][a-z]?', msg), (
            f'the refusal does not name the offending element: {msg!r}')
        assert 'Gasteiger' in msg, f'the refusal does not name the method: {msg!r}'
        assert 'mep_qm' in msg, (
            f'the refusal does not point at the path that works: {msg!r}')
        return
    raise AssertionError(
        f'PF6- returned a field instead of refusing: vmin={meta["vmin"]} '
        f'vmax={meta["vmax"]} — if these are zero this is the shipped defect '
        f'itself, and if they are not, Gasteiger is now parameterized for '
        f'hypervalent P and this test must be re-derived')


def test_positive_control_benzene_classical_mep_has_resolution():
    """An instrument that only ever refuses has no resolution.

    The refusal above is only evidence if the same code path produces a real
    field on a molecule it should handle. Benzene: negative above the ring
    faces, positive at the hydrogens.
    """
    mol = prepared('c1ccccc1')
    cube, meta = fs.field_mep(mol)
    assert meta['vmin'] < -1.0, f'no negative potential at all: vmin={meta["vmin"]}'
    assert meta['vmax'] > 1.0, f'no positive potential at all: vmax={meta["vmax"]}'
    assert meta['vmax'] - meta['vmin'] > 10.0, 'the field is flat — a zero-field tell'
    assert meta['net_charge'] == 0, f'benzene is neutral, got {meta["net_charge"]}'
    assert meta['units'] == 'kcal/mol' and meta['charges'] == 'gasteiger'
    # `iso_suggest` (a percentile of |field|) was replaced by a FIXED isovalue
    # plus the value the box was sized for: a suggestion recomputed per molecule
    # made two molecules incomparable, which is the opposite of what a contour is
    # for. The invariant survives the rename — whatever isovalue is drawn must lie
    # inside the field's own range, or the surface is empty or clipped — so it is
    # asserted on the key that exists rather than deleted with the old name.
    iso = float(meta.get('iso_sized_for') or meta['iso_fixed'])
    assert 0 < iso < abs(meta['vmin']), (
        f'iso={iso} is not inside the field range (vmin={meta["vmin"]}) — the '
        f'surface would be empty or clipped')
    assert 'nan' not in cube.lower(), 'NaN reached the cube'


def test_the_cube_writer_states_the_geometry_it_was_given():
    """The cube writer converts A -> Bohr; a unit inversion is silent.

    Dimensions agreeing is not units agreeing. Parse the cube back and check
    the header against the molecule and the meta: atom count, grid dims, voxel
    count, and the origin in Bohr times BOHR back to the padded bounding box.
    """
    mol = prepared('c1ccccc1')
    cube, meta = fs.field_mep(mol)
    syms, coords = fs.mol_atoms(mol)
    lines = cube.split('\n')

    natoms = int(lines[2].split()[0])
    assert natoms == mol.GetNumAtoms() == len(syms), (
        f'cube declares {natoms} atoms, molecule has {mol.GetNumAtoms()}')

    origin_bohr = np.array([float(x) for x in lines[2].split()[1:4]])
    # Read the pad ACTUALLY USED, not the requested default. field_mep grows the
    # box until the contour closes, so the two differ whenever growth happened —
    # benzene now lands at 8.0 A after two PAD_STEPs, and hardcoding 4.0 turned a
    # units invariant into a test of the padding policy. The invariant being
    # checked here is "origin (Bohr) x BOHR == the box corner", which must hold at
    # ANY pad; the moment it also asserts WHICH pad, every tuning pass reads as a
    # unit inversion.
    pad_used = float(meta['pad_used_angstrom'])
    expected_lo = coords.min(axis=0) - pad_used
    assert np.allclose(origin_bohr * fs.BOHR, expected_lo, atol=1e-4), (
        f'cube origin {origin_bohr * fs.BOHR} A != padded box corner '
        f'{expected_lo} — an Angstrom/Bohr inversion')

    dims = [int(lines[3 + i].split()[0]) for i in range(3)]
    assert dims == meta['dims'], f'cube dims {dims} != meta dims {meta["dims"]}'
    values = np.array([float(v) for ln in lines[3 + 3 + natoms:] if ln.strip()
                       for v in ln.split()])
    assert values.size == dims[0] * dims[1] * dims[2], (
        f'{values.size} voxels written, {dims[0] * dims[1] * dims[2]} declared')
    assert np.isfinite(values).all(), 'non-finite voxel in the cube'
    assert math.isclose(values.min(), meta['vmin'], rel_tol=1e-4)
    assert math.isclose(values.max(), meta['vmax'], rel_tol=1e-4)


# ════════════════════════════════════════════════════════════════════════════
# INCIDENT 4 — an atom cap cannot bound the clock
# ════════════════════════════════════════════════════════════════════════════

def test_a_zero_budget_is_refused_before_an_scf_object_exists():
    """The pre-flight, which is a DIFFERENT guard from the in-loop watchdog.

    `max_seconds=0` is documented across this repo as "refuse before doing any
    work, tell me the cost". Until the pre-flight landed it was answered by the
    watchdog after one SCF cycle — measured against the job ledger at 1.03 s and
    1.28 s, recorded as BUDGET. A rule stated in four comments and implemented in
    none of them is a rule about intentions.

    The refusal must carry the ESTIMATE and the basis-function count, because
    "too slow" without a number gives the caller nothing to send instead.
    """
    mol = prepared('c1ccccc1')
    fs._scf_cache.clear()
    t0 = time.time()
    try:
        fs.run_scf(mol, 'sto-3g', max_seconds=0.0)
    except fs.FieldBudgetExceeded as e:
        elapsed = time.time() - t0
        assert elapsed < 1.0, (
            f'the pre-flight took {elapsed:.2f} s — if it is not fast it is not a '
            f'pre-flight, it is the watchdog wearing its name')
        assert 'estimated' in str(e) and 'basis functions' in str(e), (
            f'the refusal carries no estimate, so the caller cannot know what to '
            f'send instead: {e}')
    else:
        raise AssertionError('a 0 s budget did not refuse before running')


def test_deadline_fires_from_inside_the_scf_loop():
    """HF is O(nao^4) per ITERATION and the iteration count is unbounded, so
    MAX_QM_ATOMS bounds SIZE and nothing else. Benzene is 12 atoms — a tenth of
    the atom cap — and a budget it cannot meet must still stop it, which can only
    happen from a check inside the SCF loop. Positive control in the same test:
    the same molecule with a real budget must converge, or the refusal above
    proves nothing.

    ⚠ THE BUDGET IS TINY-BUT-NONZERO, and the reason is an interaction that
    broke this witness the moment a new guard landed. This test used
    max_seconds=0.0. run_scf then gained a PRE-FLIGHT estimate, which refuses a
    0 s budget BEFORE building an SCF object at all — correct behaviour, and
    exactly what "refuse immediately and tell me the cost" is supposed to mean.
    But it made this witness unreachable: it went red on a message that has no
    cycle count because no cycle ever ran, and the in-loop watchdog it exists to
    prove was no longer being exercised by it.
    A new guard that shadows an older guard's test does not remove the need for
    the older guard — it removes the EVIDENCE for it, which is worse, because the
    suite still looks green in every other respect. So the budget here is now
    0.001 s: above the pre-flight's estimate for benzene (it predicts ~0.0 s at
    36 basis functions, so the screen passes) and far below what one iteration
    costs, which puts the refusal back inside the loop where this test can see
    it. The pre-flight's own behaviour is covered separately by the zero-budget
    test, so both guards keep a witness of their own.

    0.05 s is chosen against a MEASURED number, not by feel: the pre-flight's
    model gives 2.8 * 5.9e-9 * 36^4.03 ~= 0.033 s for benzene at 36 basis
    functions, so 0.05 clears the screen, while a converged benzene SCF is ~0.3 s
    — an order of magnitude above it. If a much faster machine ever converges
    benzene inside 0.05 s this test goes red rather than silently stopping being
    a witness, which is the correct direction to fail.
    """
    if SKIP_SLOW:
        skip('DIRAC_TESTS_SKIP_SLOW=1 (this test runs a real sto-3g SCF, ~3 s)')

    mol = prepared('c1ccccc1')
    assert mol.GetNumAtoms() < fs.MAX_QM_ATOMS / 5, (
        'the point of this test is a molecule far under the atom cap')

    fs._scf_cache.clear()
    t0 = time.time()
    try:
        fs.run_scf(mol, 'sto-3g', max_seconds=0.05)
    except fs.FieldBudgetExceeded as e:
        elapsed = time.time() - t0
        assert elapsed < 30.0, f'the deadline took {elapsed:.1f} s to fire'
        assert 'budget' in str(e), f'the refusal does not name the budget: {e}'
        assert 'cycles' in str(e), (
            f'the refusal does not report the cycle count, so a reader cannot '
            f'tell a slow iteration from many iterations: {e}')
    else:
        raise AssertionError(
            'a 0.05 s budget ran an SCF to completion — the deadline is not '
            'inside the SCF loop (this is the HEM incident: 22 cores, 36 min)')

    assert not fs._scf_cache, 'a refused SCF was cached — every retry now fails instantly'

    fs._scf_cache.clear()
    res = fs.run_scf(mol, 'sto-3g', max_seconds=120.0)
    assert res['converged'] is True, 'positive control failed: benzene/sto-3g did not converge'
    assert res['scf_cycles'] >= 2 and res['nbasis'] == 36
    assert res['mf']._eri is None, (
        'the two-electron integrals were retained in the SCF cache — this is '
        'what killed the daemon mid-sweep')
    assert len(fs._scf_cache) == 1


# FIXED 2026-08-11 (this test is why). Was: The non-finite guard (`if not math.isfinite(req_seconds)`) lives ONLY in  Handler.do_POST. No callable in the module clamps its own budget, so  run_sc...
# The marker is removed because the defect is gone; the test stays as the
# regression witness. A @known_defect that starts passing FAILS the suite,
# which is how this file told me the fixes had landed.
def test_run_scf_cannot_be_disabled_by_a_non_finite_budget():
    """CONTRACT: no caller of the library can switch the deadline off."""
    if SKIP_SLOW:
        skip('DIRAC_TESTS_SKIP_SLOW=1 (this test runs a real sto-3g SCF, ~3 s)')
    mol = prepared('c1ccccc1')
    fs._scf_cache.clear()
    # DISCRIMINATING FORM. run_scf now clamps a non-finite budget to
    # DEFAULT_MAX_SECONDS, which is the right behaviour but is invisible from
    # outside: 90 s is plenty for benzene, so nothing raises and the test could
    # not tell "clamped" from "unbounded". Shrinking the default for the
    # duration makes the clamp observable — if the guard is ever removed, nan
    # becomes unbounded again and this test goes red instead of quietly passing.
    saved = fs.DEFAULT_MAX_SECONDS
    try:
        fs.DEFAULT_MAX_SECONDS = 0.0      # a clamped budget must now refuse
        for bad in (float('nan'), float('inf')):
            fs._scf_cache.clear()
            try:
                fs.run_scf(mol, 'sto-3g', max_seconds=bad)
            except fs.FieldBudgetExceeded:
                continue                  # clamped to the (tiny) default
            except ValueError:
                continue                  # or rejected outright — also fine
            raise AssertionError(
                f'run_scf(max_seconds={bad!r}) ran with no enforceable deadline')
        # POSITIVE CONTROL for this test's own instrument: with the default
        # restored, a finite budget must still COMPLETE — otherwise the test
        # above would pass simply because everything raises.
        fs.DEFAULT_MAX_SECONDS = saved
        fs._scf_cache.clear()
        fs.run_scf(mol, 'sto-3g', max_seconds=saved)
    finally:
        fs.DEFAULT_MAX_SECONDS = saved


def _serve_in_thread():
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(('127.0.0.1', 0), fs.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def _post_field(port: int, body: dict, timeout: float = 300.0) -> dict:
    req = urllib.request.Request(
        f'http://127.0.0.1:{port}/field',
        data=json.dumps(body).encode(),
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def test_http_path_clamps_non_finite_max_seconds():
    """`max_seconds: "nan"` must not disable the bound.

    NaN fails every comparison, so `min(max(nan, 1.0), 900.0)` is nan and
    `time.time() > nan` is False: one JSON token would have failed the deadline
    OPEN, which is the worst direction for a gate.

    Made DISCRIMINATING by shrinking DEFAULT_MAX_SECONDS for the duration of
    the test. If nan falls back to the default, the effective budget is tiny and
    the request must be REFUSED with reason='budget'. If nan leaks through, the
    request succeeds — which is the defect. A generous finite budget is sent
    through the same path as the positive control, so a refusal cannot be
    credited to something structural.
    """
    if SKIP_SLOW:
        skip('DIRAC_TESTS_SKIP_SLOW=1 (this test runs real sto-3g SCFs, ~6 s)')

    molblock, _ = embedded('c1ccccc1')
    try:
        srv, port = _serve_in_thread()
    except OSError as e:
        skip(f'cannot bind a loopback port in this environment: {e}')

    saved = fs.DEFAULT_MAX_SECONDS
    try:
        fs.DEFAULT_MAX_SECONDS = 0.001      # clamped up to the 1.0 s floor
        fs._scf_cache.clear()
        nan_reply = _post_field(port, {'molfile': molblock, 'kind': 'homo',
                                       'basis': 'sto-3g', 'max_seconds': 'nan'})
        assert nan_reply.get('ok') is False, (
            "max_seconds='nan' produced a field: the non-finite budget was "
            'passed through and the deadline was disabled')
        assert nan_reply.get('reason') == 'budget', (
            f'refused for the wrong reason: {nan_reply!r}')
        assert 'budget' in nan_reply.get('error', '')

        fs.DEFAULT_MAX_SECONDS = saved
        fs._scf_cache.clear()
        ok_reply = _post_field(port, {'molfile': molblock, 'kind': 'homo',
                                     'basis': 'sto-3g', 'max_seconds': 120.0})
        assert ok_reply.get('ok') is True, (
            f'positive control failed — the refusal above proves nothing: '
            f'{ok_reply.get("error")!r}')
        assert ok_reply['meta']['converged'] is True
        assert ok_reply['meta']['ecp'] == [], 'no ECP expected for benzene'
    finally:
        fs.DEFAULT_MAX_SECONDS = saved
        srv.shutdown()
        srv.server_close()

    assert math.isfinite(fs.DEFAULT_MAX_SECONDS) and fs.DEFAULT_MAX_SECONDS > 0
    assert math.isfinite(fs.MAX_MAX_SECONDS), 'the budget ceiling must be finite'
    assert fs.SOSCF_MIN_REMAINING > 0, (
        'the second-order rescue must not start with no room left')


# ════════════════════════════════════════════════════════════════════════════
# standalone runner — pytest is not installed in backend/env
# ════════════════════════════════════════════════════════════════════════════

def _tests() -> list:
    return [v for k, v in globals().items()
            if k.startswith('test_') and callable(v)]


def main(argv: list[str]) -> int:
    only = [a for a in argv[1:] if not a.startswith('-')]
    tests = _tests()
    if only:
        tests = [t for t in tests if any(o in t.__name__ for o in only)]

    print(f'physics contracts — {len(tests)} tests, '
          f'pytest {"present" if _HAVE_PYTEST else "ABSENT (standalone mode)"}')
    print('─' * 100)
    passed = failed = skipped = findings = 0
    failures: list[tuple[str, str]] = []

    for fn in tests:
        defect = getattr(fn, '__known_defect__', None)
        name = fn.__name__
        t0 = time.time()
        try:
            fn()
        except Skipped as e:
            print(f'SKIP    {name:<62}        {e}')
            skipped += 1
            continue
        except AssertionError as e:
            dt = time.time() - t0
            if defect:
                print(f'FINDING {name:<62} {dt:6.2f}s  known defect')
                for ln in str(e).splitlines():
                    print(f'        │ {ln}')
                print(f'        └─ WHY IT IS OPEN: {" ".join(defect.split())}')
                findings += 1
            else:
                print(f'FAIL    {name:<62} {dt:6.2f}s')
                failed += 1
                failures.append((name, traceback.format_exc()))
            continue
        except Exception:
            dt = time.time() - t0
            print(f'ERROR   {name:<62} {dt:6.2f}s')
            failed += 1
            failures.append((name, traceback.format_exc()))
            continue
        dt = time.time() - t0
        if defect:
            print(f'XPASS   {name:<62} {dt:6.2f}s  DEFECT FIXED — remove @known_defect')
            failed += 1
            failures.append((name, 'passed while marked @known_defect (strict)'))
        else:
            print(f'PASS    {name:<62} {dt:6.2f}s')
            passed += 1

    print('─' * 100)
    print(f'{passed} passed · {findings} findings (known defects) · '
          f'{skipped} skipped · {failed} failed')
    for name, tb in failures:
        print(f'\n══ {name} ' + '═' * (96 - len(name)))
        print(tb)
    if findings:
        print('\nFINDINGS are contracts the code violates today. They do not fail '
              'this suite; they are reported so a refactor cannot inherit them '
              'silently. Read the @known_defect marker on each.')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
