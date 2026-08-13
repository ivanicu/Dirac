#!/usr/bin/env python3
"""The Postgres store, and a POSITIVE CONTROL on the constraint it depends on.

test_artifacts.py proves the addressing arithmetic with no database. This suite exists
for the claims that are only true because the SCHEMA makes them true — and the first
test is the one that matters most: it tries to store bytes under the wrong digest and
demands the database refuse.

Why that test and not a happy-path round trip: every other guarantee here is stated in
Python, and Python that has never been contradicted is a comment. `CHECK
(digest(bytes,'sha256') = sha256)` is the only line in this system that makes a
mislabelled blob IMPOSSIBLE rather than merely absent, and a constraint nobody has
watched convict is a constraint that might have been dropped in a migration three
weeks ago.

Leaves nothing behind: every row it writes is deleted in a finally block, and the test
digests are prefixed so a leaked row is identifiable.

Run: backend/env/bin/python backend/tests/test_artifacts_pg.py
Exit: 0 pass · 1 fail · 2 no database (reported as UNVERIFIED, never as pass)
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import artifacts as A                                               # noqa: E402
import failures as F                                                # noqa: E402

try:
    import psycopg
except ImportError:
    print('test_artifacts_pg: psycopg not importable. The schema-level guarantees are '
          'UNVERIFIED — not passing.', file=sys.stderr)
    sys.exit(2)

import artifacts_pg                                                 # noqa: E402

DSN = os.environ.get('DIRAC_TEST_DSN', 'dbname=dirac user=ivan')
if 'pytest' in sys.modules and not os.environ.get('DIRAC_TEST_DSN'):
    import pytest
    pytest.skip('requires isolated PostgreSQL DIRAC_TEST_DSN', allow_module_level=True)
PASS, FAIL = [], []
MARKER = b'dirac-artifact-pg-test-'
WRITTEN: list[str] = []            # digests to clean up


def connect():
    return psycopg.connect(DSN, autocommit=True)


def check(name, fn):
    """Catches every exception, not only AssertionError.

    Written the narrow way first, and one wrong column name in the ninth test aborted
    the run so the tenth never executed — a suite where a broken test HIDES the
    remaining tests, which is the same failure shape as a gate that fails toward pass.
    A crash is a FAIL with a traceback, not an interruption.
    """
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
        print(f'FAIL    {name}  (the test itself raised {type(e).__name__})\n'
              f'        {e}\n'
              + ''.join('        ' + ln for ln in
                        traceback.format_exc(limit=3).splitlines(keepends=True)))


def any_method_row() -> str:
    """A real meta.method row id — app.job.method_row_id is NOT NULL with no default.

    Read from the database rather than invented: the job ledger's whole purpose is
    that every job points at the exact method version that ran, and a test that
    fabricated a uuid would be testing a table this system does not have.
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT id FROM meta.method WHERE superseded_at IS NULL '
                    ' ORDER BY declared_at DESC LIMIT 1')
        row = cur.fetchone()
    if row is None:
        raise AssertionError('meta.method is empty, so no job row can be created; the '
                             'registry has never been synced to this database')
    return str(row[0])


