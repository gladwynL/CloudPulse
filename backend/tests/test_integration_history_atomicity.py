"""History persistence is atomic with the latest-state update and never records discarded results.

Everything here runs against real PostgreSQL: the guarantees are transactional and concurrent.
"""

import asyncio
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.models.enums import CheckSource, ErrorCategory
from app.models.resource import ResourceStatus
from app.monitoring.scheduler import Scheduler
from app.monitoring.security import TargetPolicy
from app.monitoring.store import SqlCheckStore
from app.monitoring.types import CheckResult, ClaimedCheck
from app.repositories import resources as repo
from app.schemas.resource import ResourceUpdate
from app.services import resources as service
from tests.conftest import FakeChecker
from tests.helpers import (
    count_checks,
    expire_lease,
    fetch,
    history,
    insert_check,
    insert_resource,
    make_due,
)

pytestmark = pytest.mark.integration

Factory = sessionmaker[Session]
GRACE = 30


def claim_due(factory: Factory, limit: int = 10) -> list[ClaimedCheck]:
    with factory() as session:
        claims = repo.claim_due(session, limit=limit, owner="t", grace_seconds=GRACE)
        session.commit()
        return claims


def complete(
    factory: Factory,
    claim: ClaimedCheck,
    result: CheckResult,
    source: CheckSource = CheckSource.SCHEDULED,
) -> bool:
    with factory() as session:
        stored = repo.complete(session, claim, result, source)
        session.commit()
        return stored


def healthy(rt: float = 12.345, duration: float = 20.0) -> CheckResult:
    return CheckResult(ResourceStatus.HEALTHY, 200, rt, None, None, duration)


def down() -> CheckResult:
    return CheckResult(
        ResourceStatus.DOWN,
        None,
        None,
        "timeout: no response within 5s",
        ErrorCategory.TIMEOUT,
        5001.5,
    )


# --- The happy path writes both, together -------------------------------------------------


def test_accepted_result_updates_state_and_appends_one_history_row(session_factory: Factory):
    resource_id = insert_resource(session_factory)
    (claim,) = claim_due(session_factory)

    assert complete(session_factory, claim, healthy()) is True

    resource = fetch(session_factory, resource_id)
    (row,) = history(session_factory, resource_id)
    assert resource.current_status is ResourceStatus.HEALTHY
    # One transaction timestamp: the event time of the history row IS the resource's last check.
    assert row.checked_at == resource.last_checked_at
    assert row.created_at >= row.checked_at
    assert row.status is ResourceStatus.HEALTHY
    assert row.http_status_code == 200
    assert row.response_time_ms == Decimal("12.35")  # rounded once, to two decimals
    assert row.duration_ms == Decimal("20.00")
    assert (row.error_category, row.error_summary) == (None, None)
    assert row.source is CheckSource.SCHEDULED


def test_failure_result_is_recorded_with_sanitized_error_and_no_latency(session_factory: Factory):
    resource_id = insert_resource(session_factory)
    (claim,) = claim_due(session_factory)

    complete(session_factory, claim, down(), CheckSource.MANUAL)

    (row,) = history(session_factory, resource_id)
    assert row.status is ResourceStatus.DOWN
    assert row.http_status_code is None
    assert row.response_time_ms is None  # latency and duration are distinct measurements
    assert row.duration_ms == Decimal("5001.50")
    assert row.error_category is ErrorCategory.TIMEOUT
    assert row.error_summary == "timeout: no response within 5s"
    assert row.source is CheckSource.MANUAL


def test_response_time_and_duration_are_not_conflated(session_factory: Factory):
    resource_id = insert_resource(session_factory)
    (claim,) = claim_due(session_factory)
    complete(session_factory, claim, healthy(rt=5.0, duration=900.0))

    (row,) = history(session_factory, resource_id)
    assert (row.response_time_ms, row.duration_ms) == (Decimal("5.00"), Decimal("900.00"))


def test_each_check_appends_a_row_and_history_survives_state_changes(
    session_factory: Factory, settings: Settings
):
    resource_id = insert_resource(session_factory, interval=60)
    for _ in range(3):
        (claim,) = claim_due(session_factory)
        complete(session_factory, claim, healthy())
        make_due(session_factory, resource_id)
    assert count_checks(session_factory, resource_id) == 3

    # Disabling and reconfiguring change the *latest* state, never the recorded history.
    with session_factory() as session:
        service.update_resource(
            session, resource_id, ResourceUpdate(is_enabled=False), settings, TargetPolicy()
        )
    with session_factory() as session:
        service.update_resource(
            session,
            resource_id,
            ResourceUpdate(url="https://other.example.com/"),
            settings,
            TargetPolicy(),
        )
    assert fetch(session_factory, resource_id).current_status is ResourceStatus.UNKNOWN
    assert count_checks(session_factory, resource_id) == 3


