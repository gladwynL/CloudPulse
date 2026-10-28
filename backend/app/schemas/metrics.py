"""Response schemas for history, summary, and time-series endpoints.

All timestamps are serialized as timezone-aware UTC. Millisecond and percentage values carry at
most two decimal places.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict

from app.models.enums import CheckSource, ErrorCategory
from app.models.resource import ResourceStatus


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


UtcDatetime = Annotated[datetime, AfterValidator(_to_utc)]

CheckStatus = Literal["HEALTHY", "DEGRADED", "DOWN"]
Interval = Literal["1m", "5m", "15m", "1h", "1d"]
# What a client may request; "auto" resolves to one of the concrete intervals.
IntervalChoice = Literal["auto", "1m", "5m", "15m", "1h", "1d"]


class TimeWindowResponse(BaseModel):
    """Half-open window: ``start`` inclusive, ``end`` exclusive."""

    start: UtcDatetime
    end: UtcDatetime


class CheckResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    checked_at: UtcDatetime
    status: ResourceStatus
    http_status_code: int | None
    response_time_ms: float | None
    duration_ms: float
    error_category: ErrorCategory | None
    error_summary: str | None
    source: CheckSource
    created_at: UtcDatetime


class CheckHistoryResponse(BaseModel):
    resource_id: uuid.UUID
    window: TimeWindowResponse
    items: list[CheckResultResponse]
    limit: int
    # Opaque cursor for the next (older) page; null on the last page.
    next_cursor: str | None


class CheckCounts(BaseModel):
    total: int
    healthy: int
    degraded: int
    down: int


class LatencyStats(BaseModel):
    """Computed over checks that received an HTTP response (HEALTHY and DEGRADED)."""

    samples: int
    avg_ms: float | None
    min_ms: float | None
    max_ms: float | None
    p50_ms: float | None
    p95_ms: float | None


class MetricsSummaryResponse(BaseModel):
    resource_id: uuid.UUID
    window: TimeWindowResponse
    checks: CheckCounts
    # healthy / total * 100, rounded half-up to 2 decimals; null when there are no checks.
    availability_percent: float | None
    latency: LatencyStats
    first_checked_at: UtcDatetime | None
    last_checked_at: UtcDatetime | None


class TimeseriesBucket(BaseModel):
    bucket_start: UtcDatetime
    total: int
    healthy: int
    degraded: int
    down: int
    availability_percent: float | None
    avg_response_time_ms: float | None
    p95_response_time_ms: float | None


class TimeseriesResponse(BaseModel):
    resource_id: uuid.UUID
    window: TimeWindowResponse
    interval: Interval
    # Only buckets that contain at least one check. A missing bucket means "no observations".
    buckets: list[TimeseriesBucket]
