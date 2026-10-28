"""Retention against a disposable PostgreSQL database (see the pg_engine safety guard)."""

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.models.resource import ResourceStatus
from app.monitoring.retention import RetentionTask
from app.repositories import check_results as repo
from tests.helpers import count_checks, db_now, fetch, history, insert_check, insert_resource

pytestmark = pytest.mark.integration

Factory = sessionmaker[Session]


def make_task(factory: Factory, *, days: int = 30, batch: int = 1000) -> RetentionTask:
    return RetentionTask(
        factory, retention_days=days, batch_size=batch, interval_seconds=3600, retry_seconds=1
    )


def seed_aged(factory: Factory, resource_id, ages_days: list[float]) -> None:
    now = db_now(factory)
    for age in ages_days:
        insert_check(factory, resource_id, now - timedelta(days=age), response_time_ms=1.0)


def bulk_aged(factory: Factory, resource_id, count: int, age_days: int) -> None:
    with factory() as session:
        session.execute(
            text(
                """INSERT INTO resource_check_results
                   (id, resource_id, checked_at, status, response_time_ms, duration_ms, source)
                   SELECT gen_random_uuid(), :rid, now() - (:age * interval '1 day')
                          - (g * interval '1 second'), 'HEALTHY', 1, 1, 'SCHEDULED'
                   FROM generate_series(1, :n) g"""
            ),
            {"rid": resource_id, "age": age_days, "n": count},
        )
        session.commit()


@pytest.mark.anyio
async def test_only_results_older_than_the_retention_period_are_removed(session_factory: Factory):
    rid = insert_resource(session_factory)
    seed_aged(session_factory, rid, [45, 31, 30.5, 29.9, 1, 0.01])

    deleted = await make_task(session_factory, days=30).purge_once()

    assert deleted == 3
    ages = sorted(
        (db_now(session_factory) - r.checked_at).total_seconds() / 86400
        for r in history(session_factory, rid)
    )
    assert len(ages) == 3 and ages[-1] < 30  # the 29.9, 1 and 0.01 day-old results remain


@pytest.mark.anyio
async def test_cleanup_never_touches_resource_configuration_or_latest_state(
    session_factory: Factory,
):
    rid = insert_resource(session_factory, status=ResourceStatus.DEGRADED, name="keep-me")
    with session_factory() as session:
        session.execute(
            text(
                "UPDATE monitored_resources SET last_checked_at = now() - interval '90 days', "
                "last_error = 'expected status 200, got 503' WHERE id = :i"
            ),
            {"i": rid},
        )
        session.commit()
    before = fetch(session_factory, rid)
    seed_aged(session_factory, rid, [90, 60, 40])

    await make_task(session_factory).purge_once()

    after = fetch(session_factory, rid)
    assert count_checks(session_factory, rid) == 0
    for column in (
        "name", "url", "is_enabled", "check_interval_seconds", "timeout_seconds",
        "expected_status_code", "current_status", "last_checked_at", "next_check_at",
        "last_error", "created_at", "updated_at",
    ):  # fmt: skip
        assert getattr(after, column) == getattr(before, column), column


def test_batches_are_bounded_and_drain_the_backlog(session_factory: Factory):
    rid = insert_resource(session_factory)
    bulk_aged(session_factory, rid, count=25, age_days=40)

    sizes = []
    for _ in range(4):
        with session_factory() as session:
            sizes.append(repo.delete_expired_batch(session, retention_days=30, batch_size=10))
            session.commit()

    assert sizes == [10, 10, 5, 0]
    assert count_checks(session_factory, rid) == 0


@pytest.mark.anyio
async def test_purge_loops_over_batches_and_reports_the_total(
    session_factory: Factory, caplog: pytest.LogCaptureFixture
):
    rid = insert_resource(session_factory)
    bulk_aged(session_factory, rid, count=25, age_days=40)
    bulk_aged(session_factory, rid, count=7, age_days=1)

    with caplog.at_level(logging.INFO, logger="cloudpulse.retention"):
        deleted = await make_task(session_factory, batch=10).purge_once()

    assert deleted == 25
    assert count_checks(session_factory, rid) == 7
    (record,) = [r for r in caplog.records if r.getMessage() == "retention cleanup completed"]
    assert (record.deleted, record.batches, record.retention_days) == (25, 3, 30)


