from __future__ import annotations

from copy import deepcopy
import inspect
from uuid import UUID

import pytest

import artifacts_pg
from artifacts_pg import PostgresArtifactStore
from motif.rbfe_references import PostgresRbfeReferenceResolver


CAMPAIGN_ID = "00000000-0000-4000-8000-000000000451"
JOB_ID = "00000000-0000-4000-8000-000000000452"


class _Database:
    """A transaction-faithful fake: exception means none of the working set lands."""

    def __init__(self) -> None:
        self.committed = {
            "blobs": set(),
            "artifacts": {},
            "job_links": set(),
            "campaign_links": set(),
        }

    def connect(self, *, cas_succeeds: bool):
        return _Connection(self, cas_succeeds)

    def counts(self) -> tuple[int, int, int, int]:
        state = self.committed
        return (
            len(state["blobs"]), len(state["artifacts"]),
            len(state["job_links"]), len(state["campaign_links"]),
        )


class _Connection:
    def __init__(self, database: _Database, cas_succeeds: bool) -> None:
        self.database = database
        self.working = deepcopy(database.committed)
        self.cursor_object = _Cursor(self.working, cas_succeeds)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_args):
        if exc_type is None:
            self.database.committed = self.working
        return False

    def cursor(self):
        return self.cursor_object


class _Cursor:
    def __init__(self, state: dict, cas_succeeds: bool) -> None:
        self.state = state
        self.cas_succeeds = cas_succeeds
        self.row = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=()):
        statement = " ".join(str(sql).split())
        self.row = None
        if statement.startswith("INSERT INTO app.blob"):
            self.state["blobs"].add(str(params[0]))
            return
        if statement.startswith("INSERT INTO app.artifact"):
            key = (str(params[0]), str(params[3]), "identity")
            existing = self.state["artifacts"].get(key)
            if existing is None:
                artifact_id = UUID(int=len(self.state["artifacts"]) + 1)
                self.state["artifacts"][key] = artifact_id
                self.row = (artifact_id,)
            return
        if statement.startswith("SELECT id FROM app.artifact"):
            self.row = (
                self.state["artifacts"][(str(params[0]), str(params[2]), str(params[3]))],
            )
            return
        if statement.startswith("UPDATE app.rbfe_campaign"):
            self.row = (CAMPAIGN_ID,) if self.cas_succeeds else None
            return
        if statement.startswith("INSERT INTO app.job_artifact"):
            self.state["job_links"].add(tuple(map(str, params[:4])))
            return
        if statement.startswith("INSERT INTO app.rbfe_campaign_artifact"):
            self.state["campaign_links"].add(tuple(map(str, params[:4])))
            return
        raise AssertionError(f"unexpected SQL in atomicity fake: {statement}")

    def fetchone(self):
        row, self.row = self.row, None
        return row


def _commit_two_preparation_artifacts(
        store: PostgresArtifactStore, connection: _Connection) -> None:
    with connection as transaction, transaction.cursor() as cursor:
        receptor = store.put(
            b"PREPARED RECEPTOR", role="rbfe.receptor.pdb",
            media_type="chemical/x-pdb", cursor=cursor)
        pose = store.put(
            b"POSE SDF", role="rbfe.pose.sdf",
            media_type="chemical/x-mdl-sdfile", cursor=cursor)
        cursor.execute(
            "UPDATE app.rbfe_campaign SET version=2 WHERE id=%s AND version=1 "
            "RETURNING id", (CAMPAIGN_ID,))
        if cursor.fetchone() is None:
            raise RuntimeError("injected campaign CAS conflict")
        for ordinal, artifact in enumerate((receptor, pose)):
            store.link_to_job(
                JOB_ID, artifact.id, artifact.role, ordinal, cursor=cursor)
            store.link_to_campaign(
                CAMPAIGN_ID, artifact.id, artifact.role, ordinal, cursor=cursor)


@pytest.fixture(autouse=True)
def _postgres_capabilities(monkeypatch):
    # These tests exercise transaction ownership, not the installed driver.
    monkeypatch.setattr(artifacts_pg, "psycopg", object())
    monkeypatch.setattr(artifacts_pg, "Jsonb", lambda value: value)


def test_injected_campaign_cas_conflict_commits_no_orphan_or_published_artifact():
    database = _Database()
    store = PostgresArtifactStore(
        lambda: database.connect(cas_succeeds=False))
    before = database.counts()

    with pytest.raises(RuntimeError, match="CAS conflict"):
        _commit_two_preparation_artifacts(
            store, database.connect(cas_succeeds=False))

    assert database.counts() == before == (0, 0, 0, 0)


def test_successful_campaign_cas_commits_job_and_campaign_ownership_links():
    database = _Database()
    store = PostgresArtifactStore(
        lambda: database.connect(cas_succeeds=True))

    _commit_two_preparation_artifacts(
        store, database.connect(cas_succeeds=True))

    assert database.counts() == (2, 2, 2, 2)
    assert {row[0] for row in database.committed["job_links"]} == {JOB_ID}
    assert {row[0] for row in database.committed["campaign_links"]} == {
        CAMPAIGN_ID}


def test_real_prepare_path_uses_one_cursor_through_artifacts_cas_and_links():
    source = inspect.getsource(PostgresRbfeReferenceResolver.prepare_campaign)
    transaction = source.index("with self._connect() as connection, connection.cursor() as cursor:",
                               source.index("prepared = self._build_campaign"))
    raw_put = source.index("raw_artifact = store.put", transaction)
    receptor_put = source.index("receptor_artifact = store.put", raw_put)
    pose_put = source.index("pose_artifacts.append((pose, store.put", receptor_put)
    cas = source.index('"UPDATE app.rbfe_campaign SET version=%s', pose_put)
    job_link = source.index("store.link_to_job(", cas)
    campaign_link = source.index("store.link_to_campaign(", job_link)

    assert transaction < raw_put < receptor_put < pose_put < cas < job_link < campaign_link
    assert source.count("cursor=cursor") >= 5
    assert "cursor=cursor" in inspect.getsource(
        PostgresRbfeReferenceResolver._put_object)


def test_migration_makes_campaign_ownership_a_restricted_many_to_many_relation():
    migrations = __import__("pathlib").Path(__file__).resolve().parents[1] / "db/migrations"
    campaign = (migrations / "040_rbfe_campaign_state.sql").read_text()
    migration = (
        migrations / "045_rbfe_campaign_artifact_ownership.sql").read_text()
    assert "(created_by_kind,created_by_id,updated_at DESC)" in campaign
    assert "CREATE TABLE app.rbfe_campaign_artifact" in migration
    assert "REFERENCES app.rbfe_campaign(id) ON DELETE RESTRICT" in migration
    assert "UNIQUE (id, role)" in migration
    assert "FOREIGN KEY (artifact_id, role)" in migration
    assert "REFERENCES app.artifact(id, role) ON DELETE RESTRICT" in migration
    assert "PRIMARY KEY (campaign_id, artifact_id, role)" in migration
