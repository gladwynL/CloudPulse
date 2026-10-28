"""Alert evaluation for one accepted check result, inside the completion transaction.

Called by ``repo.complete()`` only after the lease-gated state update matched and the history row
was inserted, i.e. only for **accepted** results. It never commits: if anything here fails, the
caller's rollback undoes the measurement, the latest-state update, and every alert change together,
and the unreleased lease makes the existing retry path pick the check up again.

The caller already holds the resource row lock (its UPDATE), which serializes this with rule edits,
resource disable/delete, and any other completion for the same resource. Locks are then taken in a
fixed order: rules by id, then incidents by id. Work is bounded: one indexed rule query, at most
one incident query (only if some rule is firing), a name lookup only when an incident opens, and
writes only for rules that were actually evaluated (capped per resource).
"""

import logging
import uuid
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.alerting import lifecycle
from app.alerting.evaluator import (
    Classification,
    Observation,
    Progress,
    RuleSpec,
    Transition,
    evaluate,
)
from app.models.alert import AlertRule, Incident
from app.models.enums import AlertState, ResolutionReason
from app.models.resource import MonitoredResource, ResourceStatus
from app.repositories import alerts as repo

logger = logging.getLogger("cloudpulse.alerting")


def rule_spec(rule: AlertRule) -> RuleSpec:
    return RuleSpec(
        rule_type=rule.rule_type,
        recovery_successes=rule.recovery_successes,
        consecutive_failures=rule.consecutive_failures,
        operator=rule.operator,
        threshold=rule.threshold,
        status_condition=rule.status_condition,
    )


def evaluate_accepted_result(
    session: Session,
    *,
    resource_id: uuid.UUID,
    check_id: uuid.UUID,
    status: ResourceStatus,
    response_time_ms: float | None,
) -> int:
    """Evaluate every enabled rule of the resource against one accepted measurement.

    Returns the number of state transitions (rules entering FIRING or recovering)."""
    try:
        return _evaluate(session, resource_id, check_id, status, response_time_ms)
    except Exception as exc:
        logger.error(
            "alert evaluation failed",
            extra={
                "resource_id": str(resource_id),
                "check_id": str(check_id),
                "error": type(exc).__name__,
            },
        )
        raise


def _evaluate(
    session: Session,
    resource_id: uuid.UUID,
    check_id: uuid.UUID,
    status: ResourceStatus,
    response_time_ms: float | None,
) -> int:
    rules = repo.lock_enabled_rules(session, resource_id)
    if not rules:
        return 0

    observation = Observation(
        status=status,
        response_time_ms=None if response_time_ms is None else Decimal(str(response_time_ms)),
    )
    firing = [r.id for r in rules if r.current_state is AlertState.FIRING]
    active = repo.lock_active_incidents_for_rules(session, firing)
    # The transaction timestamp: identical to the measurement's checked_at. Fetched once so that
    # the per-rule writes below can be sent as one batched UPDATE (a SQL expression per row would
    # force a statement per rule while the resource lock is held).
    now = session.execute(select(func.now())).scalar_one()
    resource_name: str | None = None
    transitions = 0
    rule_updates: list[dict] = []
    observed_incident_ids: list[uuid.UUID] = []

    for rule in rules:
        if rule.last_evaluated_check_id == check_id:
            continue  # this measurement already advanced this rule; never count it twice

        result = evaluate(
            rule_spec(rule),
            Progress(rule.current_state, rule.failure_streak, rule.recovery_streak),
            observation,
        )
        triggered = result.transition is Transition.TRIGGERED
        rule_updates.append(
            {
                "id": rule.id,
                "current_state": result.progress.state,
                "failure_streak": result.progress.failure_streak,
                "recovery_streak": result.progress.recovery_streak,
                "last_evaluated_check_id": check_id,
                "last_evaluated_at": now,
                "last_triggered_at": now if triggered else rule.last_triggered_at,
            }
        )

        if triggered:
            transitions += 1
            if resource_name is None:
                resource_name = session.execute(
                    select(MonitoredResource.name).where(MonitoredResource.id == resource_id)
                ).scalar_one()
            lifecycle.open_incident(
                session,
                rule,
                resource_name=resource_name,
                check_id=check_id,
                at=func.now(),
                details={
                    "rule_type": rule.rule_type.value,
                    "failure_streak": result.progress.failure_streak,
                    "observed_status": status.value,
                    "observed_response_time_ms": (
                        None if response_time_ms is None else float(response_time_ms)
                    ),
                },
            )
            _log_state(rule, "FIRING")
        elif result.transition is Transition.RECOVERED:
            transitions += 1
            incident = active.get(rule.id)
            if incident is None:
                logger.warning(
                    "rule recovered but had no active incident",
                    extra={"rule_id": str(rule.id), "resource_id": str(resource_id)},
                )
            else:
                # clock_timestamp(), not now(): this transaction may have waited on the incident
                # row lock (e.g. behind an acknowledgement), and now() is the transaction *start*,
                # which would order RECOVERED before an event it actually followed.
                lifecycle.resolve_incident(
                    session,
                    incident,
                    ResolutionReason.RECOVERED,
                    at=func.clock_timestamp(),
                    details={
                        "check_id": str(check_id),
                        "recovery_successes": rule.recovery_successes,
                    },
                )
            _log_state(rule, "OK")
        elif (
            result.classification is Classification.MATCH
            and result.progress.state is AlertState.FIRING
        ):
            incident = active.get(rule.id)
            if incident is not None:
                observed_incident_ids.append(incident.id)

    # Two batched statements (executemany by primary key) regardless of how many rules matched.
    if rule_updates:
        session.execute(update(AlertRule), rule_updates)
    if observed_incident_ids:
        session.execute(
            update(Incident),
            [{"id": i, "last_observed_at": now} for i in observed_incident_ids],
        )
    session.flush()
    return transitions


def _log_state(rule: AlertRule, new_state: str) -> None:
    # Logged on transitions only, never for unchanged evaluations.
    logger.info(
        "alert state changed",
        extra={
            "rule_id": str(rule.id),
            "resource_id": str(rule.resource_id),
            "severity": rule.severity.value,
            "transition": new_state,
        },
    )
