from __future__ import annotations

import hashlib
from uuid import UUID

import pytest

import failures
from artifacts_pg import PostgresArtifactStore


class _Cursor:
    def __init__(self, owner: tuple[str, str], data: bytes,
                 authorization_schema=(True, True, True)) -> None:
        self.owner = owner
        self.data = data
        self.authorization_schema = authorization_schema
        self.row = None
        self.statements: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=()):
        statement = " ".join(str(sql).split())
        self.statements.append(statement)
        if statement.startswith("SELECT to_regclass('app.rbfe_campaign')"):
            self.row = self.authorization_schema
            return
        if statement.startswith("SELECT bytes FROM app.blob"):
            self.row = (self.data,)
            return
        assert "app.job_artifact" in statement
        assert "app.rbfe_campaign" in statement
        assert "owned_object_refs" in statement
        supplied = tuple(params[-8:])
        expected = self.owner * 4
        if supplied != expected:
            self.row = None
        elif statement.startswith("SELECT count(*)"):
            self.row = (1,)
        else:
            digest = hashlib.sha256(self.data).hexdigest()
            self.row = (
                UUID(int=7), digest, "rbfe.pose.sdf",
                "chemical/x-mdl-sdfile", len(self.data), "identity", {}, None,
            )

    def fetchone(self):
        return self.row


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self._cursor


def _store(owner=("human", "chemist-a"), data=b"private pose",
           authorization_schema=(True, True, True)):
    cursor = _Cursor(owner, data, authorization_schema)
    return PostgresArtifactStore(lambda: _Connection(cursor)), cursor, data


def test_artifact_uuid_and_digest_are_not_cross_tenant_authorization():
    store, _, data = _store()
    addresses = (
        str(UUID(int=7)),
        "sha256:" + hashlib.sha256(data).hexdigest(),
    )
    for address in addresses:
        with pytest.raises(failures.DiracNotFound):
            store.head_authorized(
                address, {"kind": "human", "id": "chemist-b"})


def test_authorization_fails_closed_before_campaign_owner_schema_exists():
    store, cursor, _ = _store(authorization_schema=(True, False, False))
    with pytest.raises(failures.DiracFailure) as caught:
        store.head_authorized(
            str(UUID(int=7)), {"kind": "human", "id": "chemist-a"})
    assert caught.value.code == "DB_UNAVAILABLE"
    assert caught.value.details == {
        "required_migrations": ["045_rbfe_campaign_artifact_ownership.sql"],
        "legacy_unowned_policy": "fail_closed",
        "owner_inference": False,
        "implicit_public": False,
    }
    assert not any("FROM app.artifact a" in sql for sql in cursor.statements)


def test_owner_can_read_and_every_authorization_route_is_present():
    store, cursor, data = _store()
    artifact, returned = store.read_authorized(
        str(UUID(int=7)), {"kind": "human", "id": "chemist-a"})
    assert returned == data
    assert artifact.role == "rbfe.pose.sdf"
    access_sql = next(
        statement for statement in cursor.statements
        if "app.job_artifact" in statement)
    assert "app.rbfe_campaign_artifact" in access_sql
    assert "j.actor_kind=%s AND j.actor_id=%s" in access_sql
    assert "c.created_by_kind=%s AND c.created_by_id=%s" in access_sql
    assert "coalesce(a.metadata->>'visibility','') = 'public'" in access_sql
