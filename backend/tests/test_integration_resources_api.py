"""Resource API against real PostgreSQL: CRUD, update semantics, status, manual checks."""

import asyncio
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_check_store, get_checker
from app.core.config import Settings
from app.db.session import get_db
from app.main import create_app
from app.models.resource import ResourceStatus
from app.monitoring.store import SqlCheckStore
from app.monitoring.types import CheckResult, ErrorCategory
from tests.conftest import FakeChecker
from tests.helpers import db_now, exists, fetch, insert_resource, seconds_from

pytestmark = pytest.mark.integration

URL = "/api/v1/resources"
VALID = {"name": "Billing API", "url": "https://billing.example.com/health"}
Factory = sessionmaker[Session]


@pytest.fixture
def checker() -> FakeChecker:
    return FakeChecker()


@pytest.fixture
def client(settings: Settings, session_factory: Factory, checker) -> Iterator[TestClient]:
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


def create(client: TestClient, **overrides) -> dict:
    response = client.post(URL, json={**VALID, **overrides})
    assert response.status_code == 201, response.text
    return response.json()


# --- Create / read ----------------------------------------------------------------------


def test_create_returns_201_with_defaults_and_schedules_first_check(
    client: TestClient, session_factory: Factory
):
    now = db_now(session_factory)
    response = client.post(URL, json={**VALID, "description": "Payments"})

    assert response.status_code == 201
    body = response.json()
    assert response.headers["location"] == f"{URL}/{body['id']}"
    assert uuid.UUID(body["id"])
    assert body["name"] == "Billing API"
    assert body["description"] == "Payments"
    assert body["url"] == "https://billing.example.com/health"
    assert body["is_enabled"] is True
    assert body["check_interval_seconds"] == 60
    assert body["timeout_seconds"] == 10
    assert body["expected_status_code"] == 200
    assert body["current_status"] == "UNKNOWN"
    assert body["last_checked_at"] is None
    assert body["last_response_time_ms"] is None
    assert body["last_http_status_code"] is None
    assert body["last_error"] is None
    assert body["created_at"] and body["updated_at"]
    row = fetch(session_factory, uuid.UUID(body["id"]))
    assert abs(seconds_from(now, row.next_check_at)) < 5  # due immediately


def test_response_does_not_leak_lease_internals(client: TestClient):
    body = create(client)
    assert not {"lease_token", "lease_owner", "lease_expires_at"} & body.keys()
    assert not {"lease_token", "lease_owner"} & client.get(f"{URL}/{body['id']}").json().keys()


def test_create_persists_explicit_values_and_survives_a_fresh_session(
    client: TestClient, session_factory: Factory
):
    body = create(
        client,
        check_interval_seconds=30,
        timeout_seconds=5,
        expected_status_code=204,
        is_enabled=True,
        url="https://svc.example.com:8443/ping?x=1#frag",
    )

    row = fetch(session_factory, uuid.UUID(body["id"]))  # "after a restart": new session
    assert (row.check_interval_seconds, row.timeout_seconds, row.expected_status_code) == (
        30,
        5,
        204,
    )
    assert row.url == "https://svc.example.com:8443/ping?x=1"  # fragment stripped
    assert client.get(f"{URL}/{body['id']}").json() == body


def test_create_disabled_resource_is_not_scheduled(client: TestClient):
    body = create(client, is_enabled=False)
    assert body["is_enabled"] is False
    assert body["next_check_at"] is None


def test_default_timeout_is_capped_by_a_short_interval(client: TestClient):
    body = create(client, check_interval_seconds=10)  # default timeout (10) fits
    assert body["timeout_seconds"] == 10


def test_create_makes_no_outbound_request(client: TestClient, checker: FakeChecker):
    create(client)
    assert checker.calls == []


