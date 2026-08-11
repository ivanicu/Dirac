"""Dirac wire envelope — ONE home, in code, for what `contracts/errors.json`
only writes in prose.

Before this file: three vocabularies that did not agree (see errors.json's
own `$comment`) — the service's free-text `reason` strings, the DB's
`app.job_error` enum, and the frontend's inability to branch on either. This
module is the Python half of the fix. `scripts/gen_error_codes.mjs` is the
TypeScript half; both read `contracts/errors.json` and neither hand-copies it.

PURE by design: no HTTP import, no DB driver import. `backend/field_server.py`
(HTTP) and `backend/db/*` (Postgres) may both import this; it may import
neither. The migration-chain parse below reads a `.sql` file as TEXT, which is
not a DB import — the distinction is "no live connection", not "no schema
awareness".

NOT WIRED YET, deliberately (same posture as `src/app/services/ligand-store.ts`):
`field_server.py` is being edited by other sessions right now, and this module
is the seam they will import. It is standalone, typechecked by nothing but
pytest, and tested on its own.
"""
from __future__ import annotations

import enum
import json
import pathlib
import re
import uuid

_HERE = pathlib.Path(__file__).resolve().parent
_REPO = _HERE.parent
ERRORS_JSON_PATH = _REPO / 'contracts' / 'errors.json'
_MIGRATIONS_DIR = _HERE / 'db' / 'migrations'

# ── load the vocabulary ONCE at import ──────────────────────────────────────

with ERRORS_JSON_PATH.open(encoding='utf-8') as _fh:
    _ERRORS_DOC: dict = json.load(_fh)

if _ERRORS_DOC.get('version') != 1:
    # A version bump is a real contract change, not a detail — an envelope.py
    # written against v1 must not silently keep serving under a v2 document.
    raise ImportError(
        f'{ERRORS_JSON_PATH} is version {_ERRORS_DOC.get("version")!r}; '
        'backend/envelope.py is written against version 1 — re-derive this '
        'module against the new document before trusting it.')

CODES: dict[str, dict] = _ERRORS_DOC['codes']

_REQUIRED_CODE_KEYS = ('meaning', 'caller_action', 'retryable', 'http', 'user_copy')
for _code, _info in CODES.items():
    _missing_keys = [k for k in _REQUIRED_CODE_KEYS if k not in _info]
    if _missing_keys:
        raise ImportError(
            f'contracts/errors.json code {_code!r} is missing {_missing_keys} — '
            'every code must carry meaning/caller_action/retryable/http/user_copy')

# A str-mixin Enum: `ErrorCode.BUDGET == "BUDGET"`, `"BUDGET" in ErrorCode`,
# `ErrorCode("BUDGET")` all work, and it is built FROM the JSON — there is no
# separate hand-typed list to drift out from under it (that was incident zero
# in errors.json's own $comment: three vocabularies, none hand-in-hand).
ErrorCode = enum.Enum('ErrorCode', {code: code for code in CODES}, type=str)


def _job_error_enum_from_migrations() -> tuple[frozenset[str], pathlib.Path]:
    """Parse `CREATE TYPE app.job_error AS ENUM (...)` out of the migration
    CHAIN (git, not a live database — this module must import with Postgres
    down). A later migration that redefines the type would win; today there
    is exactly one `CREATE TYPE`, in 007, and none since."""
    pattern = re.compile(
        r'CREATE\s+TYPE\s+app\.job_error\s+AS\s+ENUM\s*\(([^)]*)\)',
        re.IGNORECASE | re.DOTALL)
    found: tuple[frozenset[str], pathlib.Path] | None = None
    for path in sorted(_MIGRATIONS_DIR.glob('*.sql')):
        m = pattern.search(path.read_text(encoding='utf-8'))
        if m:
            found = (frozenset(re.findall(r"'([^']*)'", m.group(1))), path)
    if found is None:
        raise ImportError(
            f'no `CREATE TYPE app.job_error` found under {_MIGRATIONS_DIR} — '
            'the DB-enum subset check below has nothing to check against.')
    return found


JOB_ERROR_ENUM, _JOB_ERROR_SOURCE = _job_error_enum_from_migrations()

# THE assertion errors.json's own `db_enum_note` promises exists: app.job_error
# is a SUBSET of this vocabulary (it omits BAD_HOST — rejected before a job
# exists — and OPEN_SHELL_SPIN_REQUIRED — added after 007 shipped). The
# reverse must NOT hold, so this checks one direction only. A code the DB can
# carry but this file cannot name would mean a job fails with an error nothing
# can render.
_enum_not_in_vocabulary = JOB_ERROR_ENUM - set(CODES)
if _enum_not_in_vocabulary:
    raise ImportError(
        f'app.job_error ({_JOB_ERROR_SOURCE.name}) carries '
        f'{sorted(_enum_not_in_vocabulary)}, which {ERRORS_JSON_PATH} does '
        'not declare. The DB enum is a SUBSET of the error vocabulary by '
        'contract (errors.json db_enum_note) — this has drifted.')


