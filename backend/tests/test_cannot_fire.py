#!/usr/bin/env python3
"""Witness suite for one bug CLASS — "a check that cannot fire" — hunted across
the whole repo after today's ecp_for incident (see backend/field_server.py's
`FIXED 2026-08-11` comment on `ecp_for()`, and backend/tests/test_physics_contracts.py
`test_an_ecp_claim_must_mean_core_electrons_were_replaced`).

Full ledger, including everything searched and every UNVERIFIED candidate:
docs/CHECKS_AUDIT.md. This file carries only the CONFIRMED findings, one
executable witness each.

THE CLASS: a guard whose failure path is unreachable (a try/except around a
call that returns a falsy sentinel instead of raising), or a comparison that
is always true/false because of NaN, or an assertion that silently no-ops on
a missing/empty input instead of failing loud.

THREE CONFIRMED FINDINGS, each a SIBLING of an incident already fixed
elsewhere in this repo -- the same defect class, independently reintroduced
in a different module that nobody re-checked when the first one was fixed:

  F1  backend/physics/mep_surface.py:96-109 `_ecp_for()`
      The exact bug field_server.py's `ecp_for()` had until today:
      `gto.basis.load_ecp(basis, symbol)` is called inside try/except Exception,
      but pyscf's load_ecp RETURNS [] (falsy) for an element the basis defines
      no ECP for -- it does not raise. So the except branch is unreachable, and
      every heavy element (Z>=37) gets an `ecp[symbol] = basis` entry
      unconditionally, regardless of whether the basis can supply one. Iodine
      under sto-3g (or 6-31g, or 6-31g*) is reported as ECP-corrected while
      running fully all-electron.
      -> test_mep_surface_ecp_claim_must_mean_core_electrons_were_replaced

  F2  backend/physics/mep_surface.py:339 (compute_surface_mep) and
      backend/physics/server.py:116 (Handler.do_POST)
      The exact bug field_server.py's run_scf/do_POST had until today: NaN
      fails every comparison, so `if max_seconds and predicted > max_seconds:`
      is False whenever max_seconds is NaN (`nan` is truthy, and
      `predicted > nan` is always False) -- silently disabling the ONLY
      wall-clock budget guard in this module. Unlike field_server.py's
      run_scf(), compute_surface_mep() has no watchdog callback inside the SCF
      loop either, so once the pre-flight guard is defeated there is no
      second line of defence. Reachable from the network: physics/server.py's
      HTTP handler passes `float(req.get('max_seconds', DEFAULT_MAX_SECONDS))`
      straight through with no isfinite clamp, and JSON's decoder accepts the
      literal `NaN` by default.
      -> test_mep_surface_max_seconds_is_never_finiteness_checked   (fast, source-level)
      -> test_mep_surface_nan_budget_disables_the_predicted_cost_gate (slow, real SCF)

  F3  design/check_palette.py:120-128 (the diverging-pair ΔE separability gate)
      `if a not in tokens or b not in tokens: continue` treats a MISSING or
      RENAMED token as "nothing to check" instead of a hard failure. This gate
      is wired into CI (.github/workflows/dirac.yml Gate 3, scripts/gates.sh
      gate-3-palette) and exists specifically because "A note in a style guide
      decays the first time someone reaches for a Tailwind default" (the
      file's own docstring) -- yet the one check that is supposed to catch a
      renamed colour token silently exempts it instead of failing, and the
      gate still prints "palette OK" and exits 0.
      -> test_check_palette_diverging_pair_gate_silently_skips_a_renamed_token

Every test below is a WITNESS: it is EXPECTED TO FAIL right now, because the
defect it names is still in the code. It will start passing the moment
whichever session owns that file lands the fix -- no change to this file is
needed for that to happen. Each is decorated with @confirmed_defect so a red
run reads as "defect confirmed, as documented" rather than "test is broken".

Run either way:
    backend/env/bin/python backend/tests/test_cannot_fire.py
    backend/env/bin/pytest backend/tests/test_cannot_fire.py     # if pytest is ever installed

Environment switches:
    DIRAC_TESTS_SKIP_SLOW=1   skip the one witness that runs a real SCF (~30s)

Runner pattern copied from backend/tests/test_physics_contracts.py (pytest is
NOT installed in backend/env) -- this file does not import that one, so it can
be read and run independently of whatever the three sessions owning
envelope.py / admin_queries.py / check_constraints.sql / test_physics_contracts.py
are doing right now.
"""
from __future__ import annotations

import importlib.util
import inspect
import os
import pathlib
import sys
import tempfile
import time
import traceback

_HERE = pathlib.Path(__file__).resolve().parent
_BACKEND = _HERE.parent
_REPO = _BACKEND.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

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


