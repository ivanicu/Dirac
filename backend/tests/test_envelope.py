#!/usr/bin/env python3
"""Contract tests for `backend/envelope.py` — the one home for the wire
envelope shared with `scripts/gen_error_codes.mjs` (TypeScript) and
`app.job_error` (Postgres, migration 007).

Same dual-mode posture as `backend/tests/test_physics_contracts.py` (read
there for why): pytest is NOT installed in `backend/env`, so this file runs
standalone too.

Run either way:
    backend/env/bin/python backend/tests/test_envelope.py
    backend/env/bin/pytest backend/tests/test_envelope.py    # if present
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys
import time
import traceback

_HERE = pathlib.Path(__file__).resolve().parent
_BACKEND = _HERE.parent
_REPO = _BACKEND.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import envelope as env                                   # noqa: E402

ERRORS_JSON = _REPO / 'contracts' / 'errors.json'
MIGRATION_007 = _REPO / 'backend' / 'db' / 'migrations' / '007_method_registry_and_job_ledger.sql'
GENERATOR = _REPO / 'scripts' / 'gen_error_codes.mjs'
GENERATED_TS = _REPO / 'src' / 'app' / 'services' / 'error-codes.ts'
EMBED_CONSUMER = (_REPO / 'src' / 'app.frontend.facets.molstar-rdkit.editable'
                  / 'index.ts')
FIELD_CONSUMER = _REPO / 'src' / 'app' / 'services' / 'dirac-client.ts'

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


# ════════════════════════════════════════════════════════════════════════════
# 1 — every code in errors.json is fully specified
# ════════════════════════════════════════════════════════════════════════════

def test_every_code_has_the_five_required_keys():
    """Re-reads the JSON directly rather than trusting `env.CODES` — envelope.py
    already asserts this at import time (an ImportError here would have
    stopped the whole file from loading), but that is ONE witness; this is a
    second, independent one reading the source file itself."""
    doc = json.loads(ERRORS_JSON.read_text(encoding='utf-8'))
    required = ('meaning', 'caller_action', 'retryable', 'http', 'user_copy')
    for code, info in doc['codes'].items():
        missing = [k for k in required if k not in info]
        assert not missing, f'{code} is missing {missing}'
        assert isinstance(info['retryable'], bool), f'{code}.retryable is not bool'
        assert isinstance(info['http'], int), f'{code}.http is not int'
        assert info['user_copy'], f'{code}.user_copy is empty'
        assert info['meaning'], f'{code}.meaning is empty'
        assert info['caller_action'], f'{code}.caller_action is empty'


def test_env_codes_matches_the_raw_json():
    doc = json.loads(ERRORS_JSON.read_text(encoding='utf-8'))
    assert env.CODES == doc['codes']


def test_error_code_enum_is_string_like_and_complete():
    assert set(m.value for m in env.ErrorCode) == set(env.CODES)
    assert env.ErrorCode.BUDGET == 'BUDGET'
    assert 'BUDGET' in env.ErrorCode
    assert 'NOT_A_REAL_CODE' not in env.ErrorCode
    assert env.ErrorCode('PARSE') is env.ErrorCode.PARSE


# ════════════════════════════════════════════════════════════════════════════
# 2 — the DB enum subset relation (errors.json's own db_enum_note, made real)
# ════════════════════════════════════════════════════════════════════════════

def _parse_job_error_enum_independently() -> set[str]:
    """A SECOND parser, written differently from envelope.py's own (which
    anchors on `CREATE TYPE app.job_error` and globs every migration file).
    This one just hunts for `job_error ... ENUM ( ... )` in migration 007
    directly. If the two disagree, one of the parsers is wrong — that is the
    point of writing it twice."""
    assert MIGRATION_007.exists(), f'{MIGRATION_007} not found — did the migration move?'
    text = MIGRATION_007.read_text(encoding='utf-8')
    m = re.search(r'job_error\s+AS\s+ENUM\s*\(([^)]*)\)', text, re.IGNORECASE | re.DOTALL)
    assert m, 'no `... job_error AS ENUM (...)` found in migration 007'
    return set(re.findall(r"'([^']+)'", m.group(1)))


def test_independent_parser_agrees_with_envelope_pys_parser():
    independent = _parse_job_error_enum_independently()
    assert independent == set(env.JOB_ERROR_ENUM), (
        f'two parsers of the same enum disagree: independent={sorted(independent)} '
        f'envelope.py={sorted(env.JOB_ERROR_ENUM)}')


def test_db_enum_is_a_subset_of_the_error_vocabulary():
    """The direction that MUST hold: every value app.job_error can carry has
    meaning in errors.json (a job whose error the frontend cannot name would
    be worse than the free-text era this file replaces)."""
    missing_from_vocabulary = env.JOB_ERROR_ENUM - set(env.CODES)
    assert not missing_from_vocabulary, (
        f'app.job_error carries codes errors.json does not declare: '
        f'{sorted(missing_from_vocabulary)}')


def test_the_reverse_does_not_hold_and_the_gap_is_exactly_named_codes():
    """The direction that must NOT hold, asserted exactly — not 'at least
    these two', but EXACTLY these two, so that the day a THIRD code appears
    in errors.json but not the enum, this test fails and someone has to look
    at it rather than let a silent third case join the two legitimate ones.

    errors.json's own db_enum_note names the reason for each: transport security
    codes are rejected before a job row can exist; OPEN_SHELL_SPIN_REQUIRED was
    added after migration 007 shipped; and DB_UNAVAILABLE / NOT_FOUND are ops
    codes that no job can carry — a failure caused by an unreachable database
    has, by construction, nowhere in that database to be written down.
    """
    only_in_vocabulary = set(env.CODES) - env.JOB_ERROR_ENUM
    assert only_in_vocabulary == {
        'BAD_HOST', 'OPEN_SHELL_SPIN_REQUIRED', 'NOT_FOUND', 'DB_UNAVAILABLE',
        'INVALID_PARAMETERS',
        'AUTH_REQUIRED', 'FORBIDDEN', 'RATE_LIMITED', 'QUOTA_EXCEEDED',
        'TLS_REQUIRED'}, (
        f'the errors.json-minus-enum set difference moved to '
        f'{sorted(only_in_vocabulary)} — re-read db_enum_note in errors.json '
        f'and update either the note, the enum, or this test, deliberately')


# ════════════════════════════════════════════════════════════════════════════
# 3 — the invariant: computed vs cache-hit meta have the SAME key set
# ════════════════════════════════════════════════════════════════════════════

# Shapes lifted directly from the real producers (field_server.py) so the
# fixture is not a strawman: `computed` mirrors field_mep() + the HTTP
# handler's own additions (field_server.py:578-640, 1208-1220); `cache_hit`
# mirrors db_get_cube()'s gasteiger branch (field_server.py:247,258) —
# verified by reading both, not invented.
_COMPUTED_MEP_META = {
    'kind': 'mep', 'units': 'kcal/mol', 'charges': 'gasteiger', 'net_charge': -1,
    'dims': [40, 44, 40], 'spacing_requested': 0.4, 'spacing': [0.41, 0.40, 0.41],
    'grid_capped': False, 'vmin': -12.34, 'vmax': 15.12, 'iso_fixed': 10,
    'pad_used_angstrom': 4.0, 'wall_max': 3.21, 'contour_closes_in_box': True,
    'total_seconds': 0.42, 'stored': True,
}
_CACHE_HIT_MEP_META = {
    'kind': 'mep', 'cache': 'db', 'computed_at': '2026-08-11T00:00:00+00:00',
    'units': 'kcal/mol', 'charges': 'gasteiger', 'method': 'gasteiger',
}


def test_positive_control_raw_metas_do_NOT_share_a_shape():
    """Before trusting assert_same_shape to CATCH a divergence, prove it can:
    the raw (un-normalized) fixtures above must actually differ, or this
    whole test file is exercising a check that cannot fail."""
    assert set(_COMPUTED_MEP_META) != set(_CACHE_HIT_MEP_META), (
        'the fixtures already agree on key set — they no longer reproduce '
        'the bug this file is testing; re-derive them from field_server.py')


def test_normalize_meta_gives_computed_and_cache_hit_the_same_shape():
    computed = env.normalize_meta(_COMPUTED_MEP_META, source='computed')
    cached = env.normalize_meta(_CACHE_HIT_MEP_META, source='db')
    env.assert_same_shape(computed, cached)          # must not raise
    assert set(computed) == env.FIELD_META_SCHEMA['mep']
    # values legitimately differ — the cache hit really doesn't know these —
    # and normalize_meta must say so with None, not by omitting the key.
    assert cached['net_charge'] is None
    assert cached['dims'] is None
    assert cached['vmin'] is None and cached['vmax'] is None
    # values that WERE present must survive untouched.
    assert cached['net_charge'] is None and computed['net_charge'] == -1
    assert computed['cache'] == 'computed' and cached['cache'] == 'db'


def test_negative_control_assert_same_shape_catches_a_truncated_meta():
    """A DELIBERATELY truncated meta (as if a producer forgot half its keys)
    must be caught, not silently accepted — this is the failure mode
    normalize_meta exists to close, reproduced directly against the checker
    rather than through a producer."""
    full = env.normalize_meta(_COMPUTED_MEP_META, source='computed')
    truncated = dict(full)
    del truncated['net_charge']
    del truncated['dims']
    try:
        env.assert_same_shape(full, truncated)
    except AssertionError as e:
        assert 'net_charge' in str(e) and 'dims' in str(e)
    else:
        raise AssertionError('assert_same_shape did not catch a truncated meta')


def test_normalize_meta_rejects_an_undeclared_key():
    """A typo'd or new key must fail loudly at the one home, not ship
    silently and drift the schema out from under normalize_meta."""
    bad = dict(_COMPUTED_MEP_META)
    bad['nte_charge'] = -1          # the typo
    try:
        env.normalize_meta(bad, source='computed')
    except ValueError as e:
        assert 'nte_charge' in str(e)
    else:
        raise AssertionError('normalize_meta accepted an undeclared key')


def test_normalize_meta_rejects_an_unknown_kind():
    try:
        env.normalize_meta({'kind': 'not_a_real_kind'}, source='computed')
    except ValueError as e:
        assert 'not_a_real_kind' in str(e)
    else:
        raise AssertionError('normalize_meta accepted an unknown kind')


def test_units_always_present_including_the_quantum_kinds_that_omit_it_today():
    """The bug named in the task: field_quantum()'s real meta (field_server.py
    :962-972) never sets 'units'. Reproduced here with a meta shaped exactly
    like that function's output, then closed by normalize_meta."""
    homo_meta_as_the_real_producer_builds_it = {
        'kind': 'homo', 'basis': 'sto-3g', 'method': 'RHF', 'scf_energy_ha': -1.2,
        'converged': True, 'charge': 0, 'spin': 0, 'natoms': 5, 'nbasis': 20,
        'ecp': {}, 'scf_seconds': 1.1, 'scf_cycles': 9, 'homo_ev': -9.1,
        'lumo_ev': 2.3, 'cube_seconds': 0.3, 'cube_predicted_seconds': 0.5,
        'total_seconds': 1.5,
    }
    assert 'units' not in homo_meta_as_the_real_producer_builds_it, (
        'fixture drifted — field_quantum meta now sets units; re-check '
        'field_server.py:962-972 and update this fixture deliberately')
    normalized = env.normalize_meta(homo_meta_as_the_real_producer_builds_it,
                                    source='computed')
    assert normalized['units'] == env.UNITS_BY_KIND['homo'] == 'amp'

    for kind in ('mep', 'mep_qm', 'homo', 'lumo', 'density', 'mlp'):
        assert kind in env.UNITS_BY_KIND and env.UNITS_BY_KIND[kind], (
            f'{kind} has no recorded unit')