def new_request_id() -> str:
    """A short id so a slow field can be tied to one line in the daemon's
    log. 12 hex chars of a uuid4 is 48 bits — collision is not the threat
    model here, grep-ability is."""
    return uuid.uuid4().hex[:12]


# ── FieldMeta: the units table that replaces the frontend's second one ─────
#
# contracts/iface.pyi says `units: str  # ALWAYS present (classical AND
# quantum)`. Verified against backend/field_server.py: true for field_mep
# (kcal/mol) and field_mlp ('MLP (Crippen/Fauchère)'), FALSE for
# field_quantum — the meta dict built at field_server.py:962-972 never sets
# 'units' at all. The frontend's Kinds table (facets/field-wells/index.ts)
# keeps its own per-kind `unit` string as a workaround. This dict is the one
# home; normalize_meta() below backfills it whenever the producer forgot.
#
# These are the NATIVE units of the values written into the cube, not the
# frontend's DISPLAY units — mep_qm's cube is Ha/e (pyscf cubegen.mep, atomic
# units) while the frontend's isovalue slider shows kcal/mol via its own
# `cubeScale` conversion (1/627.5094740631). That conversion is a rendering
# choice and stays in the frontend; this dict answers a different question —
# "what is actually in the file" — which is also the more useful fact for a
# 'Units' row that is supposed to describe the data, not the widget.
UNITS_BY_KIND: dict[str, str] = {
    'mep': 'kcal/mol',
    'mep_qm': 'Ha/e',
    'homo': 'amp',
    'lumo': 'amp',
    'density': 'e/Bohr³',
    'mlp': 'MLP (Crippen/Fauchère)',
}

# Every key EITHER path (computed or cache-hit) may set for a given kind,
# read off the real producers:
#   · field_mep / field_mlp (grid fields, field_server.py:578-703)
#   · field_quantum (field_server.py:942-1027)
#   · db_get_cube (field_server.py:225-262) — the cache-hit path, which sets
#     a visibly SMALLER subset today. That gap is the bug normalize_meta()
#     exists to close: filled with None, never silently absent.
_COMMON = ('kind', 'units', 'method', 'cache', 'stored', 'computed_at', 'total_seconds')
_GRID = ('dims', 'spacing_requested', 'spacing', 'grid_capped', 'vmin', 'vmax',
         'iso_fixed', 'pad_used_angstrom', 'wall_max', 'contour_closes_in_box',
         # The isovalue the BOX was sized for, beside the isovalue actually
         # drawn (`iso_fixed`). Two numbers because they can disagree: a box
         # grown to close a contour at one level is not evidence about another.
         'iso_sized_for')
_QUANTUM = ('basis', 'scf_energy_ha', 'converged', 'charge', 'spin', 'natoms',
            'nbasis', 'ecp', 'scf_seconds', 'scf_cycles', 'homo_ev', 'lumo_ev',
            'cube_seconds', 'cube_predicted_seconds',
            # A number's SCOPE travels with the number or it does not travel.
            # STO-3G ranks nitrobenzene as more electron-rich than benzene
            # (def2-SVP ranks it last) and moves water's LUMO by 12 eV, so
            # 'homo_ev' at a minimal basis has no referent — this key is how
            # the panel knows to print "not quotable at this level" instead of
            # a confident one-decimal figure.
            'frontier_caveat')

# Classical-model scope, same principle one instrument down: a spherical point
# charge cannot represent a sigma-hole AT ALL. Measured inside this one app,
# bromobenzene at the cap: Gasteiger -6.2 kcal/mol vs the QM surface route
# +9.9 — opposite signs, ~16 kcal/mol apart. The flag exists so the UI routes
# that question to the instrument that can answer it.
_CLASSICAL_CAVEAT = ('sigma_hole_representable', 'model_caveat')

# SOURCE and FRAME as separate arguments (/field/region): the sources are an
# arbitrary atom set with caller-supplied weights, the frame is the caller's
# box. Both facts are meta, and the second one is a REFUSAL made legible —
# because the frame is not ours, this route cannot grow the box to close a
# contour the way the ligand path does, so it reports instead of fixing.
_REGION = ('n_sources_sent', 'n_sources_used', 'cutoff_angstrom',
           'frame_is_callers',
           # WHERE THE CHARGES CAME FROM, which for a group field is the whole
           # question: the field is additive but the charge model is not, so a
           # truncated pocket charged per-molecule is not the intact protein.
           # 'caller-supplied' vs a residue-template source are different
           # claims and must not both render as an unqualified number.
           'charge_model',
           # A REFUSAL, counted and explained. Crystallographic waters are left
           # OUT because their hydrogens were never resolved: a bare oxygen
           # contributes a fictitious monopole, and an invented orientation
           # points the dipole confidently wrong. The count travels so the
           # caller can see how much was excluded, not just that something was.
           'waters_excluded', 'waters_note')

