"""Checks against a real PostgreSQL instance (run in CI and locally via Docker Compose).

Skipped unless TEST_DATABASE_URL is set. The database name must end in ``_test`` or ``_ci``
(see ``pg_engine``) because these tests truncate tables and run migrations up and down.
"""

from datetime import UTC, datetime, timedelta, timezone

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
from app.models.resource import MonitoredResource, ResourceStatus
from app.services.health import check_database
from tests.conftest import BACKEND_ROOT, run_alembic
from tests.helpers import count_checks, fetch, history, insert_check, insert_resource

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
        run_alembic(pg_engine, "downgrade", "0002")
        assert "resource_check_results" not in inspect(pg_engine).get_table_names()
        run_alembic(pg_engine, "downgrade", "0001")
        assert "monitored_resources" not in inspect(pg_engine).get_table_names()
        run_alembic(pg_engine, "downgrade", "base")
        assert "monitored_resources" not in inspect(pg_engine).get_table_names()
    finally:
        run_alembic(pg_engine, "upgrade", "head")
    assert "monitored_resources" in inspect(pg_engine).get_table_names()


def test_only_the_expected_tables_are_defined(pg_engine: Engine) -> None:
    # Resources plus their check history. No alert/incident tables exist yet.
    tables = set(inspect(pg_engine).get_table_names()) - {"alembic_version"}
    assert tables == {"monitored_resources", "resource_check_results"}


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


# --- Check history schema -----------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": ResourceStatus.UNKNOWN},  # UNKNOWN is a state, never a measurement
        {"http_status_code": 99},
        {"http_status_code": 600},
        {"response_time_ms": -0.01},
        {"duration_ms": -1.0},
    ],
)
def test_history_rows_reject_invalid_measurements(
    session_factory: sessionmaker[Session], overrides: dict
) -> None:
    rid = insert_resource(session_factory)
    kwargs = {"response_time_ms": 5.0, "http_status_code": 200, "status": ResourceStatus.HEALTHY}
    kwargs.update(overrides)
    with pytest.raises(IntegrityError):
        insert_check(session_factory, rid, datetime(2026, 3, 10, tzinfo=UTC), **kwargs)


@pytest.mark.parametrize(
    "column_and_value",
    [("source", "ROBOT"), ("error_category", "weird"), ("status", "BOGUS")],
)
def test_history_enums_are_enforced_by_the_database(
    session_factory: sessionmaker[Session], column_and_value: tuple[str, str]
) -> None:
    column, value = column_and_value
    rid = insert_resource(session_factory)
    row = insert_check(session_factory, rid, datetime(2026, 3, 10, tzinfo=UTC), response_time_ms=1)
    with session_factory() as session, pytest.raises(IntegrityError):
        session.execute(
            text(f"UPDATE resource_check_results SET {column} = :v WHERE id = :i"),
            {"v": value, "i": row},
        )


def test_history_timestamps_are_timezone_aware_utc_and_created_at_is_filled(
    session_factory: sessionmaker[Session],
) -> None:
    rid = insert_resource(session_factory)
    # An instant written with a non-UTC offset is stored and returned as the same instant.
    plus_two = datetime(2026, 3, 10, 14, 0, tzinfo=timezone(timedelta(hours=2)))
    insert_check(session_factory, rid, plus_two, response_time_ms=1.0)

    (row,) = history(session_factory, rid)
    assert row.checked_at.tzinfo is not None
    assert row.checked_at == datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
    assert row.created_at.tzinfo is not None and row.created_at > row.checked_at


def test_history_numbers_keep_two_decimals_without_floating_point_noise(
    session_factory: sessionmaker[Session],
) -> None:
    rid = insert_resource(session_factory)
    insert_check(
        session_factory, rid, datetime(2026, 3, 10, tzinfo=UTC), response_time_ms=0.1 + 0.2
    )
    (row,) = history(session_factory, rid)
    assert str(row.response_time_ms) == "0.30"  # numeric(10,2), not 0.30000000000000004


def test_history_indexes_match_the_query_patterns(pg_engine: Engine) -> None:
    with pg_engine.connect() as connection:
        definitions = dict(
            connection.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE tablename = 'resource_check_results'"
                )
            ).all()
        )
    assert (
        "(resource_id, checked_at DESC, id DESC)"
        in definitions["ix_resource_check_results_resource_checked_at_id"]
    )
    assert "(checked_at)" in definitions["ix_resource_check_results_checked_at"]
    # No speculative extra indexes beyond the primary key and these two.
    assert set(definitions) == {
        "pk_resource_check_results",
        "ix_resource_check_results_resource_checked_at_id",
        "ix_resource_check_results_checked_at",
    }


def test_history_foreign_key_cascades_in_the_database(pg_engine: Engine) -> None:
    with pg_engine.connect() as connection:
        rule = connection.execute(
            text(
                "SELECT confdeltype FROM pg_constraint "
                "WHERE conname = 'fk_resource_check_results_resource_id_monitored_resources'"
            )
        ).scalar_one()
    assert rule == "c"  # ON DELETE CASCADE


def test_history_migration_preserves_existing_resources_and_is_reversible(
    pg_engine: Engine, session_factory: sessionmaker[Session]
) -> None:
    rid = insert_resource(session_factory, name="survivor", status=ResourceStatus.DEGRADED)
    try:
        run_alembic(pg_engine, "downgrade", "0002")  # only the history table goes away
        with pg_engine.connect() as connection:
            names = connection.execute(text("SELECT name FROM monitored_resources")).scalars().all()
        assert names == ["survivor"]
        run_alembic(pg_engine, "upgrade", "head")
    finally:
        run_alembic(pg_engine, "upgrade", "head")

    survivor = fetch(session_factory, rid)
    assert (survivor.name, survivor.current_status) == ("survivor", ResourceStatus.DEGRADED)
    assert count_checks(session_factory, rid) == 0  # a fresh, empty history table
    insert_check(session_factory, rid, datetime(2026, 3, 10, tzinfo=UTC), response_time_ms=1.0)
    assert count_checks(session_factory, rid) == 1