# ════════════════════════════════════════════════════════════════════════════
# 4 — ok() / err() / to_v1(): the v1 shape the LIVE frontend actually reads
# ════════════════════════════════════════════════════════════════════════════

def _payload_keys_read_in(path: pathlib.Path) -> set[str]:
    assert path.exists(), f'{path} not found — did the consumer move?'
    source = path.read_text(encoding='utf-8')
    variable = 'v1' if path == FIELD_CONSUMER else 'payload'
    return set(re.findall(rf'{variable}\.(\w+)', source))


def test_live_consumers_read_exactly_the_keys_this_test_assumes():
    """Grounds the assertions below in the ACTUAL files, not a hardcoded
    belief about them — if a live session edits either consumer, this test
    re-verifies against the new reality instead of quietly going stale."""
    assert _payload_keys_read_in(EMBED_CONSUMER) == {'ok', 'molfile', 'meta', 'error'}
    assert _payload_keys_read_in(FIELD_CONSUMER) == {'ok', 'error', 'reason', 'cube', 'meta'}


def test_to_v1_success_shape_matches_the_embed_consumer():
    envelope = env.ok({'molfile': 'MOLBLOCK'}, {'kind': 'embed', 'natoms': 5})
    v1 = env.to_v1(envelope)
    assert set(v1) == {'ok', 'molfile', 'meta'}
    assert v1['ok'] is True and v1['molfile'] == 'MOLBLOCK'
    # v2-only bookkeeping must not leak into the v1 meta.
    assert not ({'envelope', 'request_id', 'producer'} & set(v1['meta']))


