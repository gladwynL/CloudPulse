from collections.abc import Callable
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.services.health import check_database


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_does_not_use_database(
    client: TestClient,
    override_db: Callable[[Session], None],
    failing_session: MagicMock,
) -> None:
    override_db(failing_session)
    response = client.get("/health")
    assert response.status_code == 200
    failing_session.execute.assert_not_called()


def test_ready_returns_ready_when_database_reachable(
    client: TestClient,
    override_db: Callable[[Session], None],
    healthy_session: MagicMock,
) -> None:
    override_db(healthy_session)
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"database": "ok"}}
    healthy_session.execute.assert_called_once()


def test_ready_returns_503_when_database_unreachable(
    client: TestClient,
    override_db: Callable[[Session], None],
    failing_session: MagicMock,
) -> None:
    override_db(failing_session)
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "checks": {"database": "unavailable"}}


def test_ready_failure_does_not_leak_error_details(
    client: TestClient,
    override_db: Callable[[Session], None],
    failing_session: MagicMock,
) -> None:
    override_db(failing_session)
    response = client.get("/ready")
    assert "connection refused" not in response.text


def test_check_database_true_on_success(healthy_session: MagicMock) -> None:
    assert check_database(healthy_session) is True


def test_check_database_false_on_sqlalchemy_error(failing_session: MagicMock) -> None:
    assert check_database(failing_session) is False
