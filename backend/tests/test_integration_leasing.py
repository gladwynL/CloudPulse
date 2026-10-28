"""Lease/claim protocol and the scheduler against real PostgreSQL."""

import asyncio
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.models.resource import ResourceStatus
from app.monitoring.scheduler import Scheduler
from app.monitoring.security import TargetPolicy
from app.monitoring.store import SqlCheckStore
from app.monitoring.types import CheckResult, ClaimOutcome, ErrorCategory
from app.repositories import resources as repo
from app.schemas.resource import ResourceUpdate
from app.services import resources as service
from tests.conftest import FakeChecker
from tests.helpers import db_now, exists, expire_lease, fetch, insert_resource, seconds_from

pytestmark = pytest.mark.integration

GRACE = 30
Factory = sessionmaker[Session]


def claim_due(factory: Factory, limit: int = 10, owner: str = "t") -> list:
    with factory() as session:
        claims = repo.claim_due(session, limit=limit, owner=owner, grace_seconds=GRACE)
        session.commit()
        return claims


def claim_one(factory: Factory, resource_id: uuid.UUID):
    with factory() as session:
        outcome = repo.claim_by_id(session, resource_id, owner="t", grace_seconds=GRACE)
        session.commit()
        return outcome


def complete(factory: Factory, claim, result: CheckResult) -> bool:
    with factory() as session:
        stored = repo.complete(session, claim, result)
        session.commit()
        return stored


def healthy(ms: float = 12.345) -> CheckResult:
    return CheckResult(ResourceStatus.HEALTHY, 200, ms, None, None, 20.0)


def down() -> CheckResult:
    return CheckResult(
        ResourceStatus.DOWN,
        None,
        None,
        "timeout: no response within 5s",
        ErrorCategory.TIMEOUT,
        5000,
    )


# --- Claiming ---------------------------------------------------------------------------


def test_only_due_enabled_unleased_resources_are_claimed(session_factory: Factory):
    due = insert_resource(session_factory, name="due", due_in_seconds=-5)
    insert_resource(session_factory, name="future", due_in_seconds=300)
    insert_resource(session_factory, name="disabled", enabled=False, due_in_seconds=-5)
    insert_resource(session_factory, name="unscheduled", due_in_seconds=None)

    claims = claim_due(session_factory)

    assert [c.resource_id for c in claims] == [due]


def test_claims_are_ordered_by_due_time_and_respect_the_limit(session_factory: Factory):
    newest = insert_resource(session_factory, name="c", due_in_seconds=-1)
    oldest = insert_resource(session_factory, name="a", due_in_seconds=-300)
    middle = insert_resource(session_factory, name="b", due_in_seconds=-100)

    first = claim_due(session_factory, limit=2)
    second = claim_due(session_factory, limit=2)

    assert [c.resource_id for c in first] == [oldest, middle]
    assert [c.resource_id for c in second] == [newest]


def test_claim_stamps_a_lease_and_exposes_the_check_target(session_factory: Factory):
    resource_id = insert_resource(
        session_factory, url="https://svc.example/h", timeout=7, expected=204, interval=30
    )
    now = db_now(session_factory)

    (claim,) = claim_due(session_factory, owner="worker:x")

    assert claim.target.url == "https://svc.example/h"
    assert claim.target.timeout_seconds == 7
    assert claim.target.expected_status_code == 204
    row = fetch(session_factory, resource_id)
    assert row.lease_token == claim.lease_token
    assert row.lease_owner == "worker:x"
    # lease = timeout (7s) + grace (30s)
    assert 36 <= seconds_from(now, row.lease_expires_at) <= 39


def test_a_leased_resource_cannot_be_claimed_again(session_factory: Factory):
    insert_resource(session_factory)
    assert len(claim_due(session_factory)) == 1
    assert claim_due(session_factory) == []


def test_concurrent_claimers_never_receive_the_same_resource(session_factory: Factory):
    created = {insert_resource(session_factory, name=f"r{i}") for i in range(30)}
    barrier = threading.Barrier(8)

    def claimer() -> list[uuid.UUID]:
        barrier.wait()
        return [c.resource_id for c in claim_due(session_factory, limit=6)]

    with ThreadPoolExecutor(8) as pool:
        batches = list(pool.map(lambda _: claimer(), range(8)))

    claimed = [rid for batch in batches for rid in batch]
    assert len(claimed) == len(set(claimed)), "a resource was claimed twice"
    assert set(claimed) <= created
    assert len(claimed) == 30  # 8 claimers x 6 can cover all 30; none is lost or skipped


# --- Completing / stale results ---------------------------------------------------------


def test_complete_stores_result_schedules_next_check_and_clears_lease(session_factory: Factory):
    resource_id = insert_resource(session_factory, interval=45)
    (claim,) = claim_due(session_factory)
    now = db_now(session_factory)

    assert complete(session_factory, claim, healthy(12.345)) is True

    row = fetch(session_factory, resource_id)
    assert row.current_status is ResourceStatus.HEALTHY
    assert row.last_http_status_code == 200
    assert float(row.last_response_time_ms) == 12.35  # stored with 2 decimals
    assert row.last_error is None
    assert abs(seconds_from(now, row.last_checked_at)) < 5
    assert 40 <= seconds_from(now, row.next_check_at) <= 50  # ~ now + interval
    assert (row.lease_token, row.lease_owner, row.lease_expires_at) == (None, None, None)


