"""The Postgres ArtifactStore. A separate module ON PURPOSE.

artifacts.py holds the arithmetic — digests, the inline threshold, range parsing, the
reference shape — and imports the standard library only. This file is the one that
knows psycopg exists. The split is not tidiness: the layering gate asserts that
artifacts.py imports no DB driver, so an SDK, a CLI running offline and a test on a
bare interpreter all get the addressing logic and none of them inherits libpq.

WHAT POSTGRES CONTRIBUTES that MemoryArtifactStore cannot, and it is worth naming
because it is the reason to have two implementations rather than one:

    app.blob      CHECK (digest(bytes,'sha256') = sha256)   ← the store CANNOT hold
                  a mislabelled payload. Not "does not"; cannot. Enforced by the
                  database, so a buggy writer in any language fails at the INSERT.
    app.artifact  UNIQUE (blob_sha256, role, encoding)      ← re-registering the same
                  cube in the same role returns the same id instead of minting an
                  alias, which is what makes `put` idempotent under retry.

MemoryArtifactStore reimplements both in Python. Having them side by side is how you
find out which invariants were being held by the schema and would silently disappear
the moment somebody ran the same code against a different backend.
"""
from __future__ import annotations

from typing import Any

import artifacts as A
import failures

try:
    import psycopg
    from psycopg.types.json import Jsonb
except ImportError:                                                # pragma: no cover
    psycopg = None                                                 # type: ignore[assignment]
    Jsonb = None                                                   # type: ignore[assignment]


