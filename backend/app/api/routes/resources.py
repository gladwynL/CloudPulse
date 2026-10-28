"""Monitored-resource endpoints under ``/api/v1/resources``.

These endpoints are unauthenticated in Phase 1. Do not expose the API to an untrusted network.
"""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Response, status
from fastapi.concurrency import run_in_threadpool

from app.api.deps import (
    AppSettings,
    CheckerDep,
    CheckStoreDep,
    DbSession,
    PolicyDep,
    reject_unknown_query_params,
)
from app.api.errors import CheckConflictError
from app.models.resource import ResourceStatus
from app.monitoring.executor import execute_claimed_check
from app.monitoring.types import CheckSource, ClaimOutcome
from app.schemas.metrics import (
    CheckCounts,
    CheckHistoryResponse,
    CheckResultResponse,
    CheckStatus,
    IntervalChoice,
    LatencyStats,
    MetricsSummaryResponse,
    TimeseriesBucket,
    TimeseriesResponse,
    TimeWindowResponse,
)
from app.schemas.resource import (
    ResourceCreate,
    ResourceListResponse,
    ResourceResponse,
    ResourceStatusResponse,
    ResourceUpdate,
)
from app.services import metrics as metrics_service
from app.services import resources as service

router = APIRouter(prefix="/resources", tags=["resources"])

_NOT_FOUND = {status.HTTP_404_NOT_FOUND: {"description": "Resource not found"}}


@router.post("", response_model=ResourceResponse, status_code=status.HTTP_201_CREATED)
def create_resource(
    body: ResourceCreate,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    policy: PolicyDep,
) -> ResourceResponse:
    """Register a service. No outbound request is made here; the first check is scheduled."""
    resource = service.create_resource(session, body, settings, policy)
    response.headers["Location"] = f"/api/v1/resources/{resource.id}"
    return ResourceResponse.model_validate(resource)


@router.get("", response_model=ResourceListResponse)
def list_resources(
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    is_enabled: bool | None = None,
    current_status: ResourceStatus | None = None,
) -> ResourceListResponse:
    """List resources ordered by creation time (then id), oldest first."""
    page = service.list_resources(
        session, limit=limit, offset=offset, is_enabled=is_enabled, status=current_status
    )
    return ResourceListResponse(
        items=[ResourceResponse.model_validate(r) for r in page.items],
        total=page.total,
        limit=limit,
        offset=offset,
    )


@router.get("/{resource_id}", response_model=ResourceResponse, responses=_NOT_FOUND)
def get_resource(resource_id: uuid.UUID, session: DbSession) -> ResourceResponse:
    return ResourceResponse.model_validate(service.get_resource(session, resource_id))


@router.patch("/{resource_id}", response_model=ResourceResponse, responses=_NOT_FOUND)
def update_resource(
    resource_id: uuid.UUID,
    body: ResourceUpdate,
    session: DbSession,
    settings: AppSettings,
    policy: PolicyDep,
) -> ResourceResponse:
    """Update configuration or enable/disable monitoring (see README for exact semantics)."""
    resource = service.update_resource(session, resource_id, body, settings, policy)
    return ResourceResponse.model_validate(resource)


