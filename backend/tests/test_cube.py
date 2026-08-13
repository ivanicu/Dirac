#!/usr/bin/env python3
"""Cube canonicalisation: narrow enough not to touch a chemist's own comments.

WHY THIS MODULE EXISTS AT ALL, measured today: pyscf writes the wall clock into a
cube's second line, so the SHA-256 of a quantum field was a function of the time of
day. Three identical runs, three different digests. Content-addressed storage could
therefore never deduplicate a quantum cube, and the cross-transport acceptance test was
IMPOSSIBLE rather than merely failing — and it passed once by luck, when both legs
landed in the same second, which is the worst of the three outcomes.

The risk in the fix is the opposite of the bug: a rule loose enough to rewrite text it
was not aimed at. Half the tests here are about NOT touching things.

Import-light (stdlib only), because a CLI verifying an artifact's digest must
canonicalise identically and cannot import a chemistry toolkit to do it.

Run: python3 backend/tests/test_cube.py
"""
from __future__ import annotations

import hashlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import cube as CU                                                   # noqa: E402

PASS, FAIL = [], []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print(f'PASS    {name}')
    except AssertionError as e:
        FAIL.append(name)
        print(f'FAIL    {name}\n        {e}')
    except Exception as e:                                            # noqa: BLE001
        import traceback
        FAIL.append(name)
        print(f'FAIL    {name}  (the test itself raised {type(e).__name__}: {e})\n'
              + ''.join('        ' + l for l in
                        traceback.format_exc(limit=3).splitlines(keepends=True)))


def pyscf_cube(when: str) -> str:
    return ('Orbital value in real space (1/Bohr^3)\n'
            f'PySCF Version: 2.14.0  Date: {when}\n'
            '    3    -5.669178    -5.669178    -5.669178\n'
            '   80     0.143523     0.000000     0.000000\n'
            '   80     0.000000     0.143523     0.000000\n'
            '   80     0.000000     0.000000     0.143523\n'
            '    8    8.000000    0.000000    0.000000    0.000000\n'
            '  1  0.10000E+00  0.20000E+00\n')


DIRAC_CUBE = ('Dirac fields backend\n'
              'mep gasteiger kcal/mol/e\n'
              '    3    -5.0    -5.0    -5.0\n'
              '   40     0.25    0.0    0.0\n'
              '   40     0.0     0.25   0.0\n'
              '   40     0.0     0.0    0.25\n'
              '    8    8.0    0.0    0.0    0.0\n'
              '  0.1  0.2\n')


def test_the_same_field_at_two_times_gets_ONE_digest():
    """THE PROPERTY. This is the bug, expressed as an assertion."""
    a = pyscf_cube('Tue Aug 11 19:54:01 2026')
    b = pyscf_cube('Wed Sep  2 03:11:59 2026')
    assert a != b, 'the fixture does not reproduce the defect'
    da = hashlib.sha256(CU.canonicalise(a).encode()).hexdigest()
    db = hashlib.sha256(CU.canonicalise(b).encode()).hexdigest()
    assert da == db, (
        'two computations of the identical field still hash differently, so the store '
        'cannot deduplicate them and no two transports can ever agree on an artifact '
        'digest')


def test_it_is_idempotent():
    """Applied on the compute path, the read path and (later) in a CLI verifier. A
    non-idempotent rule would give a cache hit a different digest from the compute
    that produced it — which is the exact bug it was written to fix, one layer up."""
    once = CU.canonicalise(pyscf_cube('Tue Aug 11 19:54:01 2026'))
    assert CU.canonicalise(once) == once
    assert CU.canonicalise(CU.canonicalise(once)) == once


def test_it_does_not_touch_a_cube_we_wrote_ourselves():
    """write_cube's comment is caller-supplied and carries no clock. If
    canonicalisation altered it, every classical cube in the 200-row cache would change
    digest for no reason."""
    assert CU.canonicalise(DIRAC_CUBE) == DIRAC_CUBE
    assert CU.is_canonical(DIRAC_CUBE)


