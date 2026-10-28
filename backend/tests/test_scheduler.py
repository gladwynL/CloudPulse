"""Scheduler and executor behaviour with an in-memory store (no database, no network)."""

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

import pytest

from app.models.resource import ResourceStatus
from app.monitoring.executor import execute_claimed_check
from app.monitoring.scheduler import Scheduler
from app.monitoring.types import (
    CheckResult,
    CheckSource,
    CheckTarget,
    ClaimedCheck,
    ClaimOutcome,
    ErrorCategory,
)
from tests.conftest import FakeChecker

pytestmark = pytest.mark.anyio


@dataclass
class FakeStore:
    """In-memory CheckStore. Models leases: a claimed resource is not claimable again."""

    pending: list[uuid.UUID] = field(default_factory=list)
    url: str = "https://example.com/health"
    ignore_leases: bool = False  # simulate a misbehaving store that re-offers leased rows
    claim_failures: int = 0  # raise from the next N claim_due calls
    complete_fails_for: set[uuid.UUID] = field(default_factory=set)

    leased: dict[uuid.UUID, uuid.UUID] = field(default_factory=dict)  # resource -> token
    completed: dict[uuid.UUID, CheckResult] = field(default_factory=dict)
    released: list[uuid.UUID] = field(default_factory=list)
    claim_limits: list[int] = field(default_factory=list)

    async def claim_due(self, limit: int) -> list[ClaimedCheck]:
        self.claim_limits.append(limit)
        if self.claim_failures > 0:
            self.claim_failures -= 1
            raise ConnectionError("database unavailable")
        claims: list[ClaimedCheck] = []
        for resource_id in list(self.pending):
            if len(claims) >= limit:
                break
            if resource_id in self.leased and not self.ignore_leases:
                continue
            token = uuid.uuid4()
            self.leased[resource_id] = token
            claims.append(
                ClaimedCheck(resource_id, CheckTarget(self.url, 5, 200), token, "test-owner")
            )
        return claims

    async def claim(self, resource_id: uuid.UUID) -> ClaimedCheck | ClaimOutcome:
        raise NotImplementedError

    async def complete(self, claim: ClaimedCheck, result: CheckResult) -> bool:
        if claim.resource_id in self.complete_fails_for:
            raise ConnectionError("database unavailable")
        if self.leased.get(claim.resource_id) != claim.lease_token:
            return False
        del self.leased[claim.resource_id]
        self.pending.remove(claim.resource_id)  # rescheduled in the future, no longer due
        self.completed[claim.resource_id] = result
        return True

    async def release(self, claim: ClaimedCheck) -> None:
        if self.leased.get(claim.resource_id) == claim.lease_token:
            del self.leased[claim.resource_id]
        self.released.append(claim.resource_id)


async def until(predicate: Callable[[], bool], timeout: float = 3.0) -> None:
    async def poll() -> None:
        while not predicate():
            await asyncio.sleep(0.005)

    await asyncio.wait_for(poll(), timeout)


@contextlib.asynccontextmanager
async def running(scheduler: Scheduler) -> AsyncIterator[asyncio.Event]:
    stop = asyncio.Event()
    task = asyncio.create_task(scheduler.run(stop))
    try:
        yield stop
    finally:
        stop.set()
        await asyncio.wait_for(task, 5)


def make_scheduler(store: FakeStore, checker, **overrides) -> Scheduler:
    params = {"max_concurrent": 5, "poll_interval": 0.01, "shutdown_grace": 1.0}
    params.update(overrides)
    return Scheduler(store, checker, **params)


def ids(n: int) -> list[uuid.UUID]:
    return [uuid.uuid4() for _ in range(n)]


# --- Scheduling -------------------------------------------------------------------------


async def test_due_resources_are_checked_and_results_stored():
    due = ids(3)
    store, checker = FakeStore(pending=list(due)), FakeChecker()

    async with running(make_scheduler(store, checker)):
        await until(lambda: len(store.completed) == 3)

    assert set(store.completed) == set(due)
    assert all(r.status is ResourceStatus.HEALTHY for r in store.completed.values())
    assert len(checker.calls) == 3