def test_to_v1_success_shape_matches_the_field_consumer():
    envelope = env.ok({'cube': 'CUBEDATA'},
                      env.normalize_meta(_COMPUTED_MEP_META, source='computed'))
    v1 = env.to_v1(envelope)
    assert set(v1) == {'ok', 'cube', 'meta'}
    assert v1['cube'] == 'CUBEDATA'
    assert v1['meta']['net_charge'] == -1, 'domain meta must survive the flatten'


def test_to_v1_failure_shape_is_a_string_error_plus_optional_reason():
    status, envelope = env.err('BUDGET', 'took too long')
    assert status == env.CODES['BUDGET']['http'] == 200
    v1 = env.to_v1(envelope)
    assert isinstance(v1['error'], str) and v1['error'] == 'took too long', (
        'the live FieldRefusal constructor does `new FieldRefusal(payload.error, ...)` '
        'and treats it as an Error message — it must be a STRING, not the v2 object')
    assert v1['reason'] == 'budget'
    assert set(v1) <= (_payload_keys_read_in(EMBED_CONSUMER)
                       | _payload_keys_read_in(FIELD_CONSUMER) | {'ok'})


def test_to_v1_reason_bucket_covers_every_code_and_only_budget_maps_to_budget():
    for code in env.CODES:
        _, envelope = env.err(code, f'refused: {code}')
        v1 = env.to_v1(envelope)
        assert (v1.get('reason') == 'budget') == (code == 'BUDGET'), (
            f'{code} must map to the budget reason iff it IS budget — the '
            'live UI offers a bigger-budget retry only on that branch '
            '(facets/field-wells/index.ts:654-659)')


