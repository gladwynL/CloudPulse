"""Shared fixtures.

Unit tests never touch a real database: ``get_db`` is overridden with a stub session.
Tests marked ``integration`` run only when ``TEST_DATABASE_URL`` is set.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_check_store, get_checker
from app.core.config import Settings
from app.db.session import get_db
from app.main import create_app
from app.models.resource import ResourceStatus
from app.monitoring.store import SqlCheckStore
from app.monitoring.types import CheckResult, CheckTarget

BACKEND_ROOT = Path(__file__).resolve().parent.parent


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
    for name, model_field in Settings.model_fields.items():
        names.add((model_field.alias or name).upper())
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


# --- Monitoring helpers ---------------------------------------------------------------


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class TargetServer:
    """A throwaway local HTTP server (no internet needed) with scripted behaviours."""

    host: str
    port: int
    requests: list[str] = field(default_factory=list)

    def url(self, path: str = "/ok") -> str:
        return f"http://{self.host}:{self.port}{path}"

    @property
    def allow_entry(self) -> str:
        return f"{self.host}:{self.port}"


@pytest.fixture
async def target_server() -> AsyncIterator[TargetServer]:
    """Routes: /ok (200), /status/<n>, /redirect (302 -> /ok), /delay (0.25s then 200),
    /stall (never answers), /bigbody (headers, then stalls mid-body)."""
    state = TargetServer("127.0.0.1", 0)
    handlers: set[asyncio.Task[None]] = set()

    async def respond(writer: asyncio.StreamWriter, status: int, extra: str = "") -> None:
        writer.write(
            f"HTTP/1.1 {status} X\r\nContent-Length: 0\r\nConnection: close\r\n{extra}\r\n".encode()
        )
        await writer.drain()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        handlers.add(task)
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            path = head.split(b" ", 2)[1].decode()
            state.requests.append(path)
            if path == "/ok":
                await respond(writer, 200)
            elif path.startswith("/status/"):
                await respond(writer, int(path.rsplit("/", 1)[1]))
            elif path == "/redirect":
                await respond(writer, 302, "Location: /ok\r\n")
            elif path == "/delay":
                await asyncio.sleep(0.25)
                await respond(writer, 200)
            elif path == "/bigbody":
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 104857600\r\n\r\npartial")
                await writer.drain()
                await asyncio.sleep(30)
            else:  # /stall and anything unknown
                await asyncio.sleep(30)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            handlers.discard(task)
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    state.port = server.sockets[0].getsockname()[1]
    try:
        yield state
    finally:
        server.close()
        for task in list(handlers):
            task.cancel()
        await asyncio.gather(*handlers, return_exceptions=True)


@dataclass
class FakeChecker:
    """Checker stand-in: returns a canned result, optionally gated on an event."""

    result: CheckResult = field(
        default_factory=lambda: CheckResult(
            status=ResourceStatus.HEALTHY,
            http_status_code=200,
            response_time_ms=12.5,
            error=None,
            error_category=None,
            duration_ms=13.0,
        )
    )
    gate: asyncio.Event | None = None
    started: asyncio.Event = field(default_factory=asyncio.Event)
    calls: list[CheckTarget] = field(default_factory=list)

    async def check(self, target: CheckTarget) -> CheckResult:
        self.calls.append(target)
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        return self.result


# --- PostgreSQL integration fixtures --------------------------------------------------

_SAFE_DB_SUFFIXES = ("_test", "_ci")


@pytest.fixture(scope="session")
def pg_engine() -> Iterator[Engine]:
    """Engine for TEST_DATABASE_URL with the schema migrated to head.

    Tests truncate tables, so the database name must mark it as disposable; this keeps a
    mis-set TEST_DATABASE_URL from wiping a development database.
    """
    url = make_url(os.environ["TEST_DATABASE_URL"])
    if not (url.database or "").endswith(_SAFE_DB_SUFFIXES):
        pytest.exit(
            f"Refusing to run destructive tests against database {url.database!r}: "
            f"name must end with one of {_SAFE_DB_SUFFIXES}",
            returncode=2,
        )
    engine = create_engine(url, pool_pre_ping=True)
    run_alembic(engine, "upgrade", "head")
    yield engine
    engine.dispose()


def run_alembic(engine: Engine, command_name: str, revision: str) -> None:
    config = AlembicConfig(str(BACKEND_ROOT / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        getattr(alembic_command, command_name)(config, revision)


@pytest.fixture
def session_factory(pg_engine: Engine) -> sessionmaker[Session]:
    with pg_engine.begin() as connection:
        connection.execute(text("TRUNCATE resource_check_results, monitored_resources"))
    return sessionmaker(pg_engine, autoflush=False, expire_on_commit=False)


# --- API client wired to the test database ---------------------------------------------


@pytest.fixture
def checker() -> FakeChecker:
    return FakeChecker()


@pytest.fixture
def pg_client(
    settings: Settings, session_factory: sessionmaker[Session], checker: FakeChecker
) -> Iterator[TestClient]:
    app = create_app(settings)

    def get_test_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = get_test_db
    app.dependency_overrides[get_check_store] = lambda: SqlCheckStore(
        session_factory, owner="test-api", lease_grace_seconds=30
    )
    app.dependency_overrides[get_checker] = lambda: checker
    with TestClient(app) as test_client:
        yield test_client