def test_failure_result_clears_http_fields_and_keeps_sanitized_error(session_factory: Factory):
    resource_id = insert_resource(session_factory)
    (claim,) = claim_due(session_factory)
    complete(session_factory, claim, healthy())
    make_due(session_factory, resource_id)

    (claim,) = claim_due(session_factory)
    complete(session_factory, claim, down())

    row = fetch(session_factory, resource_id)
    assert row.current_status is ResourceStatus.DOWN
    assert row.last_http_status_code is None
    assert row.last_response_time_ms is None
    assert row.last_error == "timeout: no response within 5s"


def make_due(factory: Factory, resource_id: uuid.UUID) -> None:
    with factory() as session:
        session.execute(
            text("UPDATE monitored_resources SET next_check_at = now() WHERE id = :i"),
            {"i": resource_id},
        )
        session.commit()


def test_result_with_a_wrong_or_released_token_is_rejected(session_factory: Factory):
    resource_id = insert_resource(session_factory)
    (claim,) = claim_due(session_factory)

    with session_factory() as session:
        repo.release(session, claim)
        session.commit()

    assert complete(session_factory, claim, healthy()) is False
    assert fetch(session_factory, resource_id).current_status is ResourceStatus.UNKNOWN


def test_released_resource_is_immediately_due_again(session_factory: Factory):
    resource_id = insert_resource(session_factory)
    (claim,) = claim_due(session_factory)
    with session_factory() as session:
        repo.release(session, claim)
        session.commit()

    assert [c.resource_id for c in claim_due(session_factory)] == [resource_id]


def test_expired_lease_is_recovered_and_the_old_holder_cannot_overwrite(session_factory: Factory):
    resource_id = insert_resource(session_factory)
    (stale_claim,) = claim_due(session_factory)
    assert claim_due(session_factory) == []  # still leased

    expire_lease(session_factory, resource_id)  # the executor "crashed"
    (fresh_claim,) = claim_due(session_factory)

    assert fresh_claim.resource_id == resource_id
    assert fresh_claim.lease_token != stale_claim.lease_token
    assert complete(session_factory, fresh_claim, down()) is True
    # The original (slow, presumed-dead) holder finally finishes: its result is stale.
    assert complete(session_factory, stale_claim, healthy()) is False
    assert fetch(session_factory, resource_id).current_status is ResourceStatus.DOWN


def test_result_for_a_deleted_resource_is_discarded(session_factory: Factory):
    resource_id = insert_resource(session_factory)
    (claim,) = claim_due(session_factory)

    with session_factory() as session:
        service.delete_resource(session, resource_id)

    assert complete(session_factory, claim, healthy()) is False
    assert not exists(session_factory, resource_id)


def test_result_for_a_resource_disabled_mid_check_is_discarded_and_it_stays_unscheduled(
    session_factory: Factory, settings: Settings
):
    resource_id = insert_resource(session_factory, status=ResourceStatus.DEGRADED)
    (claim,) = claim_due(session_factory)

    with session_factory() as session:
        service.update_resource(
            session, resource_id, ResourceUpdate(is_enabled=False), settings, TargetPolicy()
        )

    assert complete(session_factory, claim, healthy()) is False
    row = fetch(session_factory, resource_id)
    assert row.current_status is ResourceStatus.DEGRADED  # prior observation retained
    assert row.next_check_at is None
    assert claim_due(session_factory) == []  # a disabled resource is never polled


def test_result_for_a_resource_reconfigured_mid_check_is_discarded(
    session_factory: Factory, settings: Settings
):
    resource_id = insert_resource(session_factory)
    (claim,) = claim_due(session_factory)

    with session_factory() as session:
        service.update_resource(
            session,
            resource_id,
            ResourceUpdate(url="https://other.example.com/health"),
            settings,
            TargetPolicy(),
        )

    assert complete(session_factory, claim, healthy()) is False  # result is for the old URL
    row = fetch(session_factory, resource_id)
    assert row.current_status is ResourceStatus.UNKNOWN
    assert [c.resource_id for c in claim_due(session_factory)] == [resource_id]  # re-checked


def test_interval_edited_mid_check_applies_to_the_next_schedule(
    session_factory: Factory, settings: Settings
):
    resource_id = insert_resource(session_factory, interval=60, timeout=10)
    (claim,) = claim_due(session_factory)
    with session_factory() as session:
        service.update_resource(
            session,
            resource_id,
            ResourceUpdate(check_interval_seconds=600),
            settings,
            TargetPolicy(),
        )
    now = db_now(session_factory)

    assert complete(session_factory, claim, healthy()) is True  # lease intact: interval only
    assert 590 <= seconds_from(now, fetch(session_factory, resource_id).next_check_at) <= 610


