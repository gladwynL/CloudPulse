"""Alert rule management and incident queries.

Rule semantics (also in the README):

* A new rule starts in ``INSUFFICIENT_DATA`` with zero counters and evaluates only *future*
  accepted observations; history is never replayed.
* Disabling a rule closes its active incident (``RULE_DISABLED``) and resets it; a disabled rule is
  never evaluated. Enabling it starts fresh.
* Changing anything that affects evaluation (``consecutive_failures``, ``operator``, ``threshold``,
  ``status_condition``, ``recovery_successes``) resets the counters, and closes an active incident
  (``RULE_RECONFIGURED``): the old incident described the old condition. Changing only name,
  description or severity is not a reconfiguration; an active incident keeps the severity it was
  opened with. ``rule_type`` cannot be changed.
* Deleting a rule closes its active incident (``RULE_DELETED``); the incident is kept.

Every management operation first takes the resource row lock, then the rule lock, then incident
locks: the same order as check completion, so they serialize with it and cannot deadlock.
"""

import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.alerting import management
from app.alerting.lifecycle import acknowledge_incident, reset_rule
from app.core.config import Settings
from app.models.alert import AlertRule, Incident, IncidentEvent
from app.models.enums import (
    AlertRuleType,
    AlertSeverity,
    IncidentStatus,
    ResolutionReason,
)
from app.repositories import alerts as repo
from app.repositories import check_results as check_repo
from app.repositories import resources as resource_repo
from app.schemas.alerts import AlertRuleCreate, AlertRuleUpdate
from app.services.metrics import decode_cursor, encode_cursor
from app.services.resources import ResourceNotFoundError, ResourceValidationError

logger = logging.getLogger("cloudpulse.alerting")

# Configuration that changes what "firing" or "recovered" means.
_MATERIAL_FIELDS = (
    "consecutive_failures",
    "operator",
    "threshold",
    "status_condition",
    "recovery_successes",
)
_TYPE_FIELDS = ("consecutive_failures", "operator", "threshold", "status_condition")
_REQUIRED: dict[AlertRuleType, set[str]] = {
    AlertRuleType.CONSECUTIVE_FAILURES: {"consecutive_failures"},
    AlertRuleType.LATENCY_THRESHOLD: {"consecutive_failures", "operator", "threshold"},
    AlertRuleType.STATUS: {"status_condition"},
}


class AlertRuleNotFoundError(Exception):
    pass


class IncidentNotFoundError(Exception):
    pass


