"""Shared fixtures.

Unit tests never touch a real database: ``get_db`` is overridden with a stub session.
Tests marked ``integration`` run only when ``TEST_DATABASE_URL`` is set.
"""

import os
from collections.abc import Callable, Iterator
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.session import get_db
from app.main import create_app


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "integration: requires a live PostgreSQL database (TEST_DATABASE_URL)"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("TEST_DATABASE_URL"):
        return
    skip = pytest.mark.skip(reason="TEST_DATABASE_URL not set")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


def _settings_env_vars() -> set[str]:
    """Names of every environment variable ``Settings`` reads (field names and aliases)."""
    names: set[str] = set()
    for name, field in Settings.model_fields.items():
        names.add((field.alias or name).upper())
    return names


@pytest.fixture(autouse=True)
def isolate_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the host/CI environment (e.g. DATABASE_URL) from leaking into any test.

    Tests that need a variable set it explicitly with ``monkeypatch.setenv``.
    """
    for name in _settings_env_vars():
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def settings() -> Settings:
    # _env_file=None isolates tests from any developer .env file.
    return Settings(
        _env_file=None,
        environment="test",
        log_format="text",
        postgres_password="test-password",
    )


@pytest.fixture
def app(settings: Settings) -> Iterator[FastAPI]:
    application = create_app(settings)
    yield application
    application.dependency_overrides.clear()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def override_db(app: FastAPI) -> Callable[[Session], None]:
    """Install a stub session for the ``get_db`` dependency."""

    def _install(session: Session) -> None:
        app.dependency_overrides[get_db] = lambda: session

    return _install


@pytest.fixture
def healthy_session() -> Session:
    return MagicMock(spec=Session)


@pytest.fixture
def failing_session() -> Session:
    session = MagicMock(spec=Session)
    session.execute.side_effect = OperationalError("SELECT 1", {}, Exception("connection refused"))
    return session
