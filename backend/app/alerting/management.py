"""Explicit terminal dispositions for incidents when configuration changes under them.

Used by the rule and resource services. The caller must hold the resource row lock (rule/resource
management takes it first), which keeps the lock order resource -> rules -> incidents.
"""

import logging
import uuid

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.alerting import lifecycle
from app.models.alert import AlertRule, Incident
from app.models.enums import AlertState, ResolutionReason
from app.repositories import alerts as repo

logger = logging.getLogger("cloudpulse.alerting")


def close_rule_incident(
    session: Session, rule: AlertRule, reason: ResolutionReason
) -> Incident | None:
    """Resolve the rule's active incident (if any) with ``reason``. Does not change the rule."""
    incident = repo.lock_active_incidents_for_rules(session, [rule.id]).get(rule.id)
    if incident is not None:
        lifecycle.resolve_incident(session, incident, reason, at=func.clock_timestamp())
    return incident


def close_resource_incidents(
    session: Session,
    resource_id: uuid.UUID,
    reason: ResolutionReason,
    *,
    reset_rules: bool,
) -> int:
    """Resolve every active incident of a resource; optionally reset its rules to a fresh state
    (resource disabled: monitoring stops, so evaluation progress no longer means anything)."""
    rules = repo.lock_all_rules(session, resource_id)
    incidents = repo.lock_active_incidents_for_resource(session, resource_id)
    for incident in incidents:
        lifecycle.resolve_incident(session, incident, reason, at=func.clock_timestamp())
    if reset_rules:
        for rule in rules:
            if rule.current_state is not AlertState.INSUFFICIENT_DATA or (
                rule.failure_streak or rule.recovery_streak
            ):
                lifecycle.reset_rule(rule)
    session.flush()
    return len(incidents)
