"""Alert rule and incident endpoints.

Unauthenticated in this phase: do not expose the API to an untrusted network. ``MANUAL`` actors
in the incident timeline mean "an API caller", not a verified identity.
"""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.api.deps import AppSettings, DbSession, reject_unknown_query_params
from app.models.enums import AlertSeverity, AlertState, IncidentStatus
from app.schemas.alerts import (
    AlertRuleCreate,
    AlertRuleGlobalListResponse,
    AlertRuleListItem,
    AlertRuleListResponse,
    AlertRuleResponse,
    AlertRuleUpdate,
    IncidentEventResponse,
    IncidentListResponse,
    IncidentResponse,
    IncidentTimelineResponse,
)
from app.services import alerts as service

router = APIRouter(tags=["alerting"])

_RULE_404 = {status.HTTP_404_NOT_FOUND: {"description": "Alert rule not found"}}
_RESOURCE_404 = {status.HTTP_404_NOT_FOUND: {"description": "Resource not found"}}
_INCIDENT_404 = {status.HTTP_404_NOT_FOUND: {"description": "Incident not found"}}


# --- Alert rules ----------------------------------------------------------------------------


@router.post(
    "/resources/{resource_id}/alert-rules",
    response_model=AlertRuleResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        **_RESOURCE_404,
        status.HTTP_409_CONFLICT: {"description": "Rule limit for the resource reached"},
    },
)
def create_alert_rule(
    resource_id: uuid.UUID,
    body: AlertRuleCreate,
    response: Response,
    session: DbSession,
    settings: AppSettings,
) -> AlertRuleResponse:
    """Create a rule. It starts in `INSUFFICIENT_DATA` and evaluates only future accepted checks."""
    rule = service.create_rule(session, resource_id, body, settings)
    response.headers["Location"] = f"/api/v1/alert-rules/{rule.id}"
    return AlertRuleResponse.model_validate(rule)


@router.get(
    "/resources/{resource_id}/alert-rules",
    response_model=AlertRuleListResponse,
    responses=_RESOURCE_404,
    dependencies=[reject_unknown_query_params("limit", "offset")],
)
def list_alert_rules(
    resource_id: uuid.UUID,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AlertRuleListResponse:
    """A resource's rules in creation order (then id)."""
    items, total = service.list_rules(session, resource_id, limit=limit, offset=offset)
    return AlertRuleListResponse(
        items=[AlertRuleResponse.model_validate(r) for r in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/alert-rules",
    response_model=AlertRuleGlobalListResponse,
    dependencies=[
        reject_unknown_query_params(
            "resource_id", "current_state", "severity", "is_enabled", "limit", "offset"
        )
    ],
    summary="List alert rules across all resources",
)
def list_all_alert_rules(
    session: DbSession,
    resource_id: uuid.UUID | None = None,
    current_state: AlertState | None = None,
    severity: AlertSeverity | None = None,
    is_enabled: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AlertRuleGlobalListResponse:
    """Every rule with its resource's name, oldest first (then id). Filters are optional."""
    rows, total = service.list_all_rules(
        session,
        resource_id=resource_id,
        state=current_state,
        severity=severity,
        is_enabled=is_enabled,
        limit=limit,
        offset=offset,
    )
    return AlertRuleGlobalListResponse(
        items=[
            AlertRuleListItem(
                **AlertRuleResponse.model_validate(rule).model_dump(), resource_name=name
            )
            for rule, name in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/alert-rules/{rule_id}", response_model=AlertRuleResponse, responses=_RULE_404)
def get_alert_rule(rule_id: uuid.UUID, session: DbSession) -> AlertRuleResponse:
    return AlertRuleResponse.model_validate(service.get_rule(session, rule_id))


@router.patch("/alert-rules/{rule_id}", response_model=AlertRuleResponse, responses=_RULE_404)
def update_alert_rule(
    rule_id: uuid.UUID, body: AlertRuleUpdate, session: DbSession
) -> AlertRuleResponse:
    """Update a rule. Disabling, or changing a trigger/recovery setting, closes the rule's active
    incident with an explicit reason and resets its counters; renaming does not."""
    return AlertRuleResponse.model_validate(service.update_rule(session, rule_id, body))


@router.delete(
    "/alert-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_RULE_404
)
def delete_alert_rule(rule_id: uuid.UUID, session: DbSession) -> Response:
    """Delete a rule. Its active incident (if any) is resolved as `RULE_DELETED` and kept."""
    service.delete_rule(session, rule_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Incidents ------------------------------------------------------------------------------


@router.get(
    "/incidents",
    response_model=IncidentListResponse,
    dependencies=[
        reject_unknown_query_params(
            "status", "active", "severity", "resource_id", "start", "end", "limit", "cursor"
        )
    ],
)
def list_incidents(
    session: DbSession,
    settings: AppSettings,
    status_filter: Annotated[IncidentStatus | None, Query(alias="status")] = None,
    active: bool | None = None,
    severity: AlertSeverity | None = None,
    resource_id: uuid.UUID | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> IncidentListResponse:
    """Most recently triggered first. `start`/`end` filter on `triggered_at` (`[start, end)`, UTC;
    naive values are read as UTC). `active=true` means OPEN or ACKNOWLEDGED (`false`: RESOLVED).
    Pagination is keyset-based: pass `next_cursor` as `cursor`.
    Incidents outlive their rule and resource (those ids become null; names are snapshotted)."""
    items, page_size, next_cursor = service.list_incidents(
        session,
        settings,
        status=status_filter,
        active=active,
        severity=severity,
        resource_id=resource_id,
        start=start,
        end=end,
        limit=limit,
        cursor=cursor,
    )
    return IncidentListResponse(
        items=[IncidentResponse.model_validate(i) for i in items],
        limit=page_size,
        next_cursor=next_cursor,
    )


@router.get("/incidents/{incident_id}", response_model=IncidentResponse, responses=_INCIDENT_404)
def get_incident(incident_id: uuid.UUID, session: DbSession) -> IncidentResponse:
    return IncidentResponse.model_validate(service.get_incident(session, incident_id))


@router.post(
    "/incidents/{incident_id}/acknowledge",
    response_model=IncidentResponse,
    responses={
        **_INCIDENT_404,
        status.HTTP_409_CONFLICT: {"description": "Incident is already resolved"},
    },
)
def acknowledge_incident(incident_id: uuid.UUID, session: DbSession) -> IncidentResponse:
    """OPEN becomes ACKNOWLEDGED. Repeating the call is a harmless no-op (still 200, no second
    event); a resolved incident returns 409. Acknowledging does not resolve the incident or change
    alert state: automatic recovery still resolves an acknowledged incident."""
    return IncidentResponse.model_validate(service.acknowledge(session, incident_id))


@router.get(
    "/incidents/{incident_id}/events",
    response_model=IncidentTimelineResponse,
    responses=_INCIDENT_404,
)
def get_incident_events(incident_id: uuid.UUID, session: DbSession) -> IncidentTimelineResponse:
    """The incident's append-only timeline, oldest first."""
    events = service.get_timeline(session, incident_id)
    return IncidentTimelineResponse(
        incident_id=incident_id,
        items=[IncidentEventResponse.model_validate(e) for e in events],
    )