def test_err_uses_the_http_status_from_errors_json_not_always_200():
    assert env.err('TOO_LARGE', 'x')[0] == 413
    assert env.err('BAD_HOST', 'x')[0] == 403
    assert env.err('PARSE', 'x')[0] == 200


def test_err_auto_hint_from_points_at_and_manual_override():
    _, envelope = env.err('UNPARAMETERIZED', 'cannot parameterize P')
    assert envelope['error']['hint'] == 'fields.qm.mep_qm'
    _, envelope2 = env.err('UNPARAMETERIZED', 'x', hint='override')
    assert envelope2['error']['hint'] == 'override'


def test_err_rejects_an_unknown_code():
    try:
        env.err('NOT_A_REAL_CODE', 'x')
    except ValueError as e:
        assert 'NOT_A_REAL_CODE' in str(e)
    else:
        raise AssertionError('err() accepted an unknown code')


def test_ok_does_not_mutate_its_inputs_and_stamps_request_id():
    data_in = {'cube': 'X'}
    meta_in = {'kind': 'mep'}
    envelope = env.ok(data_in, meta_in)
    assert data_in == {'cube': 'X'} and meta_in == {'kind': 'mep'}, (
        'ok() must copy, not alias, its arguments')
    assert envelope['meta']['envelope'] == 2
    assert re.fullmatch(r'[0-9a-f]{12}', envelope['meta']['request_id'])
    assert envelope['meta']['producer'] is None