@pytest.mark.anyio
async def test_repeated_cleanup_is_idempotent(session_factory: Factory):
    rid = insert_resource(session_factory)
    seed_aged(session_factory, rid, [50, 10])
    task = make_task(session_factory)

    assert await task.purge_once() == 1
    assert await task.purge_once() == 0
    assert await task.purge_once() == 0
    assert count_checks(session_factory, rid) == 1


@pytest.mark.anyio
async def test_an_interrupted_cleanup_keeps_committed_batches_and_resumes_later(
    session_factory: Factory,
):
    rid = insert_resource(session_factory)
    bulk_aged(session_factory, rid, count=30, age_days=40)
    stop = asyncio.Event()
    task = make_task(session_factory, batch=10)
    original = task._delete_batch

    def stop_after_first_batch() -> int:
        deleted = original()
        stop.set()  # e.g. SIGTERM arrives between batches
        return deleted

    task._delete_batch = stop_after_first_batch  # type: ignore[method-assign]
    assert await task.purge_once(stop) == 10
    assert count_checks(session_factory, rid) == 20  # first batch is durable

    task._delete_batch = original  # type: ignore[method-assign]
    assert await task.purge_once() == 20  # a later run simply continues
    assert count_checks(session_factory, rid) == 0


def test_concurrent_cleaners_split_the_work_without_errors_or_double_deletes(
    session_factory: Factory,
):
    rid = insert_resource(session_factory)
    bulk_aged(session_factory, rid, count=600, age_days=40)
    bulk_aged(session_factory, rid, count=5, age_days=1)
    barrier = threading.Barrier(4)

    def cleaner() -> int:
        barrier.wait()
        total = 0
        while True:
            with session_factory() as session:
                deleted = repo.delete_expired_batch(session, retention_days=30, batch_size=50)
                session.commit()
            total += deleted
            if deleted == 0:
                return total

    with ThreadPoolExecutor(4) as pool:
        totals = list(pool.map(lambda _: cleaner(), range(4)))

    assert sum(totals) == 600  # every expired row deleted exactly once across the cleaners
    assert count_checks(session_factory, rid) == 5  # recent rows untouched


def test_cleanup_skips_rows_locked_by_an_in_flight_transaction(session_factory: Factory):
    rid = insert_resource(session_factory)
    bulk_aged(session_factory, rid, count=5, age_days=40)

    with session_factory() as holder:  # another worker is mid-delete on these rows
        holder.execute(
            text("SELECT id FROM resource_check_results WHERE resource_id = :r FOR UPDATE"),
            {"r": rid},
        )
        with session_factory() as session:
            # Fail fast instead of hanging if the cleaner ever blocks on locked rows.
            session.execute(text("SET LOCAL lock_timeout = '1500ms'"))
            deleted = repo.delete_expired_batch(session, retention_days=30, batch_size=100)
            session.commit()
        assert deleted == 0  # skipped, not blocked
    assert count_checks(session_factory, rid) == 5


def test_the_migration_itself_deletes_nothing(pg_engine, session_factory: Factory):
    """Retention is runtime policy: re-applying the history migration over data must not purge."""
    from sqlalchemy import inspect

    rid = insert_resource(session_factory)
    seed_aged(session_factory, rid, [400])  # far older than any retention period
    # The migration files contain no DELETE/TRUNCATE of check results.
    import pathlib

    versions = pathlib.Path(__file__).resolve().parent.parent / "alembic" / "versions"
    source = (next(versions.glob("*0003*.py"))).read_text(encoding="utf-8").upper()
    assert "DELETE FROM" not in source and "TRUNCATE" not in source
    assert "resource_check_results" in inspect(pg_engine).get_table_names()
    assert count_checks(session_factory, rid) == 1
