"""Monitored-resource endpoints under ``/api/v1/resources``.

These endpoints are unauthenticated in Phase 1. Do not expose the API to an untrusted network.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status
from fastapi.concurrency import run_in_threadpool

from app.api.deps import AppSettings, CheckerDep, CheckStoreDep, DbSession, PolicyDep
from app.api.errors import CheckConflictError
from app.models.resource import ResourceStatus
from app.monitoring.executor import execute_claimed_check
from app.monitoring.types import CheckSource, ClaimOutcome
from app.schemas.resource import (
    ResourceCreate,
    ResourceListResponse,
    ResourceResponse,
    ResourceStatusResponse,
    ResourceUpdate,
)
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
