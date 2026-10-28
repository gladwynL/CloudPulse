"""System-wide counts: three single-pass aggregate queries, no row fetching."""

from sqlalchemy import func, select
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from app.models.alert import AlertRule, Incident
from app.models.enums import AlertState, IncidentStatus
from app.models.resource import MonitoredResource as M
from app.models.resource import ResourceStatus


def resource_counts(session: Session) -> Row:
    return session.execute(
        select(
            func.count().label("total"),
            func.count().filter(M.is_enabled.is_(True)).label("enabled"),
            *[
                func.count().filter(M.current_status == status).label(status.value)
                for status in ResourceStatus
            ],
        )
    ).one()


def rule_counts(session: Session) -> Row:
    return session.execute(
        select(
            func.count().label("total"),
            func.count().filter(AlertRule.is_enabled.is_(True)).label("enabled"),
            func.count().filter(AlertRule.current_state == AlertState.FIRING).label("firing"),
        )
    ).one()


def incident_counts(session: Session) -> Row:
    return session.execute(
        select(
            func.count().filter(Incident.status == IncidentStatus.OPEN).label("open"),
            func.count()
            .filter(Incident.status == IncidentStatus.ACKNOWLEDGED)
            .label("acknowledged"),
        )
    ).one()