# --- By-id claims (manual checks) -------------------------------------------------------


def test_claim_by_id_outcomes(session_factory: Factory):
    assert claim_one(session_factory, uuid.uuid4()) is ClaimOutcome.NOT_FOUND

    disabled = insert_resource(session_factory, enabled=False)
    assert claim_one(session_factory, disabled) is ClaimOutcome.DISABLED

    future = insert_resource(session_factory, due_in_seconds=3600)  # not due: still claimable
    claim = claim_one(session_factory, future)
    assert claim.resource_id == future
    assert claim_one(session_factory, future) is ClaimOutcome.BUSY

    with session_factory() as session:
        repo.release(session, claim)
        session.commit()
    assert claim_one(session_factory, future).resource_id == future


def test_manual_claim_blocks_the_scheduler_and_vice_versa(session_factory: Factory):
    resource_id = insert_resource(session_factory)
    claim_one(session_factory, resource_id)
    assert claim_due(session_factory) == []  # scheduler cannot overlap a manual check

    other = insert_resource(session_factory, name="other")
    (scheduled,) = claim_due(session_factory)
    assert scheduled.resource_id == other
    assert claim_one(session_factory, other) is ClaimOutcome.BUSY  # and the reverse


# --- Scheduler + SqlCheckStore end to end -----------------------------------------------


def make_store(factory: Factory, owner: str = "worker:test") -> SqlCheckStore:
    return SqlCheckStore(factory, owner=owner, lease_grace_seconds=GRACE)


async def until(predicate, timeout: float = 5.0) -> None:
    async def poll() -> None:
        while not predicate():
            await asyncio.sleep(0.02)

    await asyncio.wait_for(poll(), timeout)


@pytest.mark.anyio
async def test_scheduler_checks_due_resources_persists_results_and_does_not_recheck_early(
    session_factory: Factory,
):
    due = insert_resource(session_factory, name="due", interval=60)
    future = insert_resource(session_factory, name="future", due_in_seconds=3600)
    disabled = insert_resource(session_factory, name="off", enabled=False)
    checker = FakeChecker()
    scheduler = Scheduler(
        make_store(session_factory),
        checker,
        max_concurrent=5,
        poll_interval=0.02,
        shutdown_grace=2,
    )

    stop = asyncio.Event()
    task = asyncio.create_task(scheduler.run(stop))
    await until(lambda: fetch(session_factory, due).current_status is ResourceStatus.HEALTHY)
    await asyncio.sleep(0.3)  # several more polls: nothing else may be checked
    stop.set()
    await asyncio.wait_for(task, 5)

    assert len(checker.calls) == 1
    assert fetch(session_factory, future).current_status is ResourceStatus.UNKNOWN
    assert fetch(session_factory, disabled).last_checked_at is None
    assert fetch(session_factory, due).last_checked_at is not None


@pytest.mark.anyio
async def test_scheduler_recovers_after_a_crash_left_a_lease_behind(session_factory: Factory):
    resource_id = insert_resource(session_factory)
    # A previous worker claimed it, then died before writing a result.
    await make_store(session_factory, "worker:dead").claim_due(1)
    expire_lease(session_factory, resource_id)

    checker = FakeChecker()
    scheduler = Scheduler(
        make_store(session_factory),
        checker,
        max_concurrent=2,
        poll_interval=0.02,
        shutdown_grace=2,
    )
    stop = asyncio.Event()
    task = asyncio.create_task(scheduler.run(stop))
    await until(lambda: fetch(session_factory, resource_id).last_checked_at is not None)
    stop.set()
    await asyncio.wait_for(task, 5)

    assert fetch(session_factory, resource_id).current_status is ResourceStatus.HEALTHY


@pytest.mark.anyio
async def test_two_schedulers_on_one_database_never_check_a_resource_twice(
    session_factory: Factory,
):
    """Accidental duplicate workers must not produce duplicate checks."""
    ids = {insert_resource(session_factory, name=f"r{i}") for i in range(25)}
    seen: list[str] = []

    class RecordingChecker(FakeChecker):
        async def check(self, target):
            seen.append(target.url)
            await asyncio.sleep(0.01)
            return await super().check(target)

    schedulers = [
        Scheduler(
            make_store(session_factory, f"worker:{n}"),
            RecordingChecker(),
            max_concurrent=4,
            poll_interval=0.01,
            shutdown_grace=2,
        )
        for n in range(2)
    ]
    stop = asyncio.Event()
    tasks = [asyncio.create_task(s.run(stop)) for s in schedulers]
    await until(
        lambda: all(
            fetch(session_factory, rid).current_status is ResourceStatus.HEALTHY for rid in ids
        ),
        timeout=15,
    )
    await asyncio.sleep(0.2)
    stop.set()
    await asyncio.wait_for(asyncio.gather(*tasks), 5)

    assert len(seen) == 25  # each resource exactly once (next check is a minute away)
