"""Artifact identity, addressing and the inline-or-reference decision.

WHAT AN ARTIFACT IS, and why it is not just "a blob": bytes plus the ROLE they play
plus the media type that says how to read them. `app.blob` owns the bytes and
enforces `digest(bytes,'sha256') = sha256`, so it cannot hold a mislabelled payload.
What it cannot say is that a particular blob is the `field.cube` of a particular
invocation — and a reference is only usable if it says what it refers TO.

THE DEFECT THIS FIXES, which is in the live response right now: a 2.5 MB Gaussian
cube travels as a JSON string field. Consequences, in the order they hurt:

  · an MCP tool result carrying that as base64 spends the conversation's entire
    context on bytes no model will ever read
  · a CLI cannot stream it, cannot resume it, and cannot VERIFY it — you cannot
    check the digest of something that has no digest
  · the frontend re-downloads the whole thing to change an isosurface level
  · and app.job grows one column per result kind: field_cube_id today, then
    docking_pose_id, fep_result_id, md_trajectory_id, forever

THE RULE, stated once here because three transports will implement it:

    small enough  → the bytes travel INLINE, base64, in the response
    otherwise     → a REFERENCE travels, and the bytes are fetched by digest

Both carry the same `sha256`, so a client's verification code is identical either
way. That symmetry is the whole design: `dirac artifacts verify <id>` must not care
which path the bytes arrived by, or the CLI would have two behaviours and one of
them would be untested.

DEPENDENCY DIRECTION (ADR-001, gate 11): this module imports the standard library
and `failures`. No psycopg, no HTTP, no RDKit, no pyscf. The Postgres implementation
lives in artifacts_pg.py precisely so that a CLI running against a local file, an
SDK talking to a remote server, and a test running on a bare interpreter all share
this arithmetic and none of them inherits a database driver.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

import failures

# ── the inline threshold ────────────────────────────────────────────────────────
#
# 48 KiB, and the number is a measurement rather than a preference:
#
#   water/sto-3g HOMO cube on the default grid   ~46 KB   → inline
#   ethanol MEP cube                             ~2.5 MB  → reference
#   a molfile for a drug-sized ligand            ~4 KB    → inline
#
# So the common interactive case (a small molecule's field, which the browser wants
# immediately and would otherwise pay a second round-trip for) stays in the
# response, and everything that would poison an agent's context does not. Base64
# inflates by 4/3, so 48 KiB of cube is 64 KiB on the wire — under the 100 KB most
# MCP hosts will tolerate for one tool result, with room for the rest of the
# envelope.
INLINE_MAX_BYTES = 48 * 1024

# The ceiling a client may request. Without it, `inline_max=999999999` would let any
# caller undo the decision above and re-create the original defect through a query
# parameter.
INLINE_REQUEST_CEILING = 512 * 1024

# ── media types ────────────────────────────────────────────────────────────────
#
# Vendor types for the formats that have no registered one. A Gaussian cube served
# as text/plain is a cube a client has to sniff, and sniffing is guessing.
MEDIA_TYPES: dict[str, str] = {
    'field.cube': 'application/vnd.dirac.gaussian-cube',
    'molecule.molfile': 'chemical/x-mdl-molfile',
    'molecule.sdf': 'chemical/x-mdl-sdfile',
    'molecule.pdb': 'chemical/x-pdb',
    'scf.log': 'text/plain; charset=utf-8',
    'method.provenance': 'application/json',
    'grid.points': 'application/vnd.dirac.grid-points+json',
}

_SHA256_RE = re.compile(r'^[0-9a-f]{64}$')
_ROLE_RE = re.compile(r'^[a-z][a-z0-9]*(\.[a-z][a-z0-9_]*)*$')


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_digest(value: str) -> bool:
    return bool(_SHA256_RE.match(value or ''))


def verify_bytes(data: bytes, expected_sha256: str) -> None:
    """Raise if the bytes are not what the digest says.

    Called on every read path, including the one that just wrote them. That looks
    redundant and is not: the reason to content-address at all is that the check is
    cheap enough to run always, and a verification that only runs when someone
    suspects a problem is a verification that has never run.
    """
    actual = sha256_hex(data)
    if actual != expected_sha256:
        raise failures.DiracInternal(
            f'artifact digest mismatch: stored under {expected_sha256[:12]}… but the '
            f'{len(data)} bytes hash to {actual[:12]}…. Serving them would hand a '
            f'client bytes it cannot verify, which defeats the only guarantee a '
            f'content-addressed store makes.')


@dataclass(frozen=True)
class Artifact:
    """A stored artifact's identity. Deliberately does NOT hold the bytes.

    Holding them would make the object's memory cost proportional to the payload,
    and the entire point is to describe 2.5 MB without carrying it. `read()` on the
    store is where bytes appear.
    """

    sha256: str
    role: str
    media_type: str
    size_bytes: int
    encoding: str = 'identity'
    id: str | None = None                 # assigned by the store, None until stored
    metadata: dict[str, Any] = field(default_factory=dict)
    method_version: str | None = None

    def __post_init__(self) -> None:
        if not is_digest(self.sha256):
            raise failures.DiracInternal(
                f'artifact sha256 {self.sha256!r} is not 64 lowercase hex digits; an '
                f'address that is not well-formed cannot be looked up, and accepting '
                f'it here would push the failure to whoever tries')
        if not _ROLE_RE.match(self.role):
            raise failures.DiracInternal(
                f'artifact role {self.role!r} is not a dotted lowercase path such as '
                f'"field.cube". Roles are a vocabulary clients switch on, so free '
                f'text here would become "cube" / "Cube" / "field_cube" within a week')
        if self.size_bytes < 0:
            raise failures.DiracInternal(f'negative size_bytes {self.size_bytes}')

    # The address a client uses. `id` is a convenience; the DIGEST is the identity,
    # and a client that has the digest needs nothing else to fetch or verify.
    @property
    def address(self) -> str:
        return self.id or f'sha256:{self.sha256}'

    def to_reference(self, *, inline: bytes | None = None,
                     base_path: str = '/v2/artifacts') -> dict[str, Any]:
        """The `artifacts[]` entry of a v2 envelope.

        Identical whether the bytes came inline or not, except for the presence of
        `inline_base64` — so client code branches in exactly one place, on exactly
        one key, and both branches end at the same digest check.
        """
        ref: dict[str, Any] = {
            'id': self.id,
            'sha256': self.sha256,
            'role': self.role,
            'media_type': self.media_type,
            'size_bytes': self.size_bytes,
            'encoding': self.encoding,
            'url': f'{base_path}/{self.address}',
            'metadata_url': f'{base_path}/{self.address}/metadata',
        }
        if self.method_version:
            ref['method_version'] = self.method_version
        if self.metadata:
            ref['metadata'] = self.metadata
        if inline is not None:
            verify_bytes(inline, self.sha256)
            ref['inline_base64'] = base64.b64encode(inline).decode('ascii')
            ref['inline'] = True
        else:
            ref['inline'] = False
        return ref


def should_inline(size_bytes: int, *, requested_max: int | None = None) -> bool:
    """Whether these bytes travel in the response.

    A client may LOWER the threshold freely (an agent that wants nothing inline
    passes 0) and may raise it only up to INLINE_REQUEST_CEILING. Asymmetric on
    purpose: lowering it is a client protecting its own context, raising it is a
    client re-introducing the defect this module exists to remove.
    """
    limit = INLINE_MAX_BYTES if requested_max is None else max(0, int(requested_max))
    limit = min(limit, INLINE_REQUEST_CEILING)
    return size_bytes <= limit


def decode_inline(ref: dict[str, Any]) -> bytes:
    """The client half, so an SDK and a CLI cannot disagree about it.

    Verifies the digest before returning — a client that trusts `inline_base64`
    without checking has a content-addressed API and no content addressing.
    """
    if not ref.get('inline_base64'):
        raise failures.DiracInternal(
            f'artifact {ref.get("sha256", "?")[:12]}… has no inline bytes; fetch '
            f'{ref.get("url")} instead. Guessing empty bytes here would look like an '
            f'artifact that exists and is zero-length.')
    try:
        data = base64.b64decode(ref['inline_base64'], validate=True)
    except (binascii.Error, ValueError) as e:
        raise failures.DiracInternal(f'inline_base64 is not valid base64: {e}') from e
    verify_bytes(data, ref['sha256'])
    return data


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """`bytes=a-b` → (start, end_inclusive), or None for the whole object.

    Range support is not a nicety here: a cube's header is the first ~200 bytes and
    carries the grid geometry, so a client deciding whether it wants 2.5 MB reads
    those 200 bytes first. Unsatisfiable ranges return None (serve everything)
    rather than raising, because a broken Range header must not lose the caller
    their data.
    """
    if not header or size <= 0:
        return None
    m = re.match(r'^bytes=(\d*)-(\d*)$', header.strip())
    if not m:
        return None
    lo_s, hi_s = m.group(1), m.group(2)
    if lo_s == '' and hi_s == '':
        return None
    if lo_s == '':                                   # bytes=-N → the final N bytes
        n = min(int(hi_s), size)
        return (size - n, size - 1) if n > 0 else None
    lo = int(lo_s)
    hi = size - 1 if hi_s == '' else min(int(hi_s), size - 1)
    if lo > hi or lo >= size:
        return None
    return lo, hi


class ArtifactStore(Protocol):
    """What every transport needs and nothing more.

    `put` is idempotent by content: storing the same bytes in the same role twice
    yields the same artifact. That is what makes a retried request cheap and a cache
    hit indistinguishable from a first computation, which is the property the whole
    caching layer already depends on.
    """

    def put(self, data: bytes, *, role: str, media_type: str | None = None,
            metadata: dict[str, Any] | None = None,
            method_version: str | None = None) -> Artifact: ...

    def read(self, address: str) -> tuple[Artifact, bytes]: ...

    def head(self, address: str) -> Artifact: ...


class MemoryArtifactStore:
    """The reference implementation, and the one the tests use.

    Exists for three reasons that are not "testing is nice": a CLI must work with no
    database, the SDK's own test suite must run on a bare interpreter, and — most
    usefully — a store with no SQL in it makes it obvious which of the store's
    properties are guaranteed by the CODE and which were being guaranteed by a
    Postgres constraint. Those are different claims, and only the first survives
    into a client.
    """

    def __init__(self) -> None:
        self._bytes: dict[str, bytes] = {}
        self._meta: dict[str, Artifact] = {}
        self._by_id: dict[str, str] = {}
        self.counters = {'put': 0, 'put_deduplicated': 0, 'read': 0, 'miss': 0}

    def put(self, data: bytes, *, role: str, media_type: str | None = None,
            metadata: dict[str, Any] | None = None,
            method_version: str | None = None) -> Artifact:
        digest = sha256_hex(data)
        key = f'{digest}:{role}:identity'
        self.counters['put'] += 1
        if key in self._meta:
            self.counters['put_deduplicated'] += 1
            return self._meta[key]
        art = Artifact(
            sha256=digest, role=role,
            media_type=media_type or MEDIA_TYPES.get(role, 'application/octet-stream'),
            size_bytes=len(data), id=key, metadata=metadata or {},
            method_version=method_version)
        self._bytes[digest] = data
        self._meta[key] = art
        self._by_id[key] = key
        return art

    def _resolve(self, address: str) -> Artifact:
        if address in self._meta:
            return self._meta[address]
        digest = address[7:] if address.startswith('sha256:') else address
        for art in self._meta.values():
            if art.sha256 == digest:
                return art
        self.counters['miss'] += 1
        raise failures.DiracNotFound(
            f'no artifact at {address!r}',
            details={'address': address, 'known': len(self._meta)})

    def read(self, address: str) -> tuple[Artifact, bytes]:
        art = self._resolve(address)
        data = self._bytes[art.sha256]
        verify_bytes(data, art.sha256)
        self.counters['read'] += 1
        return art, data

    def head(self, address: str) -> Artifact:
        return self._resolve(address)

    def verify(self, address: str) -> Artifact:
        artifact, _ = self.read(address)
        return artifact