@router.delete("/{resource_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_NOT_FOUND)
def delete_resource(resource_id: uuid.UUID, session: DbSession) -> Response:
    service.delete_resource(session, resource_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{resource_id}/status", response_model=ResourceStatusResponse, responses=_NOT_FOUND)
def get_resource_status(resource_id: uuid.UUID, session: DbSession) -> ResourceStatusResponse:
    """Current health: the latest observation only (no history in Phase 1)."""
    return ResourceStatusResponse.model_validate(service.get_resource(session, resource_id))


@router.post(
    "/{resource_id}/check",
    response_model=ResourceStatusResponse,
    responses={
        **_NOT_FOUND,
        status.HTTP_409_CONFLICT: {"description": "Disabled, already checking, or changed"},
    },
)
async def trigger_check(
    resource_id: uuid.UUID,
    session: DbSession,
    store: CheckStoreDep,
    checker: CheckerDep,
) -> ResourceStatusResponse:
    """Run a check now and return the resulting status.

    Uses the same lease as scheduled checks, so it cannot overlap one: if a check is already
    running the response is 409. Execution time is bounded by the resource's timeout.
    """
    claim = await store.claim(resource_id)
    if claim is ClaimOutcome.NOT_FOUND:
        raise service.ResourceNotFoundError
    if claim is ClaimOutcome.DISABLED:
        raise CheckConflictError("Resource is disabled; enable it to run checks")
    if claim is ClaimOutcome.BUSY:
        raise CheckConflictError("A check for this resource is already in progress")

    outcome = await execute_claimed_check(store, checker, claim, CheckSource.MANUAL)
    if not outcome.stored:
        raise CheckConflictError(
            "Resource was changed or deleted during the check; result discarded"
        )

    resource = await run_in_threadpool(service.get_resource, session, resource_id)
    return ResourceStatusResponse.model_validate(resource)


# --- History and metrics ----------------------------------------------------------------

_WINDOW_NOTE = (
    "Timestamps are ISO 8601; naive values are read as UTC (use a trailing `Z`, since a literal "
    "`+` in a query string must be URL-encoded). The window is `[start, end)`: start inclusive, "
    "end exclusive."
)


@router.get(
    "/{resource_id}/checks",
    response_model=CheckHistoryResponse,
    responses=_NOT_FOUND,
    dependencies=[
        reject_unknown_query_params("start", "end", "status", "limit", "cursor"),
    ],
    summary="Historical check results",
)
def list_checks(
    resource_id: uuid.UUID,
    session: DbSession,
    settings: AppSettings,
    start: datetime | None = None,
    end: datetime | None = None,
    status_filter: Annotated[CheckStatus | None, Query(alias="status")] = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> CheckHistoryResponse:
    """Newest first, ordered by `(checked_at, id)` descending. Pagination is keyset-based: pass
    the returned `next_cursor` as `cursor` to get the next (older) page. Defaults to the last
    24 hours."""
    page = metrics_service.get_history(
        session,
        resource_id,
        settings,
        start=start,
        end=end,
        status=ResourceStatus(status_filter) if status_filter else None,
        limit=limit,
        cursor=cursor,
    )
    return CheckHistoryResponse(
        resource_id=resource_id,
        window=TimeWindowResponse(start=page.window.start, end=page.window.end),
        items=[CheckResultResponse.model_validate(row) for row in page.items],
        limit=page.limit,
        next_cursor=page.next_cursor,
    )


@router.get(
    "/{resource_id}/metrics/summary",
    response_model=MetricsSummaryResponse,
    responses=_NOT_FOUND,
    dependencies=[reject_unknown_query_params("start", "end")],
    summary="Availability and latency summary",
)
def get_metrics_summary(
    resource_id: uuid.UUID,
    session: DbSession,
    settings: AppSettings,
    start: datetime | None = None,
    end: datetime | None = None,
) -> MetricsSummaryResponse:
    """Check-based availability (`healthy / total * 100`) and latency statistics over a window.
    Availability is observation-based, not continuously measured uptime. Latency covers checks
    that received an HTTP response."""
    summary = metrics_service.get_summary(session, resource_id, settings, start=start, end=end)
    row = summary.row
    f = metrics_service.to_float
    return MetricsSummaryResponse(
        resource_id=resource_id,
        window=TimeWindowResponse(start=summary.window.start, end=summary.window.end),
        checks=CheckCounts(
            total=row.total, healthy=row.healthy, degraded=row.degraded, down=row.down
        ),
        availability_percent=summary.availability_percent,
        latency=LatencyStats(
            samples=row.latency_samples,
            avg_ms=f(row.avg_ms),
            min_ms=f(row.min_ms),
            max_ms=f(row.max_ms),
            p50_ms=f(row.p50_ms),
            p95_ms=f(row.p95_ms),
        ),
        first_checked_at=row.first_checked_at,
        last_checked_at=row.last_checked_at,
    )


@router.get(
    "/{resource_id}/metrics/timeseries",
    response_model=TimeseriesResponse,
    responses=_NOT_FOUND,
    dependencies=[reject_unknown_query_params("start", "end", "interval")],
    summary="Time-bucketed metrics",
)
def get_metrics_timeseries(
    resource_id: uuid.UUID,
    session: DbSession,
    settings: AppSettings,
    start: datetime | None = None,
    end: datetime | None = None,
    interval: IntervalChoice = "auto",
) -> TimeseriesResponse:
    """Per-bucket counts, availability, average and p95 latency. Buckets are UTC-aligned
    (`1m`, `5m`, `15m`, `1h`, `1d`); `auto` picks the finest interval within the bucket limit.
    Only buckets containing at least one check are returned: a missing bucket means no
    observations, **not** downtime. The first/last bucket may be partial."""
    series = metrics_service.get_timeseries(
        session, resource_id, settings, start=start, end=end, interval=interval
    )
    f = metrics_service.to_float
    return TimeseriesResponse(
        resource_id=resource_id,
        window=TimeWindowResponse(start=series.window.start, end=series.window.end),
        interval=series.interval,
        buckets=[
            TimeseriesBucket(
                bucket_start=row.bucket_start,
                total=row.total,
                healthy=row.healthy,
                degraded=row.degraded,
                down=row.down,
                availability_percent=metrics_service.availability_percent(row.healthy, row.total),
                avg_response_time_ms=f(row.avg_ms),
                p95_response_time_ms=f(row.p95_ms),
            )
            for row in series.rows
        ],
    )
