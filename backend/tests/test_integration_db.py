"""Checks against a real PostgreSQL instance (run in CI and via Docker Compose).

Skipped unless TEST_DATABASE_URL is set.
"""

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.session import get_db
from app.main import create_app
from app.services.health import check_database

pytestmark = pytest.mark.integration


@pytest.fixture
def db_session() -> Iterator[Session]:
    engine = create_engine(os.environ["TEST_DATABASE_URL"], pool_pre_ping=True)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_check_database_against_postgres(db_session: Session) -> None:
    assert check_database(db_session) is True


def test_ready_endpoint_against_postgres(settings: Settings, db_session: Session) -> None:
    app = create_app(settings)
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as client:
        response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["checks"]["database"] == "ok"


def test_alembic_baseline_applied(db_session: Session) -> None:
    version = db_session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert version == "0001"