def test_get_unknown_resource_is_404_with_consistent_error(client: TestClient):
    response = client.get(f"{URL}/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json() == {"detail": "Resource not found"}


# --- List -------------------------------------------------------------------------------


def test_list_is_paginated_in_deterministic_creation_order(client: TestClient):
    created = [create(client, name=f"svc-{i}")["id"] for i in range(5)]

    pages = [client.get(f"{URL}?limit=2&offset={o}").json() for o in (0, 2, 4)]

    assert [p["total"] for p in pages] == [5, 5, 5]
    assert [len(p["items"]) for p in pages] == [2, 2, 1]
    assert [i["id"] for p in pages for i in p["items"]] == created
    assert pages[1]["limit"] == 2 and pages[1]["offset"] == 2
    assert client.get(f"{URL}?limit=2&offset=2").json() == pages[1]  # stable across calls


def test_list_beyond_the_end_is_empty_but_reports_total(client: TestClient):
    create(client)
    body = client.get(f"{URL}?offset=50").json()
    assert body["items"] == [] and body["total"] == 1


def test_list_empty(client: TestClient):
    assert client.get(URL).json() == {"items": [], "total": 0, "limit": 50, "offset": 0}


def test_list_filters_by_enabled_and_status(client: TestClient, session_factory: Factory):
    create(client, name="on")
    create(client, name="off", is_enabled=False)
    insert_resource(session_factory, name="deg", status=ResourceStatus.DEGRADED)

    assert [i["name"] for i in client.get(f"{URL}?is_enabled=false").json()["items"]] == ["off"]
    assert client.get(f"{URL}?is_enabled=true").json()["total"] == 2
    degraded = client.get(f"{URL}?current_status=DEGRADED").json()
    assert [i["name"] for i in degraded["items"]] == ["deg"]
    assert degraded["total"] == 1


# --- Update -----------------------------------------------------------------------------


def test_patch_updates_only_the_supplied_fields(client: TestClient):
    original = create(client, description="old")
    response = client.patch(f"{URL}/{original['id']}", json={"name": "Renamed"})

    assert response.status_code == 200
    updated = response.json()
    assert updated["name"] == "Renamed"
    assert updated["description"] == "old"
    assert updated["url"] == original["url"]
    assert updated["updated_at"] >= original["updated_at"]
    assert client.get(f"{URL}/{original['id']}").json() == updated


def test_patch_can_clear_description_and_empty_patch_is_a_noop(client: TestClient):
    original = create(client, description="old")
    cleared = client.patch(f"{URL}/{original['id']}", json={"description": None}).json()
    assert cleared["description"] is None

    noop = client.patch(f"{URL}/{original['id']}", json={})
    assert noop.status_code == 200
    assert noop.json() == cleared


def test_patch_validates_merged_configuration(client: TestClient):
    body = create(client, check_interval_seconds=60, timeout_seconds=20)

    too_short = client.patch(f"{URL}/{body['id']}", json={"check_interval_seconds": 15})
    assert too_short.status_code == 422  # existing timeout (20) would exceed the new interval

    bad_url = client.patch(f"{URL}/{body['id']}", json={"url": "http://127.0.0.1/"})
    assert bad_url.status_code == 422
    assert client.get(f"{URL}/{body['id']}").json()["url"] == VALID["url"]  # unchanged


def test_patch_unknown_resource_is_404(client: TestClient):
    assert client.patch(f"{URL}/{uuid.uuid4()}", json={"name": "x"}).status_code == 404


def test_disabling_stops_scheduling_but_retains_the_last_observation(
    client: TestClient, session_factory: Factory
):
    body = create(client)
    client.post(f"{URL}/{body['id']}/check")  # -> HEALTHY from the fake checker

    disabled = client.patch(f"{URL}/{body['id']}", json={"is_enabled": False}).json()

    assert disabled["is_enabled"] is False
    assert disabled["next_check_at"] is None
    assert disabled["current_status"] == "HEALTHY"  # retained
    assert disabled["last_checked_at"] is not None
    assert disabled["last_response_time_ms"] == 12.5
    row = fetch(session_factory, uuid.UUID(body["id"]))
    assert row.lease_token is None


def test_enabling_schedules_an_immediate_check(client: TestClient, session_factory: Factory):
    body = create(client, is_enabled=False)
    now = db_now(session_factory)

    enabled = client.patch(f"{URL}/{body['id']}", json={"is_enabled": True}).json()

    assert enabled["is_enabled"] is True
    assert abs(seconds_from(now, fetch(session_factory, uuid.UUID(body["id"])).next_check_at)) < 5


@pytest.mark.parametrize(
    "change",
    [
        {"url": "https://other.example.com/health"},
        {"expected_status_code": 204},
        {"timeout_seconds": 5},
    ],
)
def test_changing_what_is_checked_invalidates_the_previous_observation(
    client: TestClient, change: dict
):
    body = create(client)
    client.post(f"{URL}/{body['id']}/check")

    updated = client.patch(f"{URL}/{body['id']}", json=change).json()

    assert updated["current_status"] == "UNKNOWN"
    assert updated["last_checked_at"] is None
    assert updated["last_response_time_ms"] is None
    assert updated["last_http_status_code"] is None
    assert updated["last_error"] is None
    assert updated["next_check_at"] is not None  # re-check scheduled


def test_changing_only_the_name_or_interval_keeps_the_observation(
    client: TestClient, session_factory: Factory
):
    body = create(client)
    client.post(f"{URL}/{body['id']}/check")
    checked = client.get(f"{URL}/{body['id']}").json()

    renamed = client.patch(f"{URL}/{body['id']}", json={"name": "New"}).json()
    assert renamed["current_status"] == "HEALTHY"
    assert renamed["next_check_at"] == checked["next_check_at"]

    now = db_now(session_factory)
    rescheduled = client.patch(f"{URL}/{body['id']}", json={"check_interval_seconds": 300}).json()
    assert rescheduled["current_status"] == "HEALTHY"
    assert rescheduled["check_interval_seconds"] == 300
    # relative to the last check, which just happened
    assert (
        290 <= seconds_from(now, fetch(session_factory, uuid.UUID(body["id"])).next_check_at) <= 310
    )


# --- Delete -----------------------------------------------------------------------------


def test_delete_removes_the_resource(client: TestClient, session_factory: Factory):
    body = create(client)

    assert client.delete(f"{URL}/{body['id']}").status_code == 204
    assert client.get(f"{URL}/{body['id']}").status_code == 404
    assert not exists(session_factory, uuid.UUID(body["id"]))
    assert client.delete(f"{URL}/{body['id']}").status_code == 404


# --- Status -----------------------------------------------------------------------------


def test_status_endpoint_reports_the_latest_observation(client: TestClient, checker: FakeChecker):
    body = create(client)
    initial = client.get(f"{URL}/{body['id']}/status")
    assert initial.status_code == 200
    assert initial.json()["current_status"] == "UNKNOWN"

    client.post(f"{URL}/{body['id']}/check")
    status = client.get(f"{URL}/{body['id']}/status").json()

    assert set(status) == {
        "id",
        "name",
        "is_enabled",
        "current_status",
        "last_checked_at",
        "next_check_at",
        "last_response_time_ms",
        "last_http_status_code",
        "last_error",
    }
    assert status["current_status"] == "HEALTHY"
    assert status["last_http_status_code"] == 200


def test_status_of_unknown_resource_is_404(client: TestClient):
    assert client.get(f"{URL}/{uuid.uuid4()}/status").status_code == 404


# --- Manual check -----------------------------------------------------------------------


def test_manual_check_runs_the_check_persists_and_returns_status(
    client: TestClient, checker: FakeChecker, session_factory: Factory
):
    body = create(client, check_interval_seconds=120, expected_status_code=204)
    now = db_now(session_factory)

    response = client.post(f"{URL}/{body['id']}/check")

    assert response.status_code == 200
    status = response.json()
    assert status["current_status"] == "HEALTHY"
    assert status["last_checked_at"] is not None
    assert [(c.url, c.expected_status_code) for c in checker.calls] == [
        ("https://billing.example.com/health", 204)
    ]
    row = fetch(session_factory, uuid.UUID(body["id"]))
    assert 110 <= seconds_from(now, row.next_check_at) <= 130  # timer restarts from now
    assert row.lease_token is None  # lease released


def test_manual_check_records_failures(client: TestClient, checker: FakeChecker):
    checker.result = CheckResult(
        ResourceStatus.DOWN, None, None, "dns: hostname could not be resolved", ErrorCategory.DNS, 3
    )
    body = create(client)

    status = client.post(f"{URL}/{body['id']}/check").json()

    assert status["current_status"] == "DOWN"
    assert status["last_error"] == "dns: hostname could not be resolved"
    assert status["last_response_time_ms"] is None


def test_manual_check_unknown_resource_is_404(client: TestClient, checker: FakeChecker):
    assert client.post(f"{URL}/{uuid.uuid4()}/check").status_code == 404
    assert checker.calls == []


def test_manual_check_of_disabled_resource_is_409_and_makes_no_request(
    client: TestClient, checker: FakeChecker
):
    body = create(client, is_enabled=False)

    response = client.post(f"{URL}/{body['id']}/check")

    assert response.status_code == 409
    assert "disabled" in response.json()["detail"]
    assert checker.calls == []


def test_manual_check_while_a_check_is_in_progress_is_409(
    client: TestClient, checker: FakeChecker, session_factory: Factory
):
    body = create(client)
    # A scheduled check currently holds the lease.
    store = SqlCheckStore(session_factory, owner="worker", lease_grace_seconds=30)
    asyncio.run(store.claim_due(1))

    response = client.post(f"{URL}/{body['id']}/check")

    assert response.status_code == 409
    assert "in progress" in response.json()["detail"]
    assert checker.calls == []


def test_manual_check_result_is_discarded_if_the_resource_changes_during_the_check(
    client: TestClient, settings: Settings, session_factory: Factory
):
    body = create(client)

    class MutatingChecker(FakeChecker):
        async def check(self, target):
            def delete_it() -> None:
                with session_factory() as session:
                    session.execute(text("DELETE FROM monitored_resources"))
                    session.commit()

            await asyncio.to_thread(delete_it)
            return await super().check(target)

    client.app.dependency_overrides[get_checker] = lambda: MutatingChecker()

    response = client.post(f"{URL}/{body['id']}/check")

    assert response.status_code == 409
    assert "discarded" in response.json()["detail"]
    assert not exists(session_factory, uuid.UUID(body["id"]))
