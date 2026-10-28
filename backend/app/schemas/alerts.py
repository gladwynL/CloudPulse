"""Request/response schemas for alert rules and incidents.

Field-level types and ranges are validated here; cross-field rules (which fields each rule type
requires or forbids) are validated in the service so errors point at the exact field.
"""

import uuid
from decimal import Decimal
from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import (
    Actor,
    AlertOperator,
    AlertRuleType,
    AlertSeverity,
    AlertState,
    IncidentEventType,
    IncidentStatus,
    ResolutionReason,
    StatusCondition,
)
from app.schemas.metrics import UtcDatetime
from app.schemas.resource import Description, Name

# Response-time threshold in milliseconds: two decimals, up to ten minutes.
Threshold = Annotated[Decimal, Field(gt=0, le=600_000, max_digits=8, decimal_places=2)]
Count = Annotated[int, Field(ge=1, le=20)]


class AlertRuleCreate(BaseModel):
    """Create a rule on one resource.

    * ``CONSECUTIVE_FAILURES``: needs ``consecutive_failures``; DEGRADED or DOWN checks count.
    * ``LATENCY_THRESHOLD``: needs ``operator``, ``threshold`` (ms) and ``consecutive_failures``.
    * ``STATUS``: needs ``status_condition``; fires on a single matching result.

    Fields that do not belong to the chosen type are rejected, not ignored.
    """

    model_config = ConfigDict(extra="forbid")

    name: Name
    description: Description | None = None
    rule_type: AlertRuleType
    is_enabled: bool = True
    severity: AlertSeverity = AlertSeverity.WARNING
    consecutive_failures: Count | None = None
    operator: AlertOperator | None = None
    threshold: Threshold | None = None
    status_condition: StatusCondition | None = None
    # Consecutive recovering observations needed to resolve (default 1).
    recovery_successes: Count = 1


class AlertRuleUpdate(BaseModel):
    """Partial update. ``rule_type`` cannot be changed (delete and recreate instead)."""

    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    description: Description | None = None
    is_enabled: bool | None = None
    severity: AlertSeverity | None = None
    consecutive_failures: Count | None = None
    operator: AlertOperator | None = None
    threshold: Threshold | None = None
    status_condition: StatusCondition | None = None
    recovery_successes: Count | None = None

    @model_validator(mode="after")
    def _reject_explicit_nulls(self) -> Self:
        # Only description (and the type-specific fields, validated against the rule type in the
        # service) may be set to null.
        for field in {
            "name",
            "is_enabled",
            "severity",
            "recovery_successes",
        } & self.model_fields_set:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class AlertRuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    resource_id: uuid.UUID
    name: str
    description: str | None
    is_enabled: bool
    rule_type: AlertRuleType
    severity: AlertSeverity
    consecutive_failures: int | None
    operator: AlertOperator | None
    threshold: float | None
    status_condition: StatusCondition | None
    recovery_successes: int
    # Evaluation progress (persisted; advanced only by accepted check results).
    current_state: AlertState
    failure_streak: int
    recovery_streak: int
    last_evaluated_at: UtcDatetime | None
    last_triggered_at: UtcDatetime | None
    created_at: UtcDatetime
    updated_at: UtcDatetime


class AlertRuleListItem(AlertRuleResponse):
    """A rule in a cross-resource listing, with the name of the resource it belongs to."""

    resource_name: str


class AlertRuleListResponse(BaseModel):
    items: list[AlertRuleResponse]
    total: int
    limit: int
    offset: int


class AlertRuleGlobalListResponse(BaseModel):
    items: list[AlertRuleListItem]
    total: int
    limit: int
    offset: int


class IncidentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    # Null once the resource or rule has been deleted; the snapshots below remain.
    resource_id: uuid.UUID | None
    alert_rule_id: uuid.UUID | None
    resource_name: str
    rule_name: str
    rule_type: AlertRuleType
    title: str
    severity: AlertSeverity
    status: IncidentStatus
    triggered_at: UtcDatetime
    acknowledged_at: UtcDatetime | None
    resolved_at: UtcDatetime | None
    resolution_reason: ResolutionReason | None
    last_observed_at: UtcDatetime
    opened_by_check_id: uuid.UUID | None
    created_at: UtcDatetime
    updated_at: UtcDatetime


class IncidentListResponse(BaseModel):
    items: list[IncidentResponse]
    limit: int
    # Opaque cursor for the next (older) page; null on the last page.
    next_cursor: str | None


class IncidentEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    incident_id: uuid.UUID
    event_type: IncidentEventType
    occurred_at: UtcDatetime
    # SYSTEM for automatic transitions; MANUAL means "an API caller" (no authentication yet).
    actor: Actor
    details: dict[str, Any]


class IncidentTimelineResponse(BaseModel):
    incident_id: uuid.UUID
    # Chronological (oldest first).
    items: list[IncidentEventResponse]


__all__ = [
    "AlertRuleCreate",
    "AlertRuleGlobalListResponse",
    "AlertRuleListItem",
    "AlertRuleListResponse",
    "AlertRuleResponse",
    "AlertRuleUpdate",
    "IncidentEventResponse",
    "IncidentListResponse",
    "IncidentResponse",
    "IncidentTimelineResponse",
]
