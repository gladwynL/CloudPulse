"""Alert and incident request validation. No database is reached: every case is rejected first."""

import uuid
from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.session import get_db

RID = uuid.uuid4()
OTHER = uuid.uuid4()
RULES = f"/api/v1/resources/{RID}/alert-rules"


@pytest.fixture
def vclient(app: FastAPI) -> Iterator[TestClient]:
    stub = MagicMock(spec=Session)
    app.dependency_overrides[get_db] = lambda: stub
    with TestClient(app) as client:
        yield client
    stub.execute.assert_not_called()


def locs(response) -> list[list]:
    return [e["loc"] for e in response.json()["detail"]]


def post(client: TestClient, **body):
    return client.post(RULES, json=body)


GOOD_FAILURES = {"name": "r", "rule_type": "CONSECUTIVE_FAILURES", "consecutive_failures": 3}
GOOD_LATENCY = {
    "name": "r",
    "rule_type": "LATENCY_THRESHOLD",
    "operator": "GREATER_THAN",
    "threshold": 1000,
    "consecutive_failures": 3,
}
GOOD_STATUS = {"name": "r", "rule_type": "STATUS", "status_condition": "DOWN"}


@pytest.mark.parametrize(
    ("body", "field"),
    [
        # required fields per rule type
        ({"name": "r", "rule_type": "CONSECUTIVE_FAILURES"}, "consecutive_failures"),
        ({**GOOD_LATENCY, "threshold": None}, "threshold"),
        ({**GOOD_LATENCY, "operator": None}, "operator"),
        ({**GOOD_LATENCY, "consecutive_failures": None}, "consecutive_failures"),
        ({"name": "r", "rule_type": "STATUS"}, "status_condition"),
        # irrelevant fields are rejected, not silently ignored
        ({**GOOD_FAILURES, "threshold": 500}, "threshold"),
        ({**GOOD_FAILURES, "operator": "GREATER_THAN"}, "operator"),
        ({**GOOD_FAILURES, "status_condition": "DOWN"}, "status_condition"),
        ({**GOOD_STATUS, "consecutive_failures": 2}, "consecutive_failures"),
        ({**GOOD_STATUS, "threshold": 10}, "threshold"),
        ({**GOOD_STATUS, "operator": "GREATER_THAN"}, "operator"),
        ({**GOOD_LATENCY, "status_condition": "DOWN"}, "status_condition"),
    ],
)
def test_contradictory_or_incomplete_rule_configuration_is_rejected(
    vclient: TestClient, body: dict, field: str
):
    response = vclient.post(RULES, json=body)
    assert response.status_code == 422
    assert ["body", field] in locs(response)


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"consecutive_failures": 0}, "consecutive_failures"),
        ({"consecutive_failures": 21}, "consecutive_failures"),
        ({"consecutive_failures": "three"}, "consecutive_failures"),
        ({"recovery_successes": 0}, "recovery_successes"),
        ({"recovery_successes": 21}, "recovery_successes"),
        ({"severity": "FATAL"}, "severity"),
        ({"severity": "warning"}, "severity"),
        ({"name": ""}, "name"),
        ({"name": "   "}, "name"),
        ({"name": "x" * 101}, "name"),
        ({"description": "x" * 501}, "description"),
        ({"is_enabled": "maybe"}, "is_enabled"),
    ],
)
def test_invalid_common_fields(vclient: TestClient, overrides: dict, field: str):
    response = vclient.post(RULES, json={**GOOD_FAILURES, **overrides})
    assert response.status_code == 422
    assert ["body", field] in locs(response)


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"threshold": 0}, "threshold"),
        ({"threshold": -5}, "threshold"),
        ({"threshold": 600_001}, "threshold"),
        ({"threshold": 10.123}, "threshold"),  # more than two decimals
        ({"threshold": "fast"}, "threshold"),
        ({"operator": "LESS_THAN"}, "operator"),
        ({"operator": "=="}, "operator"),
    ],
)
def test_invalid_latency_settings(vclient: TestClient, overrides: dict, field: str):
    response = vclient.post(RULES, json={**GOOD_LATENCY, **overrides})
    assert response.status_code == 422
    assert ["body", field] in locs(response)


def test_unsupported_rule_types_conditions_and_unknown_fields(vclient: TestClient):
    assert post(vclient, **{**GOOD_FAILURES, "rule_type": "ANOMALY"}).status_code == 422
    assert post(vclient, **{**GOOD_STATUS, "status_condition": "SLOW"}).status_code == 422
    assert post(vclient, **{**GOOD_FAILURES, "expression": "rt > 5"}).status_code == 422
    assert post(vclient, **{**GOOD_FAILURES, "current_state": "FIRING"}).status_code == 422
    assert vclient.post(RULES, json={}).status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"name": None},
        {"is_enabled": None},
        {"severity": None},
        {"recovery_successes": None},
        {"rule_type": "STATUS"},  # the type cannot be changed
        {"current_state": "OK"},
        {"failure_streak": 0},
        {"threshold": 0},
        {"consecutive_failures": 99},
        {"severity": "FATAL"},
    ],
)
def test_patch_rejects_nulls_immutable_and_invalid_fields(vclient: TestClient, body: dict):
    assert vclient.patch(f"/api/v1/alert-rules/{OTHER}", json=body).status_code == 422


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/api/v1/resources/nope/alert-rules"),
        ("get", "/api/v1/resources/nope/alert-rules"),
        ("get", "/api/v1/alert-rules/nope"),
        ("patch", "/api/v1/alert-rules/nope"),
        ("delete", "/api/v1/alert-rules/nope"),
        ("get", "/api/v1/incidents/nope"),
        ("post", "/api/v1/incidents/nope/acknowledge"),
        ("get", "/api/v1/incidents/nope/events"),
    ],
)
def test_malformed_uuids_are_rejected(vclient: TestClient, method: str, path: str):
    response = getattr(vclient, method)(path)
    assert response.status_code == 422
    assert any(loc[0] == "path" for loc in locs(response))


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "bogus=1"])
def test_rule_list_pagination_is_bounded(vclient: TestClient, query: str):
    assert vclient.get(f"{RULES}?{query}").status_code == 422


@pytest.mark.parametrize(
    "query",
    [
        "status=CLOSED",
        "status=open",
        "severity=FATAL",
        "resource_id=nope",
        "start=yesterday",
        "end=2026-99-99",
        "start=2026-03-10T12:00:00Z&end=2026-03-10T11:00:00Z",
        "start=2026-03-10T12:00:00Z&end=2026-03-10T12:00:00Z",
        "limit=0",
        "limit=201",
        "limit=abc",
        "cursor=garbage",
        "offset=5",
        "sort=asc",
    ],
)
def test_incident_list_rejects_invalid_and_unsupported_parameters(vclient: TestClient, query: str):
    response = vclient.get(f"/api/v1/incidents?{query}")
    assert response.status_code == 422
    assert all(loc[0] == "query" for loc in locs(response))