async def test_idle_scheduler_does_no_work():
    store, checker = FakeStore(), FakeChecker()
    healthy_ticks = []

    async with running(
        make_scheduler(store, checker, on_healthy_tick=lambda: healthy_ticks.append(1))
    ):
        await until(lambda: len(healthy_ticks) >= 3)

    assert checker.calls == []


async def test_concurrency_is_bounded_and_backlog_still_drains():
    due = ids(8)
    store = FakeStore(pending=list(due))
    active = max_active = 0

    class CountingChecker(FakeChecker):
        async def check(self, target):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            try:
                await asyncio.sleep(0.03)
                return await super().check(target)
            finally:
                active -= 1

    async with running(make_scheduler(store, CountingChecker(), max_concurrent=3)):
        await until(lambda: len(store.completed) == 8)

    assert max_active == 3
    assert max(store.claim_limits) <= 3  # never asks for more than the free slots


async def test_claim_limit_reflects_free_slots_while_checks_are_in_flight():
    store = FakeStore(pending=ids(2))
    checker = FakeChecker(gate=asyncio.Event())

    async with running(make_scheduler(store, checker, max_concurrent=2)) as _:
        await until(lambda: len(checker.calls) == 2)
        store.claim_limits.clear()
        await asyncio.sleep(0.1)
        assert store.claim_limits == []  # at capacity: the scheduler does not even poll
        checker.gate.set()


async def test_a_resource_never_overlaps_itself_even_if_the_store_re_offers_it():
    resource = uuid.uuid4()
    store = FakeStore(pending=[resource], ignore_leases=True)
    checker = FakeChecker(gate=asyncio.Event())

    async with running(make_scheduler(store, checker)):
        await until(lambda: len(checker.calls) == 1)
        await asyncio.sleep(0.15)  # many poll ticks while the first check is still running
        assert len(checker.calls) == 1
        checker.gate.set()
        await until(lambda: resource in store.completed)


# --- Failure handling -------------------------------------------------------------------


async def test_loop_survives_store_failures_and_recovers():
    store = FakeStore(pending=ids(1), claim_failures=3)
    healthy_ticks: list[int] = []

    async with running(
        make_scheduler(store, FakeChecker(), on_healthy_tick=lambda: healthy_ticks.append(1))
    ):
        await until(lambda: len(store.completed) == 1)

    assert healthy_ticks  # heartbeat only after a successful poll


async def test_failure_persisting_one_result_does_not_affect_others(
    caplog: pytest.LogCaptureFixture,
):
    bad, good = ids(2)
    store = FakeStore(pending=[bad, good], complete_fails_for={bad})

    async with running(make_scheduler(store, FakeChecker())):
        await until(lambda: good in store.completed)
        await until(lambda: any("scheduled check failed" in r.message for r in caplog.records))

    assert bad not in store.completed
    assert bad in store.leased  # lease is left to expire, so the resource is retried later


async def test_checker_exception_is_recorded_as_internal_failure_not_a_crash():
    class ExplodingChecker:
        async def check(self, target):
            raise RuntimeError("boom with secret details")

    resource = uuid.uuid4()
    store = FakeStore(pending=[resource])

    async with running(make_scheduler(store, ExplodingChecker())):
        await until(lambda: resource in store.completed)

    result = store.completed[resource]
    assert result.status is ResourceStatus.DOWN
    assert result.error_category is ErrorCategory.INTERNAL
    assert "secret" not in (result.error or "")


# --- Shutdown ---------------------------------------------------------------------------


