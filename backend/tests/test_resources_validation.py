"""Request validation. Runs without a database: every case is rejected before any query."""

from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.session import get_db

VALID = {"name": "Billing API", "url": "https://billing.example.com/health"}
URL = "/api/v1/resources"
SOME_ID = "6f9c1d1e-5a1e-4a52-9d52-2d6a0f4f2e11"


@pytest.fixture
def validation_client(app: FastAPI) -> Iterator[TestClient]:
    stub = MagicMock(spec=Session)
    app.dependency_overrides[get_db] = lambda: stub
    with TestClient(app) as client:
        yield client
    stub.execute.assert_not_called()  # nothing reached the database


def error_locs(response) -> list[list[str]]:
    return [error["loc"] for error in response.json()["detail"]]


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/",
        "file:///etc/passwd",
        "http://user:pass@example.com/",
        "http://127.0.0.1/",
        "http://localhost:8000/",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/",
        "http://metadata.google.internal/",
        "not a url",
        "example.com",
        "http://2130706433/",
    ],
)
def test_create_rejects_unsafe_or_malformed_urls(validation_client: TestClient, url: str):
    response = validation_client.post(URL, json={**VALID, "url": url})

    assert response.status_code == 422
    assert ["body", "url"] in error_locs(response)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("check_interval_seconds", 5),  # below configured minimum (10)
        ("check_interval_seconds", 0),
        ("check_interval_seconds", -60),
        ("check_interval_seconds", 86_401),  # above configured maximum
        ("check_interval_seconds", "often"),
        ("timeout_seconds", 0),
        ("timeout_seconds", 31),  # above configured maximum (30)
        ("expected_status_code", 99),
        ("expected_status_code", 600),
        ("name", ""),
        ("name", "   "),
        ("name", "x" * 101),
        ("description", "x" * 501),
        ("is_enabled", "maybe"),
    ],
)
def test_create_rejects_invalid_fields(validation_client: TestClient, field: str, value: object):
    response = validation_client.post(URL, json={**VALID, field: value})

    assert response.status_code == 422
    assert ["body", field] in error_locs(response)


def test_create_rejects_timeout_longer_than_interval(validation_client: TestClient):
    response = validation_client.post(
        URL, json={**VALID, "check_interval_seconds": 15, "timeout_seconds": 20}
    )
    assert response.status_code == 422
    assert ["body", "timeout_seconds"] in error_locs(response)


def test_create_requires_name_and_url_and_rejects_unknown_fields(validation_client: TestClient):
    assert validation_client.post(URL, json={}).status_code == 422
    assert validation_client.post(URL, json={"name": "x"}).status_code == 422
    unknown = validation_client.post(URL, json={**VALID, "method": "POST"})
    assert unknown.status_code == 422  # arbitrary HTTP methods are out of scope
    assert (
        validation_client.post(URL, json={**VALID, "current_status": "HEALTHY"}).status_code == 422
    )


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", ""),
        ("patch", ""),
        ("delete", ""),
        ("get", "/status"),
        ("post", "/check"),
    ],
)
def test_malformed_uuid_is_rejected(validation_client: TestClient, method: str, path: str):
    response = getattr(validation_client, method)(f"{URL}/not-a-uuid{path}")

    assert response.status_code == 422
    assert ["path", "resource_id"] in error_locs(response)


@pytest.mark.parametrize(
    "body",
    [
        {"name": None},
        {"url": None},
        {"is_enabled": None},
        {"check_interval_seconds": None},
        {"timeout_seconds": None},
        {"expected_status_code": None},
        {"current_status": "DOWN"},
        {"id": SOME_ID},
        {"name": ""},
        {"expected_status_code": 700},
    ],
)
def test_patch_rejects_nulls_unknown_and_invalid_fields(validation_client: TestClient, body: dict):
    response = validation_client.patch(f"{URL}/{SOME_ID}", json=body)
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("query", "bad"),
    [("limit=0", "limit"), ("limit=201", "limit"), ("offset=-1", "offset"), ("limit=abc", "limit")],
)
def test_list_rejects_invalid_pagination(validation_client: TestClient, query: str, bad: str):
    response = validation_client.get(f"{URL}?{query}")
    assert response.status_code == 422
    assert ["query", bad] in error_locs(response)


def test_list_rejects_unknown_status_filter(validation_client: TestClient):
    assert validation_client.get(f"{URL}?current_status=BROKEN").status_code == 422
