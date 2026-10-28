"""Queries over historical check results.

All windows are half-open ``[start, end)`` on ``checked_at`` (inclusive start, exclusive end), so
adjacent windows never double-count a check. Every query filters on ``resource_id`` first and so
is served by ``ix_resource_check_results_resource_checked_at_id``. Aggregates are computed in
PostgreSQL; nothing here loads a result set into Python beyond one page of rows.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import (
    DateTime,
    Interval,
    Numeric,
    Select,
    cast,
    delete,
    func,
    literal,
    select,
    tuple_,
)
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from app.models.check_result import ResourceCheckResult as R
from app.models.resource import MonitoredResource, ResourceStatus

# Bucket boundaries are aligned to this UTC origin (PostgreSQL ``date_bin``). It is midnight UTC
# on a day boundary, so every supported interval (1m..1d) lands on clock boundaries.
BUCKET_ORIGIN = datetime(2000, 1, 1, tzinfo=UTC)

_LATENCY = R.response_time_ms


def resource_exists(session: Session, resource_id: uuid.UUID) -> bool:
    return (
        session.execute(
            select(MonitoredResource.id).where(MonitoredResource.id == resource_id)
        ).first()
        is not None
    )


def _window(resource_id: uuid.UUID, start: datetime, end: datetime) -> list:
    return [R.resource_id == resource_id, R.checked_at >= start, R.checked_at < end]


def list_checks_stmt(
    resource_id: uuid.UUID,
    *,
    start: datetime,
    end: datetime,
    status: ResourceStatus | None,
    limit: int,
    after: tuple[datetime, uuid.UUID] | None,
) -> Select:
    """Newest first with a stable tie-break. ``after`` is a keyset cursor: rows strictly older
    than that ``(checked_at, id)`` position. Fetches ``limit`` rows; callers pass limit + 1 to
    learn whether another page exists."""
    stmt = select(R).where(*_window(resource_id, start, end))
    if status is not None:
        stmt = stmt.where(R.status == status)
    if after is not None:
        stmt = stmt.where(tuple_(R.checked_at, R.id) < tuple_(after[0], after[1]))
    return stmt.order_by(R.checked_at.desc(), R.id.desc()).limit(limit)


def list_checks(session: Session, resource_id: uuid.UUID, **kwargs) -> list[R]:
    return list(session.execute(list_checks_stmt(resource_id, **kwargs)).scalars())


def _percentile(fraction: float):
    return func.round(cast(func.percentile_cont(fraction).within_group(_LATENCY), Numeric), 2)


def summary_stmt(resource_id: uuid.UUID, start: datetime, end: datetime) -> Select:
    """One aggregate row for the window. Latency aggregates ignore NULL response times
    (checks that never received a response), so they cover HEALTHY and DEGRADED responses."""
    return select(
        func.count().label("total"),
        func.count().filter(R.status == ResourceStatus.HEALTHY).label("healthy"),
        func.count().filter(R.status == ResourceStatus.DEGRADED).label("degraded"),
        func.count().filter(R.status == ResourceStatus.DOWN).label("down"),
        func.count(_LATENCY).label("latency_samples"),
        func.round(func.avg(_LATENCY), 2).label("avg_ms"),
        func.min(_LATENCY).label("min_ms"),
        func.max(_LATENCY).label("max_ms"),
        _percentile(0.5).label("p50_ms"),
        _percentile(0.95).label("p95_ms"),
        func.min(R.checked_at).label("first_checked_at"),
        func.max(R.checked_at).label("last_checked_at"),
    ).where(*_window(resource_id, start, end))


def summary(session: Session, resource_id: uuid.UUID, start: datetime, end: datetime) -> Row:
    return session.execute(summary_stmt(resource_id, start, end)).one()


def timeseries_stmt(
    resource_id: uuid.UUID, start: datetime, end: datetime, interval: timedelta
) -> Select:
    """Per-bucket aggregates for buckets that contain at least one check (sparse: empty buckets
    are simply absent). Buckets are aligned to ``BUCKET_ORIGIN`` in UTC."""
    bucket = func.date_bin(
        cast(literal(interval), Interval),
        R.checked_at,
        cast(literal(BUCKET_ORIGIN), DateTime(True)),
    ).label("bucket_start")
    stmt = (
        select(
            bucket,
            func.count().label("total"),
            func.count().filter(R.status == ResourceStatus.HEALTHY).label("healthy"),
            func.count().filter(R.status == ResourceStatus.DEGRADED).label("degraded"),
            func.count().filter(R.status == ResourceStatus.DOWN).label("down"),
            func.round(func.avg(_LATENCY), 2).label("avg_ms"),
            _percentile(0.95).label("p95_ms"),
        )
        .where(*_window(resource_id, start, end))
        .group_by(bucket)
        .order_by(bucket)
    )
    return stmt


def timeseries(
    session: Session,
    resource_id: uuid.UUID,
    start: datetime,
    end: datetime,
    interval: timedelta,
) -> list[Row]:
    return list(session.execute(timeseries_stmt(resource_id, start, end, interval)).all())


def delete_expired_batch(session: Session, *, retention_days: int, batch_size: int) -> int:
    """Delete up to ``batch_size`` results older than the retention period; returns the count.

    ``FOR UPDATE SKIP LOCKED`` makes concurrent cleaners (e.g. two workers) take disjoint rows
    instead of blocking or double-deleting, and the caller commits per batch so locks are short.
    The cutoff is computed on the database clock.
    """
    expired = (
        select(R.id)
        .where(R.checked_at < func.now() - timedelta(days=retention_days))
        .order_by(R.checked_at)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )
    result = session.execute(
        delete(R).where(R.id.in_(expired)).execution_options(synchronize_session=False)
    )
    return result.rowcount
