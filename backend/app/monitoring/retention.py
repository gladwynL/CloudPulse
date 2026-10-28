"""Bounded retention cleanup for historical check results, run inside the worker process.

Policy: results older than ``METRICS_RETENTION_DAYS`` (database clock) are deleted, in batches of
``METRICS_CLEANUP_BATCH_SIZE``, each batch in its own short transaction. The first run happens at
worker start and then every ``METRICS_CLEANUP_INTERVAL_HOURS`` (default 24 h); a failed run is
retried sooner. Because the schedule is "on start, then every interval", restarts never lose
cleanup: an interrupted run simply continues from whatever is still expired, and an extra run is
harmless (idempotent) and cheap (``ix_resource_check_results_checked_at``).

Deletion never touches resource configuration or latest state, only ``resource_check_results``.
Concurrent cleaners (a second worker) take disjoint batches via ``FOR UPDATE SKIP LOCKED``.
Nothing here runs from a migration.
"""

import asyncio
import contextlib
import logging
import time

from sqlalchemy.orm import Session, sessionmaker

from app.repositories import check_results as repo

logger = logging.getLogger("cloudpulse.retention")

RETRY_AFTER_FAILURE_SECONDS = 900.0


class RetentionTask:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        retention_days: int,
        batch_size: int,
        interval_seconds: float,
        retry_seconds: float = RETRY_AFTER_FAILURE_SECONDS,
    ) -> None:
        self._session_factory = session_factory
        self._retention_days = retention_days
        self._batch_size = batch_size
        self._interval = interval_seconds
        self._retry = min(retry_seconds, interval_seconds)

    async def run(self, stop: asyncio.Event) -> None:
        logger.info(
            "retention task started",
            extra={
                "retention_days": self._retention_days,
                "batch_size": self._batch_size,
                "interval_seconds": self._interval,
            },
        )
        while not stop.is_set():
            delay = self._interval
            try:
                await self.purge_once(stop)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("retention cleanup failed", extra={"error": type(exc).__name__})
                delay = self._retry
            await _sleep_or_stop(stop, delay)

    async def purge_once(self, stop: asyncio.Event | None = None) -> int:
        """Delete all currently expired results (batch by batch); returns the total removed.

        Stops early, between batches, if ``stop`` is set; committed batches stay deleted."""
        started = time.monotonic()
        total = batches = 0
        while stop is None or not stop.is_set():
            deleted = await asyncio.to_thread(self._delete_batch)
            total += deleted
            batches += 1
            if deleted < self._batch_size:
                break
            await asyncio.sleep(0)  # let checks run between batches
        logger.info(
            "retention cleanup completed",
            extra={
                "deleted": total,
                "batches": batches,
                "retention_days": self._retention_days,
                "duration_ms": round((time.monotonic() - started) * 1000, 2),
            },
        )
        return total

    def _delete_batch(self) -> int:
        with self._session_factory() as session:
            deleted = repo.delete_expired_batch(
                session, retention_days=self._retention_days, batch_size=self._batch_size
            )
            session.commit()
            return deleted


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)