def payload(tag: str, size: int = 4096) -> bytes:
    body = MARKER + tag.encode() + b'\n' + (bytes(range(256)) * (size // 256))
    return body


def store() -> artifacts_pg.PostgresArtifactStore:
    return artifacts_pg.PostgresArtifactStore(connect)


def test_the_database_REFUSES_a_mislabelled_blob():
    """POSITIVE CONTROL on app.blob's CHECK constraint.

    Everything else in this file trusts that the store cannot hold bytes that do not
    match their digest. This is the test that makes that a measurement: insert
    deliberately wrong bytes under a correct-looking digest, and demand the INSERT
    fail. If it ever succeeds, every digest in the system becomes a label rather than
    an address, and no downstream verification can detect it — because they all read
    the same wrong digest back.
    """
    data = payload('mislabelled')
    wrong = A.sha256_hex(data + b'not the same bytes')
    with connect() as conn, conn.cursor() as cur:
        try:
            cur.execute('INSERT INTO app.blob (sha256, media_type, byte_len, bytes) '
                        'VALUES (decode(%s, %s), %s, %s, %s)',
                        (wrong, 'hex', 'application/octet-stream', len(data), data))
        except psycopg.errors.CheckViolation:
            return
        except psycopg.Error as e:
            raise AssertionError(f'the insert failed, but not on the digest CHECK: '
                                 f'{type(e).__name__}: {e}') from e
        cur.execute('DELETE FROM app.blob WHERE sha256 = decode(%s, %s)',
                    (wrong, 'hex'))
        raise AssertionError(
            'app.blob accepted bytes that do not hash to their own primary key. The '
            'digest CHECK is gone or was never applied, and from this point every '
            '"content-addressed" claim in the system is unfounded — including the '
            'verification in artifacts.read, which reads the same wrong digest back '
            'and agrees with itself.')


def test_a_round_trip_returns_the_exact_bytes():
    st = store()
    data = payload('roundtrip')
    art = st.put(data, role='field.cube', metadata={'test': True})
    WRITTEN.append(art.sha256)
    assert art.id, 'no id came back, so nothing can reference this artifact'
    got, back = st.read(art.id)
    assert back == data, f'{len(back)} bytes back vs {len(data)} in'
    assert got.sha256 == art.sha256
    assert got.media_type == 'application/vnd.dirac.gaussian-cube', (
        f'the role→media-type mapping did not survive storage: {got.media_type}')
    assert got.metadata.get('test') is True, 'metadata did not round-trip'


def test_it_is_addressable_by_digest_as_well_as_by_id():
    """The digest is the identity; the uuid is a convenience. A client that has
    verified bytes holds a digest, and it must be able to re-fetch with only that."""
    st = store()
    data = payload('by-digest')
    art = st.put(data, role='field.cube')
    WRITTEN.append(art.sha256)
    _, back = st.read(f'sha256:{art.sha256}')
    assert back == data
    _, back2 = st.read(art.sha256)                      # bare digest, no scheme
    assert back2 == data


def test_storing_identical_bytes_twice_returns_the_SAME_id():
    """Enforced by UNIQUE (blob_sha256, role, encoding). Without it, a retried
    request mints an alias and the store grows with the network's reliability."""
    st = store()
    data = payload('idempotent')
    a = st.put(data, role='field.cube')
    b = st.put(data, role='field.cube')
    WRITTEN.append(a.sha256)
    assert a.id == b.id, (
        f'two ids for identical bytes in one role ({a.id} vs {b.id}) — the unique key '
        f'is not doing what the dedup path assumes')
    assert st.counters['put_deduplicated'] == 1


def test_two_roles_share_one_blob():
    """Storage cost is per distinct BYTE STRING, not per artifact. A cube reused as a
    comparison reference must not double the storage."""
    st = store()
    data = payload('two-roles')
    a = st.put(data, role='field.cube')
    b = st.put(data, role='reference.cube')
    WRITTEN.append(a.sha256)
    assert a.id != b.id and a.sha256 == b.sha256
    with connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT count(*) FROM app.blob WHERE sha256 = decode(%s, %s)',
                    (a.sha256, 'hex'))
        assert cur.fetchone()[0] == 1, 'the bytes were stored twice'
        cur.execute('SELECT count(*) FROM app.artifact WHERE blob_sha256 = decode(%s, %s)',
                    (a.sha256, 'hex'))
        assert cur.fetchone()[0] == 2, 'the two roles did not both register'


def test_head_returns_the_size_without_the_bytes():
    """The request that makes a reference worth having: 4 KB described, 0 transferred."""
    st = store()
    data = payload('head', 8192)
    art = st.put(data, role='field.cube')
    WRITTEN.append(art.sha256)
    meta = st.head(art.id)
    assert meta.size_bytes == len(data), (
        f'head reported {meta.size_bytes} for {len(data)} bytes — a client deciding '
        f'whether to download would decide on a wrong number')
    assert not hasattr(meta, 'bytes')


def test_a_byte_range_matches_the_same_slice_of_the_whole():
    """Ranges exist so a client can read a cube's HEADER (grid geometry, ~200 bytes)
    before deciding it wants the volume."""
    st = store()
    data = payload('ranged', 8192)
    art = st.put(data, role='field.cube')
    WRITTEN.append(art.sha256)
    _, chunk, rng = st.read_range(art.id, 'bytes=0-199')
    assert rng == (0, 199)
    assert chunk == data[:200], 'the range did not match the same slice of the whole'
    _, tail, rng2 = st.read_range(art.id, 'bytes=-64')
    assert tail == data[-64:]
    _, whole, none_rng = st.read_range(art.id, 'items=broken')
    assert none_rng is None and whole == data, (
        'a malformed Range cost the caller their data instead of falling back')


def test_a_missing_id_is_NOT_FOUND():
    st = store()
    try:
        st.read('00000000-0000-0000-0000-000000000000')
    except F.DiracFailure as e:
        assert e.code == 'NOT_FOUND', f'reported as {e.code}'
        return
    raise AssertionError('reading an absent artifact succeeded')


def test_an_artifact_links_to_the_job_that_produced_it():
    """The replacement for one result-id column per method. A job may emit several
    artifacts, and an artifact may belong to several jobs (a cache hit links an
    existing artifact to a new invocation without rewriting bytes)."""
    st = store()
    data = payload('job-link')
    art = st.put(data, role='field.cube')
    WRITTEN.append(art.sha256)
    with connect() as conn, conn.cursor() as cur:
        cur.execute('INSERT INTO app.job (method_row_id, state, input_sha256, request_digest, worker) '
                    "VALUES (%s, 'queued', decode(%s, %s), decode(%s, %s), 'artifact-pg-test') "
                    'RETURNING id',
                    (any_method_row(), art.sha256, 'hex', art.sha256, 'hex'))
        job_id = str(cur.fetchone()[0])
    try:
        st.link_to_job(job_id, art.id, 'field.cube')
        st.link_to_job(job_id, art.id, 'field.cube')          # must be idempotent
        with connect() as conn, conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM app.job_artifact WHERE job_id = %s',
                        (job_id,))
            n = cur.fetchone()[0]
        assert n == 1, f'{n} link rows for one (job, artifact, role) — the primary key '\
                       f'is not preventing duplicates'
    finally:
        with connect() as conn, conn.cursor() as cur:
            cur.execute('DELETE FROM app.job WHERE id = %s', (job_id,))