# --- Failure rolls both writes back -------------------------------------------------------


def test_a_failed_history_insert_rolls_back_the_state_update_too(session_factory: Factory):
    resource_id = insert_resource(session_factory)
    (claim,) = claim_due(session_factory)
    invalid = CheckResult(ResourceStatus.HEALTHY, 200, 5.0, None, None, -1.0)  # violates CHECK

    with pytest.raises(IntegrityError):
        complete(session_factory, claim, invalid)

    resource = fetch(session_factory, resource_id)
    assert resource.current_status is ResourceStatus.UNKNOWN  # state update undone
    assert resource.last_checked_at is None
    assert resource.last_http_status_code is None
    assert resource.lease_token == claim.lease_token  # lease untouched: it will expire, retry
    assert count_checks(session_factory) == 0


def test_after_a_failed_write_the_lease_expires_and_the_check_is_retried(session_factory: Factory):
    resource_id = insert_resource(session_factory)
    (claim,) = claim_due(session_factory)
    with pytest.raises(IntegrityError):
        complete(session_factory, claim, CheckResult(ResourceStatus.DOWN, 999, None, None, None, 1))

    expire_lease(session_factory, resource_id)
    (retry,) = claim_due(session_factory)
    assert complete(session_factory, retry, healthy()) is True
    assert count_checks(session_factory, resource_id) == 1


@pytest.mark.parametrize(
    "bad",
    [
        CheckResult(ResourceStatus.UNKNOWN, None, None, None, None, 1.0),  # not a result
        CheckResult(ResourceStatus.HEALTHY, 99, 1.0, None, None, 1.0),  # status code range
        CheckResult(ResourceStatus.HEALTHY, 200, -0.5, None, None, 1.0),  # negative latency
    ],
)
def test_invalid_measurements_are_rejected_and_leave_no_trace(session_factory: Factory, bad):
    resource_id = insert_resource(session_factory)
    (claim,) = claim_due(session_factory)

    with pytest.raises(IntegrityError):
        complete(session_factory, claim, bad)

    assert count_checks(session_factory) == 0
    assert fetch(session_factory, resource_id).current_status is ResourceStatus.UNKNOWN


# --- Discarded results never become history -----------------------------------------------


def test_result_with_a_stale_token_inserts_nothing(session_factory: Factory):
    resource_id = insert_resource(session_factory)
    (claim,) = claim_due(session_factory)
    with session_factory() as session:
        repo.release(session, claim)
        session.commit()

    assert complete(session_factory, claim, healthy()) is False
    assert count_checks(session_factory, resource_id) == 0


def test_expired_lease_holder_cannot_add_a_duplicate_measurement(session_factory: Factory):
    resource_id = insert_resource(session_factory)
    (old,) = claim_due(session_factory)
    expire_lease(session_factory, resource_id)
    (new,) = claim_due(session_factory)

    assert complete(session_factory, new, down()) is True
    assert complete(session_factory, old, healthy()) is False  # slow, presumed-dead holder

    (row,) = history(session_factory, resource_id)  # exactly one: the accepted result
    assert row.status is ResourceStatus.DOWN


def test_completing_the_same_claim_twice_records_one_measurement(session_factory: Factory):
    resource_id = insert_resource(session_factory)
    (claim,) = claim_due(session_factory)

    assert complete(session_factory, claim, healthy()) is True
    assert complete(session_factory, claim, healthy()) is False

    assert count_checks(session_factory, resource_id) == 1


def test_simultaneous_duplicate_completions_record_exactly_one_measurement(
    session_factory: Factory,
):
    resource_id = insert_resource(session_factory)
    (claim,) = claim_due(session_factory)
    barrier = threading.Barrier(6)

    def attempt() -> bool:
        barrier.wait()
        return complete(session_factory, claim, healthy())

    with ThreadPoolExecutor(6) as pool:
        outcomes = list(pool.map(lambda _: attempt(), range(6)))

    assert sorted(outcomes) == [False] * 5 + [True]
    assert count_checks(session_factory, resource_id) == 1