def confirmed_defect(finding_id: str):
    """LABEL ONLY -- does not mute a failure and does not use xfail. Every test
    marked with this is expected to be RED today; the label exists so the
    transcript names WHICH documented finding (docs/CHECKS_AUDIT.md) a given
    red line is proving, rather than reading like an environment problem."""
    def deco(fn):
        fn.__confirmed_defect__ = finding_id
        return fn
    return deco


def _load_module_from_path(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ════════════════════════════════════════════════════════════════════════════
# FINDING 1 — mep_surface.py's _ecp_for is the pre-fix ecp_for, reintroduced
# ════════════════════════════════════════════════════════════════════════════

@confirmed_defect('F1')
def test_mep_surface_ecp_claim_must_mean_core_electrons_were_replaced():
    """CONTRACT (already met by field_server.py's ecp_for as of today's fix):
    if `_ecp_for` names an element, that element's core must actually be
    replaced in the Mole that gets built from it. Verified against pyscf
    itself, not against a memory of pyscf.

    mep_surface.py:104-107:

        try:
            gto.basis.load_ecp(basis, symbol)
        except Exception:
            continue
        ecp[symbol] = basis

    load_ecp's return value is never checked, only whether it raised. It does
    not raise for a basis that defines no ECP -- it returns `[]`. So the
    except branch is dead code and every element with Z>=37 gets
    `ecp[symbol]=basis` unconditionally.
    """
    from pyscf import gto
    from rdkit.Chem import GetPeriodicTable
    import physics.mep_surface as ms

    pt = GetPeriodicTable()
    z_total = pt.GetAtomicNumber('I') + pt.GetAtomicNumber('H')
    atoms = [('I', (0.0, 0.0, 0.0)), ('H', (0.0, 0.0, 1.6))]

    violations = []
    for basis in ('sto-3g', '6-31g', '6-31g*', 'def2-svp'):
        ecp = ms._ecp_for(atoms, basis)
        if 'I' not in ecp:
            continue          # correctly silent: no claim was made
        try:
            mol = gto.M(atom='I 0 0 0; H 0 0 1.6', basis=basis,
                        ecp={'I': ecp['I']}, verbose=0)
        except Exception as e:                                  # noqa: BLE001
            violations.append(
                f'{basis}: _ecp_for(...) == {ecp!r} but the Mole cannot even '
                f'be built from that claim '
                f'({type(e).__name__}: {str(e).splitlines()[0]})')
            continue
        if mol.nelectron >= z_total:
            violations.append(
                f'{basis}: _ecp_for(...) == {ecp!r} but the Mole built from '
                f'that claim still has all {mol.nelectron} electrons '
                f'(all-electron, z_total={z_total}) -- the ECP was never '
                f'actually attached')

    assert not violations, (
        'mep_surface.py:96-109 _ecp_for() claims an ECP without the core '
        'being replaced -- this is the exact defect fixed today in '
        'field_server.py ecp_for() (see its `FIXED 2026-08-11` comment and '
        'backend/tests/test_physics_contracts.py '
        'test_an_ecp_claim_must_mean_core_electrons_were_replaced), '
        'reintroduced in the sibling module that computes the sigma-hole '
        'surface:\n  ' + '\n  '.join(violations))


# ════════════════════════════════════════════════════════════════════════════
# FINDING 2 — NaN defeats the only wall-clock budget guard in mep_surface.py
# ════════════════════════════════════════════════════════════════════════════

@confirmed_defect('F2')
def test_mep_surface_max_seconds_is_never_finiteness_checked():
    """CONTRACT (already met by field_server.py, in BOTH run_scf() at line 818
    and Handler.do_POST() at line 1179): a non-finite max_seconds must be
    normalised before it reaches a `predicted > max_seconds` comparison,
    because NaN fails every comparison --
    `nan and (predicted > nan)` is False no matter how large `predicted` is.

    This is a fast, source-level witness (no pyscf run): it reads the real
    source of compute_surface_mep() and of the HTTP handler that feeds it, and
    checks for the isfinite/isnan clamp that field_server.py carries at both
    of its equivalent layers. See test_mep_surface_nan_budget_disables_the_
    predicted_cost_gate below for the slow, end-to-end behavioural proof.
    """
    import physics.mep_surface as ms
    import physics.server as srv

    mep_src = inspect.getsource(ms.compute_surface_mep)
    assert 'isfinite' in mep_src or 'isnan' in mep_src, (
        'backend/physics/mep_surface.py: compute_surface_mep() never checks '
        '`max_seconds` for finiteness before '
        '`if max_seconds and predicted > max_seconds:` (~line 339) -- a NaN '
        'budget makes that comparison False unconditionally, silently '
        'disabling the ONLY wall-clock guard in the function (there is no '
        'in-loop SCF watchdog callback here, unlike field_server.py.run_scf, '
        'so once this pre-flight check is bypassed nothing else bounds the '
        'computation).')

    server_src = inspect.getsource(srv.Handler.do_POST)
    assert 'isfinite' in server_src or 'isnan' in server_src, (
        'backend/physics/server.py: Handler.do_POST() passes '
        "`float(req.get('max_seconds', DEFAULT_MAX_SECONDS))` straight into "
        'compute_surface_mep with no finiteness clamp (~line 116) -- '
        "field_server.py's HTTP layer clamps this exact input "
        '(`if not math.isfinite(req_seconds): req_seconds = DEFAULT_MAX_SECONDS`, '
        'line 1179-1181); this sibling daemon never got the fix, and nothing '
        'in this file whitelists the basis either, unlike field_server.py\'s '
        'ALLOWED_BASIS check.')


@confirmed_defect('F2')
def test_mep_surface_nan_budget_disables_the_predicted_cost_gate():
    """End-to-end behavioural proof of F2, on a REAL (tiny) SCF: methane,
    sto-3g. `estimated_scf_seconds` is monkeypatched to report an absurd
    predicted cost (1e9 s) so that, on any finite budget, the pre-flight gate
    at mep_surface.py:339 would refuse immediately with ValueError. Sent with
    `max_seconds=float('nan')` instead, the real (unpatched) code proceeds to
    run a full SCF to completion -- proving the gate never evaluated the
    predicted-cost comparison at all. This is the same demonstration as
    `test_http_path_clamps_non_finite_max_seconds` in
    backend/tests/test_physics_contracts.py, aimed at the sibling module that
    never received that fix.

    ~25-30 s: not because the bug is slow to trigger, but because methane's
    real SCF and surface-point search run to completion when the guard fails
    to stop them -- which is exactly the point being demonstrated.
    """
    if SKIP_SLOW:
        skip('DIRAC_TESTS_SKIP_SLOW=1 (runs a real sto-3g SCF + surface search, ~30s)')

    from rdkit import Chem
    from rdkit.Chem import AllChem
    import physics.mep_surface as ms

    mol = Chem.AddHs(Chem.MolFromSmiles('C'))
    AllChem.EmbedMolecule(mol, randomSeed=42)
    AllChem.MMFFOptimizeMolecule(mol, maxIters=500)
    molblock = Chem.MolToMolBlock(mol)

    original = ms.estimated_scf_seconds
    ms.estimated_scf_seconds = lambda nao: 1e9      # "this will take 31 years"
    try:
        t0 = time.time()
        try:
            out = ms.compute_surface_mep(molblock, basis='sto-3g',
                                         max_seconds=float('nan'),
                                         points_per_atom=8)
        except ValueError as e:
            # Correct behaviour, if this file is ever fixed: the gate fired
            # despite the NaN budget (it clamped to a finite default first).
            assert 'budget' in str(e) or 'predicted' in str(e), (
                f'raised ValueError but not the budget refusal: {e}')
            return
        raise AssertionError(
            f'compute_surface_mep(max_seconds=nan) ran a full SCF to '
            f'completion in {time.time() - t0:.1f}s with a PREDICTED cost of '
            f'1e9 s against it (meta.predicted_scf_seconds='
            f'{out["meta"]["predicted_scf_seconds"]!r}) -- the budget gate at '
            'mep_surface.py:339 (`if max_seconds and predicted > max_seconds:`) '
            'never fired, because `nan and (predicted > nan)` is False '
            'regardless of `predicted`. One JSON token '
            '(`"max_seconds": NaN` -- Python\'s json module accepts this by '
            'default) disables the only cost gate in this function.')
    finally:
        ms.estimated_scf_seconds = original


# ════════════════════════════════════════════════════════════════════════════
# FINDING 3 — check_palette.py's diverging-pair gate no-ops on a renamed token
# ════════════════════════════════════════════════════════════════════════════

@confirmed_defect('F3')
def test_check_palette_diverging_pair_gate_silently_skips_a_renamed_token():
    """CONTRACT: the palette gate (wired into CI: .github/workflows/dirac.yml
    Gate 3, scripts/gates.sh gate-3-palette) claims, on a clean exit, that
    "every token [is] inside the ceiling, legible, and separable" -- see its
    own final success line. Separability is the ΔE check over DIVERGING_PAIRS.

    check_palette.py:120-122:

        for a, b, label in DIVERGING_PAIRS:
            if a not in tokens or b not in tokens:
                continue
            separation = delta_e(tokens[a], tokens[b])
            ...

    A token that is renamed, typo'd, or dropped from tokens.css (exactly the
    class of drift this file's own docstring says already happened once with
    the Tailwind defaults) makes `a not in tokens` true, and the pair is
    silently exempted from the ΔE separability check rather than failing the
    gate. This test mutates a SCRATCH COPY of the real tokens.css (never the
    repo file), renaming --viz-cb-neg -- one half of the colourblind-safe
    diverging pair -- in both themes, and shows the gate still reports
    "palette OK" and returns exit code 0.
    """
    check_palette = _load_module_from_path(
        'dirac_design_check_palette', _REPO / 'design' / 'check_palette.py')

    real_tokens_path = _REPO / 'design' / 'tokens.css'
    if not real_tokens_path.exists():
        skip(f'{real_tokens_path} does not exist in this checkout')
    source_css = real_tokens_path.read_text()
    if '--viz-cb-neg' not in source_css:
        skip('--viz-cb-neg is no longer a token in design/tokens.css -- '
             're-derive this test against whichever DIVERGING_PAIRS token '
             'still exists')

    # A realistic drift: the variable renamed (e.g. during a refactor), not
    # deleted -- the colour itself is still declared and still passes its own
    # ceiling/contrast checks, so ONLY the pairing check is the one that must
    # catch this, and it is the one the mutation targets.
    mutated_css = source_css.replace('--viz-cb-neg', '--viz-cb-neg-renamed')
    assert mutated_css != source_css, 'the mutation did not change anything'

    fd, tmp_path_str = tempfile.mkstemp(suffix='.css')
    os.close(fd)
    tmp_path = pathlib.Path(tmp_path_str)
    try:
        tmp_path.write_text(mutated_css)
        check_palette.TOKENS = tmp_path
        exit_code = check_palette.main()
        assert exit_code != 0, (
            'design/check_palette.py exited 0 ("palette OK") on a tokens.css '
            'where --viz-cb-neg was renamed to --viz-cb-neg-renamed in both '
            'themes. The colourblind-safe diverging pair '
            "(DIVERGING_PAIRS' 'viz-cb-pos'/'viz-cb-neg' entry) was never "
            "checked for ΔE separability in either theme, because "
            "`if a not in tokens or b not in tokens: continue` treats a "
            'missing/renamed key as "nothing to verify" instead of a gate '
            'failure. This check is CI-enforced (Gate 3 in '
            '.github/workflows/dirac.yml, gate-3-palette in scripts/gates.sh) '
            'and would report green on a real drift of this shape.')
    finally:
        tmp_path.unlink(missing_ok=True)


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

    print(f'"check that cannot fire" witnesses — {len(tests)} tests, '
          f'pytest {"present" if _HAVE_PYTEST else "ABSENT (standalone mode)"}')
    print('every test below is EXPECTED TO FAIL until its named finding is fixed')
    print('─' * 100)
    passed = failed = skipped = 0
    failures: list[tuple[str, str, str]] = []

    for fn in tests:
        finding = getattr(fn, '__confirmed_defect__', '?')
        name = fn.__name__
        t0 = time.time()
        try:
            fn()
        except Skipped as e:
            print(f'SKIP    [{finding}] {name:<62}        {e}')
            skipped += 1
            continue
        except AssertionError as e:
            dt = time.time() - t0
            print(f'FAIL    [{finding}] {name:<62} {dt:6.2f}s  '
                  f'(EXPECTED — confirmed unfixed defect)')
            failed += 1
            failures.append((finding, name, str(e)))
            continue
        except Exception:
            dt = time.time() - t0
            print(f'ERROR   [{finding}] {name:<62} {dt:6.2f}s  '
                  f'(unexpected — not the documented AssertionError shape)')
            failed += 1
            failures.append((finding, name, traceback.format_exc()))
            continue
        dt = time.time() - t0
        print(f'PASS    [{finding}] {name:<62} {dt:6.2f}s  '
              f'(defect appears FIXED — update docs/CHECKS_AUDIT.md)')
        passed += 1

    print('─' * 100)
    print(f'{passed} passed (fixed) · {failed} failed (confirmed defects still present) · '
          f'{skipped} skipped')
    for finding, name, msg in failures:
        print(f'\n══ [{finding}] {name} ' + '═' * max(1, 90 - len(name) - len(finding)))
        print(msg)

    if failed and passed == 0 and skipped == 0:
        print('\nAll red, as expected: every confirmed finding in '
              'docs/CHECKS_AUDIT.md is still present in the code. This exit '
              'code is intentionally non-zero — do not "fix" this suite by '
              'weakening an assertion; fix the file the assertion names.')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