def test_request_ids_are_not_constant():
    ids = {env.new_request_id() for _ in range(20)}
    assert len(ids) == 20


# ════════════════════════════════════════════════════════════════════════════
# 5 — the generated TS file is in sync with contracts/errors.json
# ════════════════════════════════════════════════════════════════════════════

def test_generated_ts_matches_a_fresh_run_of_the_generator():
    """A generator that has drifted from its own committed output is worse
    than no generator — it launders staleness into the appearance of
    currency. Byte-compare, not a structural diff, because the point
    includes formatting/ordering drift, not only content drift."""
    if not GENERATED_TS.exists():
        raise AssertionError(f'{GENERATED_TS} does not exist — run '
                             f'`node {GENERATOR.relative_to(_REPO)}` and commit it')
    committed = GENERATED_TS.read_bytes()

    tmp_dir = pathlib.Path(
        __import__('tempfile').mkdtemp(prefix='dirac_gen_error_codes_'))
    tmp_out = tmp_dir / 'error-codes.ts'
    try:
        result = subprocess.run(
            ['node', str(GENERATOR), str(tmp_out)],
            cwd=str(_REPO), capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        skip('node is not on PATH — cannot verify generator sync')
        return
    if result.returncode != 0:
        raise AssertionError(
            f'generator exited {result.returncode}\nstdout: {result.stdout}\n'
            f'stderr: {result.stderr}')
    fresh = tmp_out.read_bytes()
    assert fresh == committed, (
        f'{GENERATED_TS.relative_to(_REPO)} has drifted from a fresh run of '
        f'{GENERATOR.relative_to(_REPO)} — regenerate and commit it')


def test_generated_ts_is_deterministic_across_two_runs():
    tmp_dir = pathlib.Path(
        __import__('tempfile').mkdtemp(prefix='dirac_gen_error_codes_det_'))
    out_a, out_b = tmp_dir / 'a.ts', tmp_dir / 'b.ts'
    for out in (out_a, out_b):
        result = subprocess.run(['node', str(GENERATOR), str(out)],
                                cwd=str(_REPO), capture_output=True, text=True,
                                timeout=60)
        assert result.returncode == 0, result.stderr
    assert out_a.read_bytes() == out_b.read_bytes()


def test_generated_ts_declares_every_code_and_only_those_three_fields():
    src = GENERATED_TS.read_text(encoding='utf-8')
    for code in env.CODES:
        assert re.search(rf'\b{code}\s*:\s*{{', src), f'{code} missing from generated TS'
    assert 'export const ERROR_CODES' in src
    assert 'export type ErrorCode' in src
    # a spot check that the three named fields are what got emitted, not a
    # renamed or reshuffled set that would still "contain the code name".
    assert 'user_copy:' in src and 'retryable:' in src and 'points_at:' in src


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

    print(f'envelope contracts — {len(tests)} tests, '
          f'pytest {"present" if _HAVE_PYTEST else "ABSENT (standalone mode)"}')
    print('─' * 100)
    passed = failed = skipped = 0
    failures: list[tuple[str, str]] = []

    for fn in tests:
        name = fn.__name__
        t0 = time.time()
        try:
            fn()
        except Skipped as e:
            print(f'SKIP    {name:<70}        {e}')
            skipped += 1
            continue
        except AssertionError:
            dt = time.time() - t0
            print(f'FAIL    {name:<70} {dt:6.2f}s')
            failed += 1
            failures.append((name, traceback.format_exc()))
            continue
        except Exception:
            dt = time.time() - t0
            print(f'ERROR   {name:<70} {dt:6.2f}s')
            failed += 1
            failures.append((name, traceback.format_exc()))
            continue
        dt = time.time() - t0
        print(f'PASS    {name:<70} {dt:6.2f}s')
        passed += 1

    print('─' * 100)
    print(f'{passed} passed · {skipped} skipped · {failed} failed')
    for name, tb in failures:
        print(f'\n══ {name} ' + '═' * (96 - len(name)))
        print(tb)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
