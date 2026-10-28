"""Input validation for the history/metrics endpoints; no database is reached."""

import uuid
from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.session import get_db

RID = uuid.uuid4()
ENDPOINTS = ["/checks", "/metrics/summary", "/metrics/timeseries"]
START, END = "2026-03-10T12:00:00Z", "2026-03-10T13:00:00Z"


@pytest.fixture
def vclient(app: FastAPI) -> Iterator[TestClient]:
    stub = MagicMock(spec=Session)
    app.dependency_overrides[get_db] = lambda: stub
    with TestClient(app) as client:
        yield client
    stub.execute.assert_not_called()  # every case is rejected before any query


def locs(response) -> list[list[str]]:
    return [e["loc"] for e in response.json()["detail"]]


def get(client: TestClient, suffix: str, query: str = "", rid: object = RID):
    return client.get(f"/api/v1/resources/{rid}{suffix}" + (f"?{query}" if query else ""))


@pytest.mark.parametrize("suffix", ENDPOINTS)
def test_malformed_uuid(vclient: TestClient, suffix: str):
    response = get(vclient, suffix, rid="not-a-uuid")
    assert response.status_code == 422
    assert ["path", "resource_id"] in locs(response)


@pytest.mark.parametrize("suffix", ENDPOINTS)
@pytest.mark.parametrize("field", ["start", "end"])
@pytest.mark.parametrize("value", ["yesterday", "2026-13-45T00:00:00Z", "12345abc", ""])
def test_malformed_timestamps(vclient: TestClient, suffix: str, field: str, value: str):
    response = get(vclient, suffix, f"{field}={value}")
    assert response.status_code == 422
    assert ["query", field] in locs(response)


@pytest.mark.parametrize("suffix", ENDPOINTS)
def test_reversed_and_empty_windows(vclient: TestClient, suffix: str):
    for query in (f"start={END}&end={START}", f"start={START}&end={START}"):
        response = get(vclient, suffix, query)
        assert response.status_code == 422
        assert locs(response) == [["query", "start"]]
        assert "earlier than end" in response.json()["detail"][0]["msg"]


@pytest.mark.parametrize("suffix", ENDPOINTS)
def test_unreasonably_large_windows(vclient: TestClient, suffix: str):
    response = get(vclient, suffix, "start=2026-01-01T00:00:00Z&end=2026-03-01T00:00:00Z")
    assert response.status_code == 422
    assert "30 days" in response.json()["detail"][0]["msg"]


@pytest.mark.parametrize("suffix", ENDPOINTS)
def test_unsupported_query_parameters_are_rejected_not_ignored(vclient: TestClient, suffix: str):
    response = get(vclient, suffix, "bogus=1")
    assert response.status_code == 422
    assert locs(response) == [["query", "bogus"]]
    assert response.json()["detail"][0]["msg"] == "unsupported query parameter"


def test_each_endpoint_rejects_the_others_parameters(vclient: TestClient):
    assert get(vclient, "/metrics/summary", "limit=5").status_code == 422
    assert get(vclient, "/metrics/summary", "interval=5m").status_code == 422
    assert get(vclient, "/metrics/timeseries", "status=DOWN").status_code == 422
    assert get(vclient, "/checks", "interval=5m").status_code == 422


@pytest.mark.parametrize("query", ["limit=0", "limit=-1", "limit=201", "limit=abc"])
def test_page_size_bounds(vclient: TestClient, query: str):
    response = get(vclient, "/checks", query)
    assert response.status_code == 422
    assert ["query", "limit"] in locs(response)


@pytest.mark.parametrize("cursor", ["garbage", "e30", "!!!!"])
def test_invalid_cursor(vclient: TestClient, cursor: str):
    response = get(vclient, "/checks", f"cursor={cursor}")
    assert response.status_code == 422
    assert locs(response) == [["query", "cursor"]]


@pytest.mark.parametrize("status", ["UNKNOWN", "healthy", "BROKEN", ""])
def test_status_filter_accepts_only_check_outcomes(vclient: TestClient, status: str):
    response = get(vclient, "/checks", f"status={status}")
    assert response.status_code == 422
    assert ["query", "status"] in locs(response)


@pytest.mark.parametrize("interval", ["2m", "1w", "0", "FIVE", "5M"])
def test_interval_must_be_a_supported_value(vclient: TestClient, interval: str):
    response = get(vclient, "/metrics/timeseries", f"interval={interval}")
    assert response.status_code == 422
    assert ["query", "interval"] in locs(response)


def test_interval_too_fine_for_the_window_is_rejected_with_guidance(vclient: TestClient):
    response = get(
        vclient,
        "/metrics/timeseries",
        "start=2026-03-01T00:00:00Z&end=2026-03-10T00:00:00Z&interval=1m",
    )
    assert response.status_code == 422
    assert locs(response) == [["query", "interval"]]
    message = response.json()["detail"][0]["msg"]
    assert "coarser interval" in message and "1000" in message
