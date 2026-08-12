#!/usr/bin/env python3
"""Addressing, the inline decision, and tamper detection — with no database.

Import-light on purpose (this raises the portability ratchet): everything here is
arithmetic about bytes and thresholds, and none of it needs psycopg, RDKit or a
running daemon. If any of these tests ever requires one, a dependency has leaked into
a layer that three transports share.

THE PROPERTY THESE TESTS PROTECT, stated once: a client's verification code must be
identical whether the bytes arrived inline or by reference. The moment those two
paths differ, one of them stops being exercised, and it will be the one that carries
2.5 MB.

Run: python3 backend/tests/test_artifacts.py
"""
from __future__ import annotations

import base64
import hashlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import artifacts as A                                               # noqa: E402
import failures as F                                                # noqa: E402

PASS, FAIL = [], []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print(f'PASS    {name}')
    except AssertionError as e:
        FAIL.append(name)
        print(f'FAIL    {name}\n        {e}')


CUBE = b'dirac test cube\n' + bytes(range(256)) * 40          # ~10 KB, non-text


def test_the_digest_is_the_identity():
    st = A.MemoryArtifactStore()
    a = st.put(CUBE, role='field.cube')
    assert a.sha256 == hashlib.sha256(CUBE).hexdigest()
    got, data = st.read(f'sha256:{a.sha256}')
    assert data == CUBE, 'the bytes came back different from what went in'
    assert got.sha256 == a.sha256


def test_storing_the_same_bytes_twice_does_not_mint_a_second_artifact():
    """Idempotence under retry. Without it, a client that retries a timed-out request
    gets a second id for the same cube and the store's size grows with the network's
    reliability rather than with the science."""
    st = A.MemoryArtifactStore()
    a = st.put(CUBE, role='field.cube')
    b = st.put(CUBE, role='field.cube')
    assert a.id == b.id, f'two ids for identical bytes in one role: {a.id} vs {b.id}'
    assert st.counters['put_deduplicated'] == 1


def test_the_same_bytes_in_a_different_role_are_a_different_artifact():
    """The identity is (blob, role), not the blob. The same cube can be this job's
    `field.cube` and a comparison's `reference.cube`, and collapsing them would make
    the role unreadable from the reference."""
    st = A.MemoryArtifactStore()
    a = st.put(CUBE, role='field.cube')
    b = st.put(CUBE, role='reference.cube')
    assert a.id != b.id
    assert a.sha256 == b.sha256, 'the bytes should still be shared'


def test_tampered_bytes_are_convicted_on_read():
    """THE RED PROOF for the whole module. Reach into the store, change one byte, and
    demand that read() refuses. A content-addressed store whose read path does not
    verify is a store with a digest column."""
    st = A.MemoryArtifactStore()
    a = st.put(CUBE, role='field.cube')
    corrupted = bytearray(CUBE)
    corrupted[17] ^= 0x01                       # one bit
    st._bytes[a.sha256] = bytes(corrupted)
    try:
        st.read(a.id)
    except F.DiracFailure as e:
        assert e.code == 'INTERNAL', f'a digest mismatch reported as {e.code}'
        assert 'mismatch' in str(e).lower()
        return
    raise AssertionError('one flipped bit was served without complaint — the digest '
                         'is decoration, and every downstream verification inherits '
                         'that')


def test_a_missing_artifact_is_NOT_FOUND_and_not_UNSUPPORTED():
    """The codes drive different client behaviour: UNSUPPORTED means fix the request,
    NOT_FOUND means the reference you hold is dead. A CLI told UNSUPPORTED would
    suggest changing parameters that are not the problem."""
    st = A.MemoryArtifactStore()
    try:
        st.read('sha256:' + '0' * 64)
    except F.DiracFailure as e:
        assert e.code == 'NOT_FOUND', f'a missing artifact reported as {e.code}'
        assert e.retryable is False
        return
    raise AssertionError('reading an absent artifact succeeded')


def test_a_malformed_address_is_refused_at_construction():
    for bad in ('not-a-digest', 'ABCDEF' + '0' * 58, '0' * 63):
        try:
            A.Artifact(sha256=bad, role='field.cube',
                       media_type='application/octet-stream', size_bytes=1)
        except F.DiracFailure:
            continue
        raise AssertionError(f'sha256={bad!r} was accepted; an address that is not '
                             f'well-formed cannot be looked up, so accepting it here '
                             f'just moves the failure to the client')


def test_a_role_must_be_a_vocabulary_not_free_text():
    for bad in ('Field.Cube', 'field cube', 'field-cube', '', '2cube'):
        try:
            A.Artifact(sha256='a' * 64, role=bad, media_type='x/y', size_bytes=1)
        except F.DiracFailure:
            continue
        raise AssertionError(f'role={bad!r} was accepted — within a week the store '
                             f'holds cube, Cube and field_cube for one concept and '
                             f'no client can switch on it')