FIELD_META_SCHEMA: dict[str, frozenset[str]] = {
    'mep': frozenset(_COMMON + _GRID + _CLASSICAL_CAVEAT
                     + ('charges', 'net_charge')),
    # NOT given _CLASSICAL_CAVEAT: field_mlp() emits neither key (checked, not
    # assumed), and declaring a key a producer never sets would make the panel
    # render "model_caveat: not recorded" for a model whose caveat is simply a
    # different one. A schema that over-declares teaches the UI to expect
    # silence.
    'mlp': frozenset(_COMMON + _GRID + ('total_logp', 'single_signed')),
    # The region route's kinds. Declared here even though that route does not
    # yet normalise its own exit, because FIELD_META_SCHEMA is the ONE home for
    # what a field's meta may contain — a kind the schema has never heard of
    # makes normalize_meta raise 'unknown field kind', so an undeclared kind is
    # a route that can never adopt the shared shape.
    'mep_region': frozenset(_COMMON + _GRID + _REGION + ('net_charge',)),
    'mlp_region': frozenset(_COMMON + _GRID + _REGION + ('net_charge',)),
    'mep_qm': frozenset(_COMMON + _QUANTUM),
    'homo': frozenset(_COMMON + _QUANTUM),
    'lumo': frozenset(_COMMON + _QUANTUM),
    'density': frozenset(_COMMON + _QUANTUM),
}


def normalize_meta(meta: dict, *, source: str) -> dict:
    """Return a NEW meta dict with exactly the declared keys for `meta['kind']`
    — present keys kept, absent ones filled with `None` — so a cache hit and
    a fresh compute of the SAME field kind are indistinguishable in SHAPE
    (never claimed to be indistinguishable in VALUE: a cache row genuinely
    does not know `charge`/`spin`/`ecp` today, and None says so honestly
    instead of the key just not existing).

    `source` becomes `meta['cache']` — one of iface.d.ts's CacheSource values
    ('browser'|'memory'|'db'|'computed'); this function does not validate
    which, that is the caller's contract to keep.

    Raises ValueError on an unknown kind, or on a key `meta` carries that the
    schema does not declare — a typo in a producer should fail loudly here,
    not ship a field whose meta silently drifted from every other kind's.
    """
    kind = meta.get('kind')
    schema = FIELD_META_SCHEMA.get(kind)
    if schema is None:
        raise ValueError(
            f'normalize_meta: unknown field kind {kind!r} — not one of '
            f'{sorted(FIELD_META_SCHEMA)} in FIELD_META_SCHEMA')
    undeclared = set(meta) - schema
    if undeclared:
        raise ValueError(
            f'normalize_meta: {kind!r} meta carries undeclared key(s) '
            f'{sorted(undeclared)} — add them to FIELD_META_SCHEMA (this is '
            'the one home; a silently-dropped key is worse than a loud one)')

    out = {key: meta.get(key) for key in schema}
    out['cache'] = source
    # The units fix: never leave it unset when the answer is already known.
    if out.get('units') is None:
        out['units'] = UNITS_BY_KIND.get(kind)
    return out


def assert_same_shape(meta_a: dict, meta_b: dict) -> None:
    """Raise AssertionError naming the exact keys that differ. Deliberately a
    key-SET comparison, not a value comparison — that is the invariant this
    file exists to hold (see normalize_meta's docstring on shape vs value)."""
    keys_a, keys_b = set(meta_a), set(meta_b)
    if keys_a != keys_b:
        only_a = sorted(keys_a - keys_b)
        only_b = sorted(keys_b - keys_a)
        raise AssertionError(
            'meta key sets differ: '
            f'only in first={only_a} only in second={only_b}')


# ── the envelope itself ──────────────────────────────────────────────────────
#
# v2, per SPEC.md §4.6: `{ok, data, meta{envelope:2, request_id, producer}}`.
# `meta` here is exactly today's FieldMeta/EmbedMeta with three bookkeeping
# keys folded in — `producer` is v2's answer to SPEC.md §4.6's stated v1 gap
# ("producer stamp lives in DB row ... not in wire meta on cache miss path").
#
# v1 is what `facets/field-wells/index.ts` reads TODAY (verified: `payload.ok`,
# `payload.cube`, `payload.molfile`, `payload.meta`, `payload.error`,
# `payload.reason` — all flat, `reason` even though contracts/iface.d.ts's
# `Envelope` type does not declare it). Building v1 is opt-OUT: `ok()`/`err()`
# always build v2; a caller that needs the live wire shape calls `to_v1()`
# explicitly. Breaking the live client is not acceptable, so this direction
# is the safe default — a caller who forgets to call `to_v1()` gets a LOUDER
# shape, not a silently wrong flat one.

