"""Small builders shared by the PostgreSQL integration tests."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, text
from sqlalchemy.orm import Session, sessionmaker

from app.models.resource import MonitoredResource, ResourceStatus


def insert_resource(
    factory: sessionmaker[Session],
    *,
    name: str = "svc",
    url: str = "https://example.com/health",
    enabled: bool = True,
    interval: int = 60,
    timeout: int = 10,
    expected: int = 200,
    due_in_seconds: float | None = -1,
    status: ResourceStatus = ResourceStatus.UNKNOWN,
) -> uuid.UUID:
    """Insert a row directly. ``due_in_seconds`` is relative to the database clock;
    negative means already due, None means never scheduled."""
    resource = MonitoredResource(
        name=name,
        url=url,
        is_enabled=enabled,
        check_interval_seconds=interval,
        timeout_seconds=timeout,
        expected_status_code=expected,
        current_status=status,
        next_check_at=(
            None if due_in_seconds is None else func.now() + timedelta(seconds=due_in_seconds)
        ),
    )
    with factory() as session:
        session.add(resource)
        session.commit()
        return resource.id


def fetch(factory: sessionmaker[Session], resource_id: uuid.UUID) -> MonitoredResource:
    with factory() as session:
        row = session.get(MonitoredResource, resource_id)
        assert row is not None
        session.expunge(row)
        return row


def exists(factory: sessionmaker[Session], resource_id: uuid.UUID) -> bool:
    with factory() as session:
        return session.get(MonitoredResource, resource_id) is not None


def expire_lease(factory: sessionmaker[Session], resource_id: uuid.UUID) -> None:
    """Simulate a crashed executor: its lease runs out without a result being written."""
    with factory() as session:
        session.execute(
            text(
                "UPDATE monitored_resources SET lease_expires_at = now() - interval '1 second' "
                "WHERE id = :id"
            ),
            {"id": resource_id},
        )
        session.commit()


def db_now(factory: sessionmaker[Session]) -> datetime:
    with factory() as session:
        return session.execute(text("SELECT now()")).scalar_one()


def seconds_from(now: datetime, moment: datetime | None) -> float:
    assert moment is not None
    return (moment.astimezone(UTC) - now.astimezone(UTC)).total_seconds()