def test_the_threshold_is_asymmetric_by_design():
    """A client may LOWER the inline limit without bound (an agent protecting its
    context asks for 0) and may raise it only to the ceiling. If raising were
    unbounded, `inline_max=10**9` would restore the 2.5-MB-in-JSON defect through a
    query parameter."""
    assert A.should_inline(1000) is True
    assert A.should_inline(A.INLINE_MAX_BYTES) is True
    assert A.should_inline(A.INLINE_MAX_BYTES + 1) is False
    assert A.should_inline(1000, requested_max=0) is False, 'a client asking for no '\
        'inline bytes still got them'
    assert A.should_inline(A.INLINE_REQUEST_CEILING + 1,
                           requested_max=10 ** 9) is False, (
        'the ceiling can be raised without limit by the caller, which is the defect '
        'this module exists to remove, reachable through a parameter')


def test_a_reference_is_the_same_object_with_or_without_the_bytes():
    """The symmetry that keeps client code single-branched."""
    st = A.MemoryArtifactStore()
    a = st.put(CUBE, role='field.cube')
    inline = a.to_reference(inline=CUBE)
    ref = a.to_reference()
    shared = {'id', 'sha256', 'role', 'media_type', 'size_bytes', 'encoding', 'url',
              'metadata_url'}
    for k in shared:
        assert inline[k] == ref[k], f'{k} differs between the inline and reference forms'
    assert set(inline) - set(ref) == {'inline_base64'}, (
        f'the two forms differ by more than the bytes: '
        f'{set(inline) ^ set(ref)} — every extra difference is a place client code '
        f'has to branch, and one branch will go untested')
    assert inline['inline'] is True and ref['inline'] is False


def test_inline_bytes_are_verified_by_the_client_half():
    st = A.MemoryArtifactStore()
    a = st.put(CUBE, role='field.cube')
    ref = a.to_reference(inline=CUBE)
    assert A.decode_inline(ref) == CUBE
    ref['inline_base64'] = base64.b64encode(CUBE + b'x').decode()
    try:
        A.decode_inline(ref)
    except F.DiracFailure:
        return
    raise AssertionError('the client half accepted inline bytes that do not match the '
                         'advertised digest — so an artifact could be tampered with in '
                         'transit and every check would still pass')


def test_a_reference_cannot_advertise_bytes_it_does_not_describe():
    st = A.MemoryArtifactStore()
    a = st.put(CUBE, role='field.cube')
    try:
        a.to_reference(inline=CUBE + b'tampered')
    except F.DiracFailure:
        return
    raise AssertionError('to_reference embedded bytes whose digest differs from the '
                         'sha256 in the same object — a self-inconsistent reference')


def test_range_parsing_covers_the_forms_a_client_actually_sends():
    n = 1000
    assert A.parse_range('bytes=0-199', n) == (0, 199)
    assert A.parse_range('bytes=200-', n) == (200, 999)
    assert A.parse_range('bytes=-100', n) == (900, 999)
    assert A.parse_range('bytes=0-99999', n) == (0, 999), 'an over-long end should '\
        'clamp, not fail'
    # Unsatisfiable and malformed both serve the whole object rather than raising: a
    # broken Range header must not cost the caller their data.
    for bad in ('bytes=2000-3000', 'bytes=500-100', 'items=0-1', 'bytes=', None, ''):
        assert A.parse_range(bad, n) is None, f'{bad!r} did not fall back to whole'


def test_the_cube_media_type_is_not_text_plain():
    """A client that has to sniff is a client that guesses."""
    assert A.MEDIA_TYPES['field.cube'] == 'application/vnd.dirac.gaussian-cube'
    st = A.MemoryArtifactStore()
    assert st.put(CUBE, role='field.cube').media_type.startswith('application/vnd.dirac')


def test_it_imports_nothing_heavy():
    """The dependency law, asserted rather than trusted (ADR-001, gate 11)."""
    import ast
    import pathlib
    src = pathlib.Path(__file__).resolve().parent.parent / 'artifacts.py'
    tree = ast.parse(src.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split('.')[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split('.')[0])
    forbidden = names & {'psycopg', 'psycopg2', 'rdkit', 'pyscf', 'numpy', 'http',
                         'urllib', 'field_server', 'envelope', 'jobs'}
    assert not forbidden, (
        f'artifacts.py imports {sorted(forbidden)} — the addressing logic is shared by '
        f'an offline CLI, an SDK and an MCP adapter, and none of them can be made to '
        f'depend on a database driver or a chemistry toolkit to compute a digest')


for name, fn in list(globals().items()):
    if name.startswith('test_') and callable(fn):
        check(name, fn)

print('─' * 100)
print(f'{len(PASS)} passed · {len(FAIL)} failed')
sys.exit(1 if FAIL else 0)
