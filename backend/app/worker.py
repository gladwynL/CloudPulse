"""Monitoring worker process: ``python -m app.worker``.

Runs the polling scheduler. The API process never polls, so scaling the API to several
workers cannot produce duplicate scheduled checks. Run exactly one worker (see README);
extra workers are safe (leases are exclusive) but unsupported.

``python -m app.worker --healthcheck`` exits 0 if the scheduler completed a database poll
recently; Docker uses it as the container health check.
"""

import argparse
import asyncio
import contextlib
import logging
import os
import signal
import socket
import sys
import time
from pathlib import Path

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.session import get_engine, get_session_factory
from app.monitoring.checker import HttpChecker
from app.monitoring.scheduler import Scheduler
from app.monitoring.security import TargetPolicy
from app.monitoring.store import SqlCheckStore

logger = logging.getLogger("cloudpulse.worker")

HEARTBEAT_MAX_AGE_SECONDS = 60.0


def _heartbeat(path: str) -> None:
    # A missing heartbeat only affects the container health check.
    with contextlib.suppress(OSError):
        Path(path).touch()


def _install_signal_handlers(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
    def request_stop(*_: object) -> None:
        logger.info("shutdown signal received")
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_stop)
        except NotImplementedError:  # Windows event loops
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))


async def run_worker(settings: Settings) -> None:
    owner = f"worker:{socket.gethostname()}:{os.getpid()}"
    policy = TargetPolicy(frozenset(settings.monitoring_allowed_local_hosts))
    store = SqlCheckStore(
        get_session_factory(),
        owner=owner,
        lease_grace_seconds=settings.monitoring_lease_grace_seconds,
    )
    checker = HttpChecker(policy, max_connections=settings.monitoring_max_concurrent_checks)
    heartbeat_path = settings.monitoring_heartbeat_file
    scheduler = Scheduler(
        store,
        checker,
        max_concurrent=settings.monitoring_max_concurrent_checks,
        poll_interval=settings.monitoring_poll_interval_seconds,
        shutdown_grace=settings.monitoring_shutdown_grace_seconds,
        on_healthy_tick=lambda: _heartbeat(heartbeat_path),
    )

    stop = asyncio.Event()
    _install_signal_handlers(asyncio.get_running_loop(), stop)
    logger.info(
        "worker starting",
        extra={
            "owner": owner,
            "environment": settings.environment,
            "local_target_allowlist_size": len(settings.monitoring_allowed_local_hosts),
        },
    )
    if settings.monitoring_allowed_local_hosts:
        logger.warning(
            "private-address allowlist is active (development only)",
            extra={"hosts": settings.monitoring_allowed_local_hosts},
        )
    try:
        await scheduler.run(stop)
    finally:
        await checker.aclose()
        get_engine().dispose()
        logger.info("worker stopped")


def healthcheck(settings: Settings) -> int:
    try:
        age = time.time() - Path(settings.monitoring_heartbeat_file).stat().st_mtime
    except OSError:
        return 1
    return 0 if age <= HEARTBEAT_MAX_AGE_SECONDS else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.worker")
    parser.add_argument("--healthcheck", action="store_true", help="exit 0 if the worker is alive")
    args = parser.parse_args(argv)

    settings = get_settings()
    if args.healthcheck:
        return healthcheck(settings)

    configure_logging(
        level=settings.log_level,
        fmt=settings.log_format,
        service="cloudpulse-worker",
        environment=settings.environment,
    )
    asyncio.run(run_worker(settings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