async def test_graceful_shutdown_lets_in_flight_checks_finish_and_store_results():
    resource = uuid.uuid4()
    store = FakeStore(pending=[resource])
    checker = FakeChecker(gate=asyncio.Event())
    stop = asyncio.Event()
    run = asyncio.create_task(make_scheduler(store, checker, shutdown_grace=5).run(stop))

    await asyncio.wait_for(checker.started.wait(), 2)
    stop.set()
    await asyncio.sleep(0.1)
    assert not run.done()  # still draining the in-flight check

    checker.gate.set()
    await asyncio.wait_for(run, 2)
    assert resource in store.completed
    assert store.released == []


async def test_in_flight_checks_are_cancelled_and_leases_released_after_the_grace_period():
    resource = uuid.uuid4()
    store = FakeStore(pending=[resource])
    checker = FakeChecker(gate=asyncio.Event())  # never opened: simulates a hung check
    stop = asyncio.Event()
    run = asyncio.create_task(make_scheduler(store, checker, shutdown_grace=0.05).run(stop))

    await asyncio.wait_for(checker.started.wait(), 2)
    stop.set()
    await asyncio.wait_for(run, 2)

    assert store.released == [resource]
    assert resource not in store.completed
    assert resource not in store.leased  # due again immediately after restart


async def test_restarted_scheduler_picks_up_work_left_by_a_previous_instance():
    due = ids(2)
    store = FakeStore(pending=list(due))  # state lives in the store, not the scheduler

    first = make_scheduler(store, FakeChecker(), max_concurrent=1)
    stop = asyncio.Event()
    stop.set()  # "process stopped" before doing anything
    await first.run(stop)
    assert store.completed == {}

    async with running(make_scheduler(store, FakeChecker())):
        await until(lambda: set(store.completed) == set(due))


# --- Executor ---------------------------------------------------------------------------


def claim_for(
    resource_id: uuid.UUID, store: FakeStore, url: str = "https://example.com/"
) -> ClaimedCheck:
    claim = ClaimedCheck(resource_id, CheckTarget(url, 5, 200), uuid.uuid4(), "test")
    store.leased[resource_id] = claim.lease_token
    store.pending.append(resource_id)
    return claim


async def test_executor_logs_structured_fields_without_the_full_url(
    caplog: pytest.LogCaptureFixture,
):
    resource = uuid.uuid4()
    store = FakeStore()
    claim = claim_for(resource, store, url="https://api.example.com/v1/health?token=SECRET123")

    with caplog.at_level(logging.INFO, logger="cloudpulse.monitoring"):
        outcome = await execute_claimed_check(store, FakeChecker(), claim, CheckSource.MANUAL)

    assert outcome.stored is True
    (record,) = [r for r in caplog.records if r.name == "cloudpulse.monitoring"]
    assert record.resource_id == str(resource)
    assert record.check_source == "manual"
    assert record.check_status == "HEALTHY"
    assert record.http_status_code == 200
    assert record.response_time_ms == 12.5
    assert record.duration_ms == 13.0
    assert record.target_host == "api.example.com"
    assert "SECRET123" not in caplog.text
    assert "/v1/health" not in caplog.text


async def test_executor_reports_a_discarded_result_when_the_lease_was_lost(
    caplog: pytest.LogCaptureFixture,
):
    resource = uuid.uuid4()
    store = FakeStore()
    claim = claim_for(resource, store)
    store.leased[resource] = uuid.uuid4()  # resource was reconfigured/reclaimed meanwhile

    with caplog.at_level(logging.INFO, logger="cloudpulse.monitoring"):
        outcome = await execute_claimed_check(store, FakeChecker(), claim, CheckSource.SCHEDULED)

    assert outcome.stored is False
    assert resource not in store.completed
    assert any(r.levelno == logging.WARNING and r.stored is False for r in caplog.records)


async def test_executor_releases_the_lease_when_cancelled():
    resource = uuid.uuid4()
    store = FakeStore()
    claim = claim_for(resource, store)
    checker = FakeChecker(gate=asyncio.Event())

    task = asyncio.create_task(execute_claimed_check(store, checker, claim, CheckSource.MANUAL))
    await asyncio.wait_for(checker.started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert store.released == [resource]
