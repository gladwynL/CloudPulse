"""Queries for alert rules, incidents, and incident events.

Lock order (to rule out deadlocks): **resource row -> alert rule rows (by id) -> incident rows
(by id)**. The resource row lock is the serialization point for everything about a resource's alert
state: check completion takes it through its lease-gated UPDATE, and rule/resource management takes
it explicitly. Acknowledgement locks only the incident row and nothing after it.
"""

import uuid
from datetime import datetime

from sqlalchemy import Select, func, select, tuple_
from sqlalchemy.orm import Session

from app.models.alert import AlertRule, Incident, IncidentEvent
from app.models.enums import AlertSeverity, IncidentStatus

ACTIVE = (IncidentStatus.OPEN, IncidentStatus.ACKNOWLEDGED)


# --- Rules --------------------------------------------------------------------------------


def lock_enabled_rules(session: Session, resource_id: uuid.UUID) -> list[AlertRule]:
    """The enabled rules of a resource, locked in id order. Called on every accepted check;
    served by ``ix_alert_rules_resource_enabled`` and bounded by the per-resource rule cap."""
    stmt = (
        select(AlertRule)
        .where(AlertRule.resource_id == resource_id, AlertRule.is_enabled.is_(True))
        .order_by(AlertRule.id)
        .with_for_update()
    )
    return list(session.execute(stmt).scalars())


def lock_all_rules(session: Session, resource_id: uuid.UUID) -> list[AlertRule]:
    stmt = (
        select(AlertRule)
        .where(AlertRule.resource_id == resource_id)
        .order_by(AlertRule.id)
        .with_for_update()
    )
    return list(session.execute(stmt).scalars())


def get_rule(session: Session, rule_id: uuid.UUID, *, for_update: bool = False) -> AlertRule | None:
    stmt = select(AlertRule).where(AlertRule.id == rule_id)
    if for_update:
        stmt = stmt.with_for_update()
    return session.execute(stmt).scalar_one_or_none()


def count_rules(session: Session, resource_id: uuid.UUID) -> int:
    return session.execute(
        select(func.count()).select_from(AlertRule).where(AlertRule.resource_id == resource_id)
    ).scalar_one()


def list_rules(
    session: Session, resource_id: uuid.UUID, *, limit: int, offset: int
) -> tuple[list[AlertRule], int]:
    total = count_rules(session, resource_id)
    items = list(
        session.execute(
            select(AlertRule)
            .where(AlertRule.resource_id == resource_id)
            .order_by(AlertRule.created_at, AlertRule.id)
            .limit(limit)
            .offset(offset)
        ).scalars()
    )
    return items, total


# --- Incidents ----------------------------------------------------------------------------


def lock_active_incidents_for_rules(
    session: Session, rule_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Incident]:
    """Active incident per rule (at most one each: guaranteed by a partial unique index)."""
    if not rule_ids:
        return {}
    stmt = (
        select(Incident)
        .where(Incident.alert_rule_id.in_(rule_ids), Incident.status.in_(ACTIVE))
        .order_by(Incident.id)
        .with_for_update()
    )
    return {i.alert_rule_id: i for i in session.execute(stmt).scalars()}  # type: ignore[misc]


def lock_active_incidents_for_resource(session: Session, resource_id: uuid.UUID) -> list[Incident]:
    stmt = (
        select(Incident)
        .where(Incident.resource_id == resource_id, Incident.status.in_(ACTIVE))
        .order_by(Incident.id)
        .with_for_update()
    )
    return list(session.execute(stmt).scalars())


def get_incident(
    session: Session, incident_id: uuid.UUID, *, for_update: bool = False
) -> Incident | None:
    stmt = select(Incident).where(Incident.id == incident_id)
    if for_update:
        stmt = stmt.with_for_update()
    return session.execute(stmt).scalar_one_or_none()


def list_incidents_stmt(
    *,
    status: IncidentStatus | None,
    severity: AlertSeverity | None,
    resource_id: uuid.UUID | None,
    start: datetime | None,
    end: datetime | None,
    limit: int,
    after: tuple[datetime, uuid.UUID] | None,
) -> Select:
    """Newest triggered first, ``(triggered_at DESC, id DESC)``, keyset-paginated. The window
    ``[start, end)`` applies to ``triggered_at``."""
    stmt = select(Incident)
    if status is not None:
        stmt = stmt.where(Incident.status == status)
    if severity is not None:
        stmt = stmt.where(Incident.severity == severity)
    if resource_id is not None:
        stmt = stmt.where(Incident.resource_id == resource_id)
    if start is not None:
        stmt = stmt.where(Incident.triggered_at >= start)
    if end is not None:
        stmt = stmt.where(Incident.triggered_at < end)
    if after is not None:
        stmt = stmt.where(tuple_(Incident.triggered_at, Incident.id) < tuple_(after[0], after[1]))
    return stmt.order_by(Incident.triggered_at.desc(), Incident.id.desc()).limit(limit)


def list_incidents(session: Session, **kwargs) -> list[Incident]:
    return list(session.execute(list_incidents_stmt(**kwargs)).scalars())


def list_events(session: Session, incident_id: uuid.UUID) -> list[IncidentEvent]:
    return list(
        session.execute(
            select(IncidentEvent)
            .where(IncidentEvent.incident_id == incident_id)
            .order_by(IncidentEvent.occurred_at, IncidentEvent.id)
        ).scalars()
    )