def test_deleting_a_job_does_not_delete_the_bytes():
    """ON DELETE CASCADE is on the job side only. Reaping a job row must not destroy
    an artifact another job may reference — and the sweep of orphaned blobs is manual
    for exactly this reason."""
    st = store()
    data = payload('cascade')
    art = st.put(data, role='field.cube')
    WRITTEN.append(art.sha256)
    with connect() as conn, conn.cursor() as cur:
        cur.execute('INSERT INTO app.job (method_row_id, state, input_sha256, request_digest, worker) '
                    "VALUES (%s, 'queued', decode(%s, %s), decode(%s, %s), 'artifact-pg-test') "
                    'RETURNING id',
                    (any_method_row(), art.sha256, 'hex', art.sha256, 'hex'))
        job_id = str(cur.fetchone()[0])
    st.link_to_job(job_id, art.id, 'field.cube')
    with connect() as conn, conn.cursor() as cur:
        cur.execute('DELETE FROM app.job WHERE id = %s', (job_id,))
    _, back = st.read(art.id)
    assert back == data, 'deleting the job destroyed the artifact bytes'


def cleanup() -> None:
    """Delete every row this run created. Test data in a real cache is worse than a
    failing test: it is a row that looks like science."""
    if not WRITTEN:
        return
    with connect() as conn, conn.cursor() as cur:
        for digest in set(WRITTEN):
            cur.execute('DELETE FROM app.artifact WHERE blob_sha256 = decode(%s, %s)',
                        (digest, 'hex'))
            cur.execute('DELETE FROM app.blob WHERE sha256 = decode(%s, %s)',
                        (digest, 'hex'))
        cur.execute("DELETE FROM app.job WHERE worker = 'artifact-pg-test'")


if __name__ == '__main__':
    try:
        for name, fn in list(globals().items()):
            if name.startswith('test_') and callable(fn):
                check(name, fn)
    finally:
        cleanup()

    print('─' * 100)
    print(f'{len(PASS)} passed · {len(FAIL)} failed')
    sys.exit(1 if FAIL else 0)
