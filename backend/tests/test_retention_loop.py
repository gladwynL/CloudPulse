"""RetentionTask scheduling behaviour with no database."""

import asyncio
import logging

import pytest

from app.monitoring.retention import RetentionTask

pytestmark = pytest.mark.anyio


async def test_loop_runs_at_start_then_each_interval_and_stops_promptly():
    runs: list[int] = []

    class Counting(RetentionTask):
        async def purge_once(self, stop=None) -> int:
            runs.append(1)
            return 0

    task = Counting(None, retention_days=30, batch_size=10, interval_seconds=0.05)  # type: ignore[arg-type]
    stop = asyncio.Event()
    running = asyncio.create_task(task.run(stop))
    await asyncio.sleep(0.28)
    stop.set()
    await asyncio.wait_for(running, 1)  # stop interrupts the wait, not after a full interval

    assert 3 <= len(runs) <= 7  # immediate run + roughly one per 50 ms


async def test_loop_survives_failures_and_retries_sooner_than_the_interval(
    caplog: pytest.LogCaptureFixture,
):
    attempts: list[int] = []

    class Flaky(RetentionTask):
        async def purge_once(self, stop=None) -> int:
            attempts.append(1)
            if len(attempts) < 3:
                raise ConnectionError("database down (secret detail)")
            return 0

    # interval is an hour; the failure retry delay is capped at retry_seconds (0.02 s)
    task = Flaky(None, retention_days=30, batch_size=10, interval_seconds=3600, retry_seconds=0.02)  # type: ignore[arg-type]
    stop = asyncio.Event()
    with caplog.at_level(logging.ERROR, logger="cloudpulse.retention"):
        running = asyncio.create_task(task.run(stop))
        for _ in range(100):
            if len(attempts) >= 3:
                break
            await asyncio.sleep(0.02)
        stop.set()
        await asyncio.wait_for(running, 1)

    assert len(attempts) >= 3
    errors = [r for r in caplog.records if r.getMessage() == "retention cleanup failed"]
    assert len(errors) == 2 and errors[0].error == "ConnectionError"
    assert "secret detail" not in caplog.text