class PostgresArtifactStore:
    """Content-addressed storage over app.blob + app.artifact.

    Takes a connection FACTORY rather than a connection, so the caller owns the
    pooling policy and this class owns no long-lived resource. The field daemon
    already opens short autocommit connections per operation; matching that avoids a
    second, differently-behaving connection lifecycle in the same process.
    """

    def __init__(self, connect, *, base_path: str = '/v2/artifacts') -> None:
        self._connect = connect
        self.base_path = base_path
        self.counters = {'put': 0, 'put_deduplicated': 0, 'read': 0, 'miss': 0,
                         'digest_mismatch': 0, 'unavailable': 0,
                         'authorization_denied': 0,
                         'authorization_schema_unavailable': 0}

    # ── writes ────────────────────────────────────────────────────────────────
    def put(self, data: bytes, *, role: str, media_type: str | None = None,
            metadata: dict[str, Any] | None = None,
            method_version: str | None = None,
            method_id: str | None = None,
            cursor=None) -> A.Artifact:
        """Store bytes and name their role. Idempotent by (content, role).

        The digest is computed HERE and passed as the primary key, and the database
        recomputes it in the CHECK. Two independent computations of the same value is
        the point: if this process's hashlib and the database's pgcrypto ever
        disagree, the INSERT fails loudly instead of storing bytes under a name that
        does not describe them.
        """
        if psycopg is None:
            self.counters['unavailable'] += 1
            raise failures.DiracFailure(
                'DB_UNAVAILABLE',
                'psycopg is not importable, so artifacts cannot be persisted. The '
                'in-memory store is a valid ArtifactStore and the caller may fall '
                'back to it — but bytes stored there do not survive the process, and '
                'a reference handed to a client that outlives it would 404.')
        digest = A.sha256_hex(data)
        mt = media_type or A.MEDIA_TYPES.get(role, 'application/octet-stream')
        self.counters['put'] += 1

        def persist(cur) -> A.Artifact:
            cur.execute(
                'INSERT INTO app.blob (sha256, media_type, byte_len, bytes) '
                'VALUES (decode(%s, %s), %s, %s, %s) ON CONFLICT (sha256) DO NOTHING',
                (digest, 'hex', mt, len(data), data))
            # RETURNING on a DO NOTHING conflict yields no row, so the id is read
            # back explicitly rather than inferred. An inferred id after a conflict
            # is how a dedup path ends up returning None and a caller ends up with a
            # reference to nothing.
            cur.execute(
                'INSERT INTO app.artifact '
                '  (blob_sha256, media_type, role, size_bytes, metadata, '
                '   created_by_method) '
                'VALUES (decode(%s, %s), %s, %s, %s, %s, %s) '
                'ON CONFLICT (blob_sha256, role, encoding) DO NOTHING '
                'RETURNING id',
                (digest, 'hex', mt, role, len(data),
                 Jsonb(metadata or {}), method_id))
            row = cur.fetchone()
            if row is None:
                self.counters['put_deduplicated'] += 1
                cur.execute(
                    'SELECT id FROM app.artifact '
                    ' WHERE blob_sha256 = decode(%s, %s) AND role = %s '
                    '   AND encoding = %s',
                    (digest, 'hex', role, 'identity'))
                row = cur.fetchone()
                if row is None:                                    # pragma: no cover
                    raise failures.DiracInternal(
                        'the artifact INSERT conflicted and the conflicting row is '
                        'not there. That is not a race this code can lose — it means '
                        'the unique key and the lookup key disagree.')
            return A.Artifact(sha256=digest, role=role, media_type=mt,
                              size_bytes=len(data), id=str(row[0]),
                              metadata=metadata or {},
                              method_version=method_version)

        # A caller that already owns the authoritative transaction may lend its
        # cursor.  This is not a convenience: campaign preparation has one CAS
        # commit barrier, and opening a private connection here used to make the
        # artifact survive when that later CAS rolled back.
        if cursor is not None:
            return persist(cursor)
        with self._connect() as conn, conn.cursor() as cur:
            return persist(cur)

    def link_to_job(self, job_id: str, artifact_id: str, role: str,
                    ordinal: int = 0, *, cursor=None) -> None:
        """Record which invocation produced this artifact.

        Separate from `put` because an artifact can exist before its job row does
        (the daemon writes the cube, then the ledger), and because one artifact may
        belong to several jobs — a cache hit legitimately links an existing artifact
        to a new job without rewriting any bytes.
        """
        if psycopg is None:
            return

        def link(cur) -> None:
            cur.execute(
                'INSERT INTO app.job_artifact (job_id, artifact_id, role, ordinal) '
                'VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING',
                (job_id, artifact_id, role, ordinal))

        if cursor is not None:
            link(cursor)
            return
        with self._connect() as conn, conn.cursor() as cur:
            link(cur)

    def link_to_campaign(self, campaign_id: str, artifact_id: str, role: str,
                         ordinal: int = 0, *, cursor=None) -> None:
        """Make campaign ownership explicit and independently queryable.

        Artifact metadata is immutable first-writer context, not an ownership
        relation: content deduplication can reuse one artifact in several campaigns.
        The join table is therefore the authorization/provenance fact.
        """
        if psycopg is None:
            return

        def link(cur) -> None:
            cur.execute(
                'INSERT INTO app.rbfe_campaign_artifact '
                '(campaign_id, artifact_id, role, ordinal) '
                'VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING',
                (campaign_id, artifact_id, role, ordinal))

        if cursor is not None:
            link(cursor)
            return
        with self._connect() as conn, conn.cursor() as cur:
            link(cur)

    # ── reads ─────────────────────────────────────────────────────────────────
    _SELECT = (
        'SELECT a.id, encode(a.blob_sha256, %s), a.role, a.media_type, '
        '       a.size_bytes, a.encoding, a.metadata, m.version '
        '  FROM app.artifact a '
        '  LEFT JOIN meta.method m ON m.id = a.created_by_method ')

    def verify(self, address: str) -> A.Artifact:
        artifact, _ = self.read(address)
        return artifact

    def _row_to_artifact(self, row) -> A.Artifact:
        return A.Artifact(sha256=row[1], role=row[2], media_type=row[3],
                          size_bytes=row[4], encoding=row[5], id=str(row[0]),
                          metadata=dict(row[6] or {}), method_version=row[7])

    @staticmethod
    def _principal(actor: dict[str, str] | None) -> tuple[str, str]:
        if not isinstance(actor, dict):
            raise failures.DiracInvalidParameters(
                'artifact access requires an authenticated actor')
        kind = str(actor.get('kind') or '')
        actor_id = str(actor.get('id') or '').strip()
        if kind not in {'human', 'agent', 'service'} or not actor_id:
            raise failures.DiracInvalidParameters(
                'artifact actor must be a human, agent, or service')
        return kind, actor_id

    def _require_authorization_schema(self, cur) -> None:
        """Refuse before compiling ACL SQL against a partially migrated schema.

        PostgreSQL resolves every relation in a statement before evaluating its
        WHERE clause.  Without this capability probe, a daemon started before 040
        or 045 turns an intentional deny into an undefined-table 500.  A legacy
        private Artifact is never assigned an inferred owner or made public as a
        compatibility shortcut; it remains indistinguishable from absence.
        """
        cur.execute(
            "SELECT to_regclass('app.rbfe_campaign') IS NOT NULL, "
            "to_regclass('app.rbfe_campaign_artifact') IS NOT NULL, "
            "EXISTS (SELECT 1 FROM pg_constraint "
            "WHERE conrelid=to_regclass('app.rbfe_campaign_artifact') "
            "AND contype='f' "
            "AND conname='rbfe_campaign_artifact_role_fk' "
            "AND pg_get_constraintdef(oid) LIKE "
            "'%%FOREIGN KEY (artifact_id, role)%%'), "
            "to_regclass('app.research_loop_artifact') IS NOT NULL")
        capability = tuple(cur.fetchone() or (False, False, False, False))
        if all(capability):
            return
        missing = []
        if not capability[0]:
            missing.append('040_rbfe_campaign_state.sql')
        if not all(capability[1:3]):
            missing.append('045_rbfe_campaign_artifact_ownership.sql')
        if not capability[3]:
            missing.append('049_research_loop.sql')
        self.counters['authorization_schema_unavailable'] += 1
        raise failures.DiracFailure(
            'DB_UNAVAILABLE',
            'artifact authorization schema is incomplete; private artifacts '
            'remain fail-closed until explicit ownership can be verified',
            details={
                'required_migrations': missing,
                'legacy_unowned_policy': 'fail_closed',
                'owner_inference': False,
                'implicit_public': False,
            })

    @staticmethod
    def _access_clause(alias: str = 'a') -> str:
        """SQL ownership witness for an HTTP-visible Artifact.

        A UUID or digest is an integrity capability, never an authorization
        capability.  Legacy/unlinked rows therefore fail closed.  Internal
        science paths continue to use ``head/read`` because their authorization
        happened when the server resolved the owning Job/Campaign reference.
        """
        return f'''(
            coalesce({alias}.metadata->>'visibility','') = 'public'
            OR EXISTS (
                SELECT 1 FROM app.job_artifact ja
                JOIN app.job j ON j.id=ja.job_id
                WHERE ja.artifact_id={alias}.id
                  AND j.actor_kind=%s AND j.actor_id=%s
            )
            OR EXISTS (
                SELECT 1 FROM app.rbfe_campaign c
                WHERE c.id::text={alias}.metadata->>'campaign_id'
                  AND c.created_by_kind=%s AND c.created_by_id=%s
            )
            OR EXISTS (
                SELECT 1 FROM app.rbfe_campaign_artifact ca
                JOIN app.rbfe_campaign c ON c.id=ca.campaign_id
                WHERE ca.artifact_id={alias}.id
                  AND c.created_by_kind=%s AND c.created_by_id=%s
            )
            OR EXISTS (
                SELECT 1
                FROM design.motif_scientific_object o
                JOIN app.rbfe_campaign c ON EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements(
                        CASE WHEN jsonb_typeof(c.state->'owned_object_refs')='array'
                             THEN c.state->'owned_object_refs' ELSE '[]'::jsonb END
                    ) owned_ref
                    WHERE owned_ref->>'id'=o.id::text
                )
                WHERE o.document_artifact_id={alias}.id
                  AND c.created_by_kind=%s AND c.created_by_id=%s
            )
            OR EXISTS (
                SELECT 1
                FROM app.research_loop_artifact la
                JOIN app.research_loop_state ls ON ls.run_id=la.run_id
                WHERE la.artifact_id={alias}.id
                  AND ls.actor_kind=%s AND ls.actor_id=%s
            )
        )'''

    @staticmethod
    def _access_params(kind: str, actor_id: str) -> tuple[str, ...]:
        return (kind, actor_id, kind, actor_id, kind, actor_id,
                kind, actor_id, kind, actor_id)

    def head(self, address: str) -> A.Artifact:
        """Metadata without bytes — the request a client makes to decide.

        This is the half that makes a reference worth having: 2.5 MB described in
        200 bytes, so a CLI can print the size and an agent can decide not to want
        it.
        """
        if psycopg is None:
            raise failures.DiracFailure('DB_UNAVAILABLE', 'no database driver')
        by_digest = A.is_digest(address) or address.startswith('sha256:')
        digest = address[7:] if address.startswith('sha256:') else address
        with self._connect() as conn, conn.cursor() as cur:
            if by_digest:
                # Several roles may share one blob, so a digest lookup is ambiguous
                # by construction. Newest wins and the ambiguity is not hidden: the
                # metadata response carries `roles_at_digest` when it is > 1.
                cur.execute(self._SELECT +
                            ' WHERE a.blob_sha256 = decode(%s, %s) '
                            ' ORDER BY a.created_at DESC LIMIT 1',
                            ('hex', digest, 'hex'))
            else:
                cur.execute(self._SELECT + ' WHERE a.id = %s', ('hex', address))
            row = cur.fetchone()
            if row is None:
                self.counters['miss'] += 1
                raise failures.DiracNotFound(
                    f'no artifact at {address!r}',
                    details={'address': address,
                             'looked_up_by': 'digest' if by_digest else 'id'})
            art = self._row_to_artifact(row)
            if by_digest:
                cur.execute('SELECT count(*) FROM app.artifact '
                            ' WHERE blob_sha256 = decode(%s, %s)', (digest, 'hex'))
                n = cur.fetchone()[0]
                if n > 1:
                    art = A.Artifact(**{**art.__dict__,
                                        'metadata': {**art.metadata,
                                                     'roles_at_digest': n}})
            return art

    def head_authorized(self, address: str,
                        actor: dict[str, str]) -> A.Artifact:
        """Resolve metadata only when the authenticated actor owns its lineage.

        Digest lookup is filtered before ordering; otherwise the newest artifact
        at shared bytes could belong to another actor and make an owned role look
        absent (or, worse, disclose which role was newest).
        """
        if psycopg is None:
            raise failures.DiracFailure('DB_UNAVAILABLE', 'no database driver')
        kind, actor_id = self._principal(actor)
        by_digest = A.is_digest(address) or address.startswith('sha256:')
        digest = address[7:] if address.startswith('sha256:') else address
        access = self._access_clause('a')
        access_params = self._access_params(kind, actor_id)
        with self._connect() as conn, conn.cursor() as cur:
            self._require_authorization_schema(cur)
            if by_digest:
                cur.execute(
                    self._SELECT
                    + ' WHERE a.blob_sha256=decode(%s,%s) AND ' + access
                    + ' ORDER BY a.created_at DESC LIMIT 1',
                    ("hex", digest, "hex", *access_params))
            else:
                cur.execute(
                    self._SELECT + ' WHERE a.id=%s AND ' + access,
                    ("hex", address, *access_params))
            row = cur.fetchone()
            if row is None:
                self.counters['miss'] += 1
                self.counters['authorization_denied'] += 1
                # Deliberately indistinguishable from a nonexistent address.
                raise failures.DiracNotFound(
                    f'no artifact at {address!r}',
                    details={'address': address, 'looked_up_by':
                             'digest' if by_digest else 'id'})
            art = self._row_to_artifact(row)
            if by_digest:
                cur.execute(
                    'SELECT count(*) FROM app.artifact a '
                    'WHERE a.blob_sha256=decode(%s,%s) AND ' + access,
                    (digest, 'hex', *access_params))
                count = int(cur.fetchone()[0])
                if count > 1:
                    art = A.Artifact(**{
                        **art.__dict__,
                        'metadata': {**art.metadata, 'roles_at_digest': count},
                    })
            return art

    def read(self, address: str) -> tuple[A.Artifact, bytes]:
        """Bytes, verified against the digest they were stored under.

        Verified on every read even though a CHECK constraint makes a mismatch
        impossible in the database: the bytes travel through psycopg, this process's
        memory and possibly a compression step, and the check costs ~1 ms/MB. A
        verification that only runs when someone is suspicious has never run.
        """
        art = self.head(address)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute('SELECT bytes FROM app.blob WHERE sha256 = decode(%s, %s)',
                        (art.sha256, 'hex'))
            row = cur.fetchone()
        if row is None:
            self.counters['miss'] += 1
            raise failures.DiracNotFound(
                f'artifact {art.id} names blob {art.sha256[:12]}… and that blob is '
                f'not in app.blob. The reference is dangling, which the foreign key '
                f'should make impossible — read it as corruption, not as absence.',
                details={'artifact_id': art.id, 'sha256': art.sha256})
        data = bytes(row[0])
        try:
            A.verify_bytes(data, art.sha256)
        except failures.DiracFailure:
            self.counters['digest_mismatch'] += 1
            raise
        self.counters['read'] += 1
        return art, data

    def read_authorized(self, address: str,
                        actor: dict[str, str]) -> tuple[A.Artifact, bytes]:
        art = self.head_authorized(address, actor)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute('SELECT bytes FROM app.blob WHERE sha256 = decode(%s, %s)',
                        (art.sha256, 'hex'))
            row = cur.fetchone()
        if row is None:
            self.counters['miss'] += 1
            raise failures.DiracNotFound(f'no artifact at {address!r}')
        data = bytes(row[0])
        try:
            A.verify_bytes(data, art.sha256)
        except failures.DiracFailure:
            self.counters['digest_mismatch'] += 1
            raise
        self.counters['read'] += 1
        return art, data

    def read_range(self, address: str, range_header: str | None
                   ) -> tuple[A.Artifact, bytes, tuple[int, int] | None]:
        """A byte range, sliced AFTER the whole object is verified.

        Verifying the whole object to serve 200 bytes of it looks wasteful and is
        the only correct order available: a partial read cannot be checked against a
        whole-object digest, so the alternative is serving unverified bytes. When a
        cube's header is the common case, the cost is one DB read of an object we
        would have read anyway.
        """
        art, data = self.read(address)
        rng = A.parse_range(range_header, len(data))
        if rng is None:
            return art, data, None
        lo, hi = rng
        return art, data[lo:hi + 1], rng

    def read_range_authorized(
            self, address: str, range_header: str | None,
            actor: dict[str, str]) -> tuple[A.Artifact, bytes, tuple[int, int] | None]:
        art, data = self.read_authorized(address, actor)
        rng = A.parse_range(range_header, len(data))
        if rng is None:
            return art, data, None
        lo, hi = rng
        return art, data[lo:hi + 1], rng
