"""Polling scheduler: claims due resources and runs their checks with bounded concurrency.

The database is the source of truth for what is due (``next_check_at``) and who is working
on it (the lease), so the loop holds no authoritative state: after a restart it simply
claims whatever is due. Running it in more than one process is safe, only wasteful,
because leases are exclusive; the supported deployment is a single worker.
"""

import asyncio
import logging
import uuid
from collections.abc import Callable

from app.monitoring.executor import Checker, execute_claimed_check
from app.monitoring.store import CheckStore
from app.monitoring.types import CheckSource, ClaimedCheck

logger = logging.getLogger("cloudpulse.scheduler")

MAX_BACKOFF_SECONDS = 30.0


class Scheduler:
    def __init__(
        self,
        store: CheckStore,
        checker: Checker,
        *,
        max_concurrent: int,
        poll_interval: float,
        shutdown_grace: float,
        on_healthy_tick: Callable[[], None] | None = None,
    ) -> None:
        self._store = store
        self._checker = checker
        self._max_concurrent = max_concurrent
        self._poll_interval = poll_interval
        self._shutdown_grace = shutdown_grace
        self._on_healthy_tick = on_healthy_tick
        self._tasks: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._capacity_freed = asyncio.Event()

    @property
    def in_flight(self) -> int:
        return len(self._tasks)

    async def run(self, stop: asyncio.Event) -> None:
        """Poll until ``stop`` is set, then wait for in-flight checks (up to the grace period)."""
        logger.info(
            "scheduler started",
            extra={
                "max_concurrent": self._max_concurrent,
                "poll_interval_seconds": self._poll_interval,
            },
        )
        failures = 0
        try:
            while not stop.is_set():
                try:
                    await self._tick()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # e.g. database briefly unavailable: log, back off, keep running.
                    failures += 1
                    logger.error(
                        "scheduler poll failed",
                        extra={"error": type(exc).__name__, "consecutive_failures": failures},
                    )
                else:
                    failures = 0
                    if self._on_healthy_tick is not None:
                        self._on_healthy_tick()

                delay = (
                    self._poll_interval
                    if failures == 0
                    else min(self._poll_interval * 2**failures, MAX_BACKOFF_SECONDS)
                )
                await self._wait(stop, delay)
        finally:
            await self._drain()
            logger.info("scheduler stopped")

    async def _tick(self) -> None:
        free = self._max_concurrent - len(self._tasks)
        if free <= 0:
            return
        claims = await self._store.claim_due(free)
        if claims:
            logger.info(
                "claimed due checks",
                extra={"count": len(claims), "in_flight": len(self._tasks) + len(claims)},
            )
        for claim in claims:
            if claim.resource_id in self._tasks:
                # Leases make this impossible; guard anyway so a check never overlaps itself.
                logger.warning(
                    "claim for resource already in flight; ignored",
                    extra={"resource_id": str(claim.resource_id)},
                )
                continue
            task = asyncio.create_task(self._run_one(claim), name=f"check-{claim.resource_id}")
            self._tasks[claim.resource_id] = task
            task.add_done_callback(lambda _t, rid=claim.resource_id: self._finished(rid))

    async def _run_one(self, claim: ClaimedCheck) -> None:
        try:
            await execute_claimed_check(self._store, self._checker, claim, CheckSource.SCHEDULED)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # One failed check (e.g. a DB error while saving) must never stop the loop. The
            # lease expires and the resource is retried.
            logger.error(
                "scheduled check failed",
                extra={"resource_id": str(claim.resource_id), "error": type(exc).__name__},
            )

    def _finished(self, resource_id: uuid.UUID) -> None:
        was_full = len(self._tasks) >= self._max_concurrent
        self._tasks.pop(resource_id, None)
        if was_full:
            # Only wake the loop early when a backlog may be waiting for a slot.
            self._capacity_freed.set()

    async def _wait(self, stop: asyncio.Event, delay: float) -> None:
        stop_waiter = asyncio.create_task(stop.wait())
        capacity_waiter = asyncio.create_task(self._capacity_freed.wait())
        try:
            await asyncio.wait(
                {stop_waiter, capacity_waiter},
                timeout=delay,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            stop_waiter.cancel()
            capacity_waiter.cancel()
            self._capacity_freed.clear()

    async def _drain(self) -> None:
        pending_tasks = list(self._tasks.values())
        if not pending_tasks:
            return
        logger.info(
            "waiting for in-flight checks",
            extra={"count": len(pending_tasks), "grace_seconds": self._shutdown_grace},
        )
        _, still_running = await asyncio.wait(pending_tasks, timeout=self._shutdown_grace)
        if still_running:
            logger.warning(
                "cancelling checks still running after grace period",
                extra={"count": len(still_running)},
            )
            for task in still_running:
                task.cancel()  # each executor releases its lease before exiting
            await asyncio.gather(*still_running, return_exceptions=True)
