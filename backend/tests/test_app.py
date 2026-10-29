from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.middleware import REQUEST_ID_HEADER


def test_app_factory_builds_app(app: FastAPI) -> None:
    assert isinstance(app, FastAPI)
    assert app.title == "CloudPulse API"
    assert {"/health", "/ready"} <= set(app.openapi()["paths"])


def test_app_starts_and_serves_openapi(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert response.json()["info"]["title"] == "CloudPulse API"


def test_response_includes_generated_request_id(client: TestClient) -> None:
    response = client.get("/health")
    assert len(response.headers[REQUEST_ID_HEADER]) == 32


def test_incoming_request_id_is_propagated(client: TestClient) -> None:
    response = client.get("/health", headers={REQUEST_ID_HEADER: "trace-abc-123"})
    assert response.headers[REQUEST_ID_HEADER] == "trace-abc-123"


def test_cors_allows_configured_frontend_origin(client: TestClient) -> None:
    response = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_cors_rejects_unknown_origin(client: TestClient) -> None:
    response = client.options(
        "/health",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "access-control-allow-origin" not in response.headers


def test_disabling_api_docs_removes_every_documentation_route() -> None:
    from app.core.config import Settings
    from app.main import create_app

    app = create_app(Settings(_env_file=None, api_docs_enabled=False))  # type: ignore[call-arg]
    with TestClient(app) as client:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 404, path
        # The API itself is unaffected.
        assert client.get("/health").status_code == 200