def test_it_does_not_eat_text_that_merely_looks_datelike():
    """The rule is anchored to pyscf's exact prefix. A looser one would rewrite a
    chemist's own comment, and that corruption would go unnoticed for months."""
    for line in ('Computed on Tue Aug 11 for the Smith series',
                 'Date: 2026-08-11 — batch 4',
                 'PySCF-like Version: 2.14.0  Date: today',
                 'homo of ligand 44, Date: unknown'):
        c = f'first comment\n{line}\n    3   0.0 0.0 0.0\n'
        assert CU.canonicalise(c) == c, (
            f'canonicalise rewrote {line!r}, which is not a pyscf provenance line — a '
            f'rule that edits arbitrary comment text is data loss, not normalisation')


def test_the_toolkit_version_SURVIVES():
    """The timestamp is noise for addressing; the version is provenance. Dropping both
    would be throwing away the one fact that says which code wrote the bytes."""
    out = CU.canonicalise(pyscf_cube('Tue Aug 11 19:54:01 2026'))
    assert 'PySCF Version: 2.14.0' in out, (
        'the pyscf version was removed along with the date; a client can no longer tell '
        'which toolkit produced the file it is holding')
    assert '19:54:01' not in out


def test_the_removed_timestamp_is_RECOVERABLE():
    """It is a real fact and must not simply vanish. The caller puts it in the
    response metadata, where a fact about the REQUEST belongs."""
    a = pyscf_cube('Tue Aug 11 19:54:01 2026')
    assert CU.timestamp_in(a) == 'Tue Aug 11 19:54:01 2026'
    assert CU.timestamp_in(CU.canonicalise(a)) is None, (
        'a canonical cube still reports a timestamp, so a caller would stamp the '
        'placeholder text into its metadata as if it were a date')
    assert CU.timestamp_in(DIRAC_CUBE) is None


def test_only_line_TWO_is_eligible():
    """A pyscf-shaped line anywhere else is data, not a header. Volumetric values are
    numbers, but a comment deeper in a concatenated multi-cube file must be left
    alone."""
    c = ('first\nsecond comment\n    3   0.0 0.0 0.0\n'
         'PySCF Version: 2.14.0  Date: Tue Aug 11 19:54:01 2026\n')
    assert CU.canonicalise(c) == c, (
        'a pyscf line below the header was rewritten; canonicalisation must be '
        'positional, or it becomes a search-and-replace over the whole payload')


def test_a_degenerate_input_does_not_explode():
    """Called on every artifact, including truncated ones from an interrupted write."""
    for weird in ('', '\n', 'one line only', 'a\nb'):
        CU.canonicalise(weird)          # must not raise
    assert CU.canonicalise('') == ''


def test_a_NON_canonical_cube_is_DETECTED():
    """The red proof for is_canonical: it must convict, or the gate that uses it is
    decoration."""
    assert not CU.is_canonical(pyscf_cube('Tue Aug 11 19:54:01 2026')), (
        'is_canonical accepted a cube carrying a wall-clock timestamp, so nothing '
        'downstream can distinguish an addressable artifact from one whose digest '
        'changes every second')


def test_it_imports_nothing_heavy():
    import ast
    import pathlib
    src = pathlib.Path(__file__).resolve().parent.parent / 'cube.py'
    names = set()
    for node in ast.walk(ast.parse(src.read_text())):
        if isinstance(node, ast.Import):
            names.update(a.name.split('.')[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split('.')[0])
    forbidden = names & {'rdkit', 'pyscf', 'numpy', 'psycopg', 'http', 'urllib',
                         'field_server'}
    assert not forbidden, (
        f'cube.py imports {sorted(forbidden)} — a CLI verifying an artifact digest must '
        f'canonicalise identically, and it cannot be made to install a chemistry '
        f'toolkit to do it')


if __name__ == '__main__':
    for name, fn in list(globals().items()):
        if name.startswith('test_') and callable(fn):
            check(name, fn)

    print('─' * 100)
    print(f'{len(PASS)} passed · {len(FAIL)} failed')
    sys.exit(1 if FAIL else 0)
