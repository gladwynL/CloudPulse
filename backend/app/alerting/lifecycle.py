"""Incident lifecycle writes. Each transition and its timeline event go in the caller's single
transaction: nothing here commits, so a failure anywhere rolls the whole transition back.

``at`` is a SQL timestamp expression. The completion transaction passes ``func.now()`` so an
incident carries exactly the instant of the measurement behind it; API-driven transitions pass
``func.clock_timestamp()`` (see ``app.models.alert`` for why).
"""

import logging
import uuid
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.models.alert import AlertRule, Incident, IncidentEvent
from app.models.enums import (
    Actor,
    AlertState,
    IncidentEventType,
    IncidentStatus,
    ResolutionReason,
)

logger = logging.getLogger("cloudpulse.alerting")

TITLE_MAX = 255


def _event(
    session: Session,
    incident_id: uuid.UUID,
    event_type: IncidentEventType,
    *,
    at: ColumnElement,
    actor: Actor,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        IncidentEvent(
            incident_id=incident_id,
            event_type=event_type,
            occurred_at=at,
            actor=actor,
            details=details or {},
        )
    )


def open_incident(
    session: Session,
    rule: AlertRule,
    *,
    resource_name: str,
    check_id: uuid.UUID,
    at: ColumnElement,
    details: dict[str, Any],
) -> Incident:
    """Open the rule's single active incident. The partial unique index is the backstop: a second
    active incident for the same rule raises IntegrityError, rolling the transaction back."""
    title = f"{rule.severity.value}: {rule.name} on {resource_name}"[:TITLE_MAX]
    incident = Incident(
        resource_id=rule.resource_id,
        alert_rule_id=rule.id,
        resource_name=resource_name,
        rule_name=rule.name,
        rule_type=rule.rule_type,
        title=title,
        severity=rule.severity,
        status=IncidentStatus.OPEN,
        triggered_at=at,
        last_observed_at=at,
        opened_by_check_id=check_id,
    )
    session.add(incident)
    session.flush()  # assigns the id and surfaces constraint violations now
    _event(
        session,
        incident.id,
        IncidentEventType.OPENED,
        at=at,
        actor=Actor.SYSTEM,
        details={"rule_id": str(rule.id), "check_id": str(check_id), **details},
    )
    logger.info(
        "incident opened",
        extra={
            "incident_id": str(incident.id),
            "rule_id": str(rule.id),
            "resource_id": str(rule.resource_id),
            "severity": rule.severity.value,
            "transition": "OPENED",
        },
    )
    return incident


def resolve_incident(
    session: Session,
    incident: Incident,
    reason: ResolutionReason,
    *,
    at: ColumnElement,
    actor: Actor = Actor.SYSTEM,
    details: dict[str, Any] | None = None,
) -> None:
    """Move an active incident to RESOLVED with an explicit reason (acknowledged ones included)."""
    incident.status = IncidentStatus.RESOLVED
    incident.resolved_at = at
    incident.resolution_reason = reason
    incident.updated_at = at
    _event(
        session,
        incident.id,
        IncidentEventType[reason.name],
        at=at,
        actor=actor,
        details=details,
    )
    logger.info(
        "incident resolved",
        extra={
            "incident_id": str(incident.id),
            "rule_id": str(incident.alert_rule_id) if incident.alert_rule_id else None,
            "resource_id": str(incident.resource_id) if incident.resource_id else None,
            "severity": incident.severity.value,
            "transition": reason.value,
        },
    )


def acknowledge_incident(session: Session, incident: Incident) -> bool:
    """OPEN -> ACKNOWLEDGED. Returns False (and writes nothing) if it is already acknowledged.
    The caller must have rejected RESOLVED incidents. Does not touch the rule's alert state."""
    if incident.status is IncidentStatus.ACKNOWLEDGED:
        return False
    at = func.clock_timestamp()
    incident.status = IncidentStatus.ACKNOWLEDGED
    incident.acknowledged_at = at
    incident.updated_at = at
    _event(session, incident.id, IncidentEventType.ACKNOWLEDGED, at=at, actor=Actor.MANUAL)
    logger.info(
        "incident acknowledged",
        extra={
            "incident_id": str(incident.id),
            "severity": incident.severity.value,
            "transition": "ACKNOWLEDGED",
        },
    )
    return True


def reset_rule(rule: AlertRule) -> None:
    """Back to a fresh evaluation state (used when a rule is disabled or materially changed)."""
    rule.current_state = AlertState.INSUFFICIENT_DATA
    rule.failure_streak = 0
    rule.recovery_streak = 0