def test_result_for_a_deleted_resource_cannot_create_an_orphan(session_factory: Factory):
    resource_id = insert_resource(session_factory)
    (claim,) = claim_due(session_factory)
    with session_factory() as session:
        service.delete_resource(session, resource_id)

    assert complete(session_factory, claim, healthy()) is False
    assert count_checks(session_factory) == 0


def test_result_for_a_resource_disabled_mid_check_is_not_recorded_but_old_history_stays(
    session_factory: Factory, settings: Settings
):
    resource_id = insert_resource(session_factory)
    (first,) = claim_due(session_factory)
    complete(session_factory, first, healthy())
    make_due(session_factory, resource_id)
    (second,) = claim_due(session_factory)
    with session_factory() as session:
        service.update_resource(
            session, resource_id, ResourceUpdate(is_enabled=False), settings, TargetPolicy()
        )

    assert complete(session_factory, second, down()) is False

    rows = history(session_factory, resource_id)
    assert [r.status for r in rows] == [ResourceStatus.HEALTHY]  # only the first, accepted check


def test_result_for_a_resource_reconfigured_mid_check_is_not_recorded(
    session_factory: Factory, settings: Settings
):
    resource_id = insert_resource(session_factory)
    (claim,) = claim_due(session_factory)
    with session_factory() as session:
        service.update_resource(
            session,
            resource_id,
            ResourceUpdate(expected_status_code=204),
            settings,
            TargetPolicy(),
        )

    assert complete(session_factory, claim, healthy()) is False
    assert count_checks(session_factory, resource_id) == 0


# --- Competing workers ---------------------------------------------------------------------


def test_competing_claimers_record_each_check_exactly_once(session_factory: Factory):
    ids = [insert_resource(session_factory, name=f"r{i}") for i in range(24)]
    barrier = threading.Barrier(6)

    def worker() -> int:
        barrier.wait()
        done = 0
        while claims := claim_due(session_factory, limit=3):
            for claim in claims:
                done += complete(session_factory, claim, healthy())
        return done

    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(lambda _: worker(), range(6)))

    assert sum(results) == 24
    rows = history(session_factory)
    assert len(rows) == 24
    assert {r.resource_id for r in rows} == set(ids)  # one per resource, none doubled


@pytest.mark.anyio
async def test_two_schedulers_produce_one_history_row_per_check(session_factory: Factory):
    ids = [insert_resource(session_factory, name=f"r{i}") for i in range(20)]
    schedulers = [
        Scheduler(
            SqlCheckStore(session_factory, owner=f"w{n}", lease_grace_seconds=GRACE),
            FakeChecker(),
            max_concurrent=4,
            poll_interval=0.01,
            shutdown_grace=2,
        )
        for n in range(2)
    ]
    stop = asyncio.Event()
    tasks = [asyncio.create_task(s.run(stop)) for s in schedulers]

    async def until_all_checked() -> None:
        while count_checks(session_factory) < 20:
            await asyncio.sleep(0.02)

    await asyncio.wait_for(until_all_checked(), 15)
    await asyncio.sleep(0.3)  # next checks are a minute away: nothing more may be recorded
    stop.set()
    await asyncio.wait_for(asyncio.gather(*tasks), 5)

    rows = history(session_factory)
    assert len(rows) == 20
    assert {r.resource_id for r in rows} == set(ids)
    assert {r.source for r in rows} == {CheckSource.SCHEDULED}


# --- Foreign key and cascade ---------------------------------------------------------------


def test_deleting_a_resource_deletes_its_history_and_only_its_history(
    session_factory: Factory,
):
    mine, other = (
        insert_resource(session_factory, name="a"),
        insert_resource(session_factory, name="b"),
    )
    when = datetime(2026, 3, 10, 12, tzinfo=UTC)
    for resource_id in (mine, mine, other):
        insert_check(session_factory, resource_id, when, response_time_ms=1.0)

    with session_factory() as session:
        service.delete_resource(session, mine)

    assert count_checks(session_factory, mine) == 0  # intentional cascade: no orphans
    assert count_checks(session_factory, other) == 1


def test_history_cannot_reference_a_missing_resource(session_factory: Factory):
    with pytest.raises(IntegrityError):
        insert_check(
            session_factory, uuid.uuid4(), datetime(2026, 3, 10, tzinfo=UTC), response_time_ms=1.0
        )