class StateConflictError(Exception):
    """The request is valid but conflicts with current state (maps to HTTP 409)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def validate_rule_config(rule_type: AlertRuleType, config: dict) -> None:
    """Each rule type needs exactly its own fields: missing ones and irrelevant ones are errors."""
    required = _REQUIRED[rule_type]
    for field in _TYPE_FIELDS:
        value = config.get(field)
        if field in required and value is None:
            raise ResourceValidationError(field, f"required for rule_type {rule_type.value}")
        if field not in required and value is not None:
            raise ResourceValidationError(field, f"not allowed for rule_type {rule_type.value}")


def _log_rule(event: str, rule: AlertRule) -> None:
    logger.info(
        event,
        extra={
            "rule_id": str(rule.id),
            "resource_id": str(rule.resource_id),
            "rule_type": rule.rule_type.value,
            "severity": rule.severity.value,
        },
    )


# --- Rules ----------------------------------------------------------------------------------


def create_rule(
    session: Session, resource_id: uuid.UUID, data: AlertRuleCreate, settings: Settings
) -> AlertRule:
    config = {f: getattr(data, f) for f in _TYPE_FIELDS}
    validate_rule_config(data.rule_type, config)
    # Resource row first (lock order), which also serializes rule creation against checks.
    if resource_repo.get(session, resource_id, for_update=True) is None:
        raise ResourceNotFoundError
    if repo.count_rules(session, resource_id) >= settings.alerting_max_rules_per_resource:
        raise StateConflictError(
            f"A resource may have at most {settings.alerting_max_rules_per_resource} alert rules"
        )
    rule = AlertRule(
        resource_id=resource_id,
        name=data.name,
        description=data.description,
        is_enabled=data.is_enabled,
        rule_type=data.rule_type,
        severity=data.severity,
        recovery_successes=data.recovery_successes,
        **config,
    )
    session.add(rule)
    session.flush()
    session.commit()
    _log_rule("alert rule created", rule)
    return rule


def get_rule(session: Session, rule_id: uuid.UUID) -> AlertRule:
    rule = repo.get_rule(session, rule_id)
    if rule is None:
        raise AlertRuleNotFoundError
    return rule


def list_rules(
    session: Session, resource_id: uuid.UUID, *, limit: int, offset: int
) -> tuple[list[AlertRule], int]:
    if not check_repo.resource_exists(session, resource_id):
        raise ResourceNotFoundError
    return repo.list_rules(session, resource_id, limit=limit, offset=offset)


def _lock_rule(session: Session, rule_id: uuid.UUID) -> AlertRule:
    """Resource row, then rule row. A rule that vanishes meanwhile is simply not found."""
    unlocked = repo.get_rule(session, rule_id)
    if unlocked is None:
        raise AlertRuleNotFoundError
    if resource_repo.get(session, unlocked.resource_id, for_update=True) is None:
        raise AlertRuleNotFoundError
    rule = repo.get_rule(session, rule_id, for_update=True)
    if rule is None:
        raise AlertRuleNotFoundError
    session.refresh(rule)  # re-read under the lock
    return rule


def update_rule(session: Session, rule_id: uuid.UUID, data: AlertRuleUpdate) -> AlertRule:
    rule = _lock_rule(session, rule_id)
    changes = data.model_dump(exclude_unset=True)

    merged = {f: changes.get(f, getattr(rule, f)) for f in _TYPE_FIELDS}
    validate_rule_config(rule.rule_type, merged)

    material = any(
        f in changes and _normalized(changes[f]) != _normalized(getattr(rule, f))
        for f in _MATERIAL_FIELDS
    )
    was_enabled = rule.is_enabled
    disabling = was_enabled and changes.get("is_enabled") is False

    for field, value in changes.items():
        setattr(rule, field, value)

    if disabling:
        management.close_rule_incident(session, rule, ResolutionReason.RULE_DISABLED)
        reset_rule(rule)
    elif material:
        # A disabled rule has no incident and is already reset; these are no-ops for it.
        management.close_rule_incident(session, rule, ResolutionReason.RULE_RECONFIGURED)
        reset_rule(rule)

    rule.updated_at = func.now()
    session.flush()
    session.commit()
    session.refresh(rule)
    _log_rule("alert rule updated", rule)
    return rule


def delete_rule(session: Session, rule_id: uuid.UUID) -> None:
    rule = _lock_rule(session, rule_id)
    management.close_rule_incident(session, rule, ResolutionReason.RULE_DELETED)
    session.flush()  # the resolution must be written before the FK is nulled by the delete
    session.delete(rule)
    session.flush()
    session.commit()
    _log_rule("alert rule deleted", rule)


def _normalized(value: object) -> object:
    return Decimal(str(value)) if isinstance(value, (int, float, Decimal)) else value


# --- Incidents ------------------------------------------------------------------------------


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def list_incidents(
    session: Session,
    settings: Settings,
    *,
    status: IncidentStatus | None,
    severity: AlertSeverity | None,
    resource_id: uuid.UUID | None,
    start: datetime | None,
    end: datetime | None,
    limit: int | None,
    cursor: str | None,
) -> tuple[list[Incident], int, str | None]:
    start_utc = _as_utc(start) if start else None
    end_utc = _as_utc(end) if end else None
    if start_utc and end_utc and start_utc >= end_utc:
        raise ResourceValidationError("start", "start must be earlier than end", location="query")
    page_size = limit if limit is not None else settings.metrics_default_page_size
    if not 1 <= page_size <= settings.metrics_max_page_size:
        raise ResourceValidationError(
            "limit", f"must be between 1 and {settings.metrics_max_page_size}", location="query"
        )
    after = decode_cursor(cursor) if cursor else None

    rows = repo.list_incidents(
        session,
        status=status,
        severity=severity,
        resource_id=resource_id,
        start=start_utc,
        end=end_utc,
        limit=page_size + 1,
        after=after,
    )
    items = rows[:page_size]
    next_cursor = (
        encode_cursor(items[-1].triggered_at, items[-1].id) if len(rows) > page_size else None
    )
    return items, page_size, next_cursor


def get_incident(session: Session, incident_id: uuid.UUID) -> Incident:
    incident = repo.get_incident(session, incident_id)
    if incident is None:
        raise IncidentNotFoundError
    return incident


def acknowledge(session: Session, incident_id: uuid.UUID) -> Incident:
    """OPEN -> ACKNOWLEDGED. Repeating it on an acknowledged incident is a no-op (200, no second
    event). A RESOLVED incident cannot be acknowledged (409). Alert state is not touched.

    Only the incident row is locked, so this serializes with automatic recovery on that row: if
    recovery commits first this sees RESOLVED (409); if this commits first, recovery then resolves
    the acknowledged incident."""
    incident = repo.get_incident(session, incident_id, for_update=True)
    if incident is None:
        raise IncidentNotFoundError
    if incident.status is IncidentStatus.RESOLVED:
        raise StateConflictError("Incident is already resolved and cannot be acknowledged")
    acknowledge_incident(session, incident)
    session.flush()
    session.commit()
    session.refresh(incident)
    return incident


def get_timeline(session: Session, incident_id: uuid.UUID) -> list[IncidentEvent]:
    if repo.get_incident(session, incident_id) is None:
        raise IncidentNotFoundError
    return repo.list_events(session, incident_id)
