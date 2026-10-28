"""Checks against a real PostgreSQL instance (run in CI and locally via Docker Compose).

Skipped unless TEST_DATABASE_URL is set. The database name must end in ``_test`` or ``_ci``
(see ``pg_engine``) because these tests truncate tables and run migrations up and down.
"""

from datetime import timedelta

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.models.resource import MonitoredResource
from app.services.health import check_database
from tests.conftest import BACKEND_ROOT, run_alembic

pytestmark = pytest.mark.integration


@pytest.fixture
def db_session(pg_engine: Engine) -> Session:
    with Session(pg_engine) as session:
        yield session


def test_check_database_against_postgres(db_session: Session) -> None:
    assert check_database(db_session) is True


def test_ready_endpoint_against_postgres(settings: Settings, db_session: Session) -> None:
    app = create_app(settings)
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as client:
        response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["checks"]["database"] == "ok"


# --- Migrations -------------------------------------------------------------------------


def script_head() -> str:
    from alembic.config import Config

    head = ScriptDirectory.from_config(Config(str(BACKEND_ROOT / "alembic.ini"))).get_current_head()
    assert head is not None
    return head


def test_database_is_at_the_latest_migration(pg_engine: Engine) -> None:
    with pg_engine.connect() as connection:
        version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert version == script_head()


def test_models_and_migrations_are_in_sync(pg_engine: Engine) -> None:
    """Equivalent of ``alembic check``: autogenerate must find nothing to change."""
    with pg_engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        assert compare_metadata(context, Base.metadata) == []


def test_downgrade_and_upgrade_round_trip(pg_engine: Engine) -> None:
    try:
        run_alembic(pg_engine, "downgrade", "0001")
        assert "monitored_resources" not in inspect(pg_engine).get_table_names()
        run_alembic(pg_engine, "downgrade", "base")
        assert "monitored_resources" not in inspect(pg_engine).get_table_names()
    finally:
        run_alembic(pg_engine, "upgrade", "head")
    assert "monitored_resources" in inspect(pg_engine).get_table_names()


def test_only_the_monitored_resources_table_is_defined(pg_engine: Engine) -> None:
    # Phase 1 stores the latest observation only: no history/metric/alert/incident tables.
    tables = set(inspect(pg_engine).get_table_names()) - {"alembic_version"}
    assert tables == {"monitored_resources"}


# --- Schema constraints -----------------------------------------------------------------


def valid_row(**overrides) -> MonitoredResource:
    values = {
        "name": "svc",
        "url": "https://example.com",
        "check_interval_seconds": 60,
        "timeout_seconds": 10,
    }
    return MonitoredResource(**{**values, **overrides})


def test_valid_row_gets_server_defaults(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        row = valid_row()
        session.add(row)
        session.commit()
        assert row.is_enabled is True
        assert row.expected_status_code == 200
        assert row.current_status.value == "UNKNOWN"
        assert row.created_at is not None and row.updated_at is not None
        assert row.id is not None


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": "   "},
        {"check_interval_seconds": 0},
        {"timeout_seconds": 0},
        {"check_interval_seconds": 5, "timeout_seconds": 10},  # timeout beyond interval
        {"expected_status_code": 99},
        {"expected_status_code": 600},
        {"name": None},
        {"url": None},
    ],
)
def test_database_rejects_invalid_rows(
    session_factory: sessionmaker[Session], overrides: dict
) -> None:
    with session_factory() as session:
        session.add(valid_row(**overrides))
        with pytest.raises(IntegrityError):
            session.commit()


def test_database_rejects_unknown_status_values(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        row = valid_row()
        session.add(row)
        session.commit()
        with pytest.raises(IntegrityError):
            session.execute(
                text("UPDATE monitored_resources SET current_status = 'BOGUS' WHERE id = :i"),
                {"i": row.id},
            )


def test_scheduler_and_listing_indexes_exist(pg_engine: Engine) -> None:
    index_names = {i["name"] for i in inspect(pg_engine).get_indexes("monitored_resources")}
    assert {"ix_monitored_resources_due", "ix_monitored_resources_created_at_id"} <= index_names


def test_time_columns_are_timezone_aware(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        row = valid_row(next_check_at=func.now() + timedelta(seconds=30))
        session.add(row)
        session.commit()
        session.refresh(row)
        assert row.next_check_at.tzinfo is not None
        assert row.created_at.tzinfo is not None