def ok(data: dict, meta: dict) -> dict:
    """Build a v2 success envelope. `data` is the domain payload (`cube` or
    `molfile` today); `meta` is the domain metadata, unmodified except for
    three envelope-level keys."""
    out_meta = dict(meta)
    out_meta['envelope'] = 2
    out_meta['request_id'] = new_request_id()
    out_meta.setdefault('producer', None)
    return {'ok': True, 'data': dict(data), 'meta': out_meta}


def err(code: str, message: str, *, hint: str | None = None,
        detail: dict | None = None) -> tuple[int, dict]:
    """Build a v2 failure envelope. Returns `(http_status, body)` — the
    status comes from errors.json, so a caller cannot send 200 for a 413 by
    forgetting a literal.

    `hint` overrides the code's own `points_at` when given; otherwise
    `points_at` (e.g. UNPARAMETERIZED -> 'fields.qm.mep_qm') is used
    automatically, because a refusal should name the working alternative
    without every call site having to remember to ask for it.
    """
    info = CODES.get(code)
    if info is None:
        raise ValueError(f'unknown error code {code!r} — not one of {sorted(CODES)}')

    error: dict = {
        'code': code,
        'message': message,
        'user_copy': info['user_copy'],
        'retryable': info['retryable'],
    }
    resolved_hint = hint if hint is not None else info.get('points_at')
    if resolved_hint is not None:
        error['hint'] = resolved_hint
    if detail is not None:
        error['detail'] = detail

    body = {
        'ok': False,
        'error': error,
        'meta': {'envelope': 2, 'request_id': new_request_id()},
    }
    return info['http'], body


# v1's error path is free-text `reason` in {'budget','unsupported','internal',
# 'network'} (facets/field-wells/index.ts FieldRefusal; 'network' is a
# client-only value for a fetch that never got a response and never appears
# here). Only 'budget' is actually branched on today (offers a bigger-budget
# retry); everything else renders the message verbatim. The mapping below is
# a deliberately LOSSY bucketing of the real ten-code vocabulary into that
# three-value one — lossy is the point: v1 could not distinguish these, v2
# can (the caller has `error.code`), and to_v1() must not invent precision
# the live client never had.
_V1_REASON_FOR_CODE: dict[str, str] = {
    'PARSE': 'unsupported',
    'UNCONVERGED': 'unsupported',
    'UNPARAMETERIZED': 'unsupported',
    'BUDGET': 'budget',
    'OPEN_SHELL_SPIN_REQUIRED': 'unsupported',
    'UNSUPPORTED': 'unsupported',
    'TOO_LARGE': 'unsupported',
    'BAD_HOST': 'internal',
    'CANCELLED': 'internal',
    'INTERNAL': 'internal',
    # Ops codes (added with the admin router). v1's three-word reason vocabulary
    # has no bucket for infrastructure, so both land in 'internal' — which is
    # exactly the flattening that made v1 useless for reacting to a failure, and
    # exactly why v2 carries the code itself. The lossiness is the argument.
    'NOT_FOUND': 'internal',
    'DB_UNAVAILABLE': 'internal',
}
assert set(_V1_REASON_FOR_CODE) == set(CODES), (
    'the v1 reason bucket table has drifted from the vocabulary it buckets')

_V2_ONLY_META_KEYS = ('envelope', 'request_id', 'producer')


def to_v1(envelope: dict) -> dict:
    """Flatten a v2 envelope into exactly today's live shape.

    Success: `{ok: true, cube?, molfile?, meta}` — `data`'s keys float up to
    top level, and the three v2-only meta keys are stripped (v1 never had
    them; leaking them is not "exactly today's shape").

    Failure: `{ok: false, error: string}` with `reason` added when the code
    maps to one — dropping `reason` entirely would make every refusal read
    as 'internal' client-side (`payload.reason ?? 'internal'`), silently
    erasing the budget-vs-everything-else distinction the live UI depends on.
    """
    if envelope.get('ok'):
        meta = {k: v for k, v in envelope.get('meta', {}).items()
                 if k not in _V2_ONLY_META_KEYS}
        out: dict = {'ok': True, 'meta': meta}
        out.update(envelope.get('data', {}))
        return out

    error = envelope.get('error', {})
    out = {'ok': False, 'error': error.get('message', '')}
    reason = _V1_REASON_FOR_CODE.get(error.get('code'))
    if reason is not None:
        out['reason'] = reason
    return out
