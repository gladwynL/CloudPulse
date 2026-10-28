"""Small builders shared by the PostgreSQL integration tests."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.models.alert import AlertRule, Incident, IncidentEvent
from app.models.check_result import ResourceCheckResult
from app.models.enums import (
    AlertOperator,
    AlertRuleType,
    AlertSeverity,
    CheckSource,
    ErrorCategory,
    StatusCondition,
)
from app.models.resource import MonitoredResource, ResourceStatus
from app.monitoring.types import CheckResult, ClaimedCheck
from app.repositories import resources as _resource_repo


def insert_resource(
    factory: sessionmaker[Session],
    *,
    name: str = "svc",
    url: str = "https://example.com/health",
    enabled: bool = True,
    interval: int = 60,
    timeout: int = 10,
    expected: int = 200,
    due_in_seconds: float | None = -1,
    status: ResourceStatus = ResourceStatus.UNKNOWN,
) -> uuid.UUID:
    """Insert a row directly. ``due_in_seconds`` is relative to the database clock;
    negative means already due, None means never scheduled."""
    resource = MonitoredResource(
        name=name,
        url=url,
        is_enabled=enabled,
        check_interval_seconds=interval,
        timeout_seconds=timeout,
        expected_status_code=expected,
        current_status=status,
        next_check_at=(
            None if due_in_seconds is None else func.now() + timedelta(seconds=due_in_seconds)
        ),
    )
    with factory() as session:
        session.add(resource)
        session.commit()
        return resource.id


def fetch(factory: sessionmaker[Session], resource_id: uuid.UUID) -> MonitoredResource:
    with factory() as session:
        row = session.get(MonitoredResource, resource_id)
        assert row is not None
        session.expunge(row)
        return row


def exists(factory: sessionmaker[Session], resource_id: uuid.UUID) -> bool:
    with factory() as session:
        return session.get(MonitoredResource, resource_id) is not None


def expire_lease(factory: sessionmaker[Session], resource_id: uuid.UUID) -> None:
    """Simulate a crashed executor: its lease runs out without a result being written."""
    with factory() as session:
        session.execute(
            text(
                "UPDATE monitored_resources SET lease_expires_at = now() - interval '1 second' "
                "WHERE id = :id"
            ),
            {"id": resource_id},
        )
        session.commit()


def db_now(factory: sessionmaker[Session]) -> datetime:
    with factory() as session:
        return session.execute(text("SELECT now()")).scalar_one()


def seconds_from(now: datetime, moment: datetime | None) -> float:
    assert moment is not None
    return (moment.astimezone(UTC) - now.astimezone(UTC)).total_seconds()


# --- history helpers ------------------------------------------------------------------------


def insert_check(
    factory: sessionmaker[Session],
    resource_id: uuid.UUID,
    checked_at: datetime,
    *,
    status: ResourceStatus = ResourceStatus.HEALTHY,
    response_time_ms: float | None = None,
    http_status_code: int | None = None,
    duration_ms: float = 10.0,
    source: CheckSource = CheckSource.SCHEDULED,
    error_category: ErrorCategory | None = None,
    error_summary: str | None = None,
) -> uuid.UUID:
    """Insert a history row with an explicit ``checked_at`` (deterministic fixtures)."""
    row = ResourceCheckResult(
        resource_id=resource_id,
        checked_at=checked_at,
        status=status,
        http_status_code=http_status_code,
        response_time_ms=None if response_time_ms is None else Decimal(str(response_time_ms)),
        duration_ms=Decimal(str(duration_ms)),
        error_category=error_category,
        error_summary=error_summary,
        source=source,
    )
    with factory() as session:
        session.add(row)
        session.commit()
        return row.id


def history(
    factory: sessionmaker[Session], resource_id: uuid.UUID | None = None
) -> list[ResourceCheckResult]:
    stmt = select(ResourceCheckResult).order_by(
        ResourceCheckResult.checked_at, ResourceCheckResult.id
    )
    if resource_id is not None:
        stmt = stmt.where(ResourceCheckResult.resource_id == resource_id)
    with factory() as session:
        rows = list(session.execute(stmt).scalars())
        for row in rows:
            session.expunge(row)
        return rows


def count_checks(factory: sessionmaker[Session], resource_id: uuid.UUID | None = None) -> int:
    return len(history(factory, resource_id))


def make_due(factory: sessionmaker[Session], resource_id: uuid.UUID) -> None:
    with factory() as session:
        session.execute(
            text("UPDATE monitored_resources SET next_check_at = now() WHERE id = :i"),
            {"i": resource_id},
        )
        session.commit()


# --- alerting helpers ---------------------------------------------------------------------


def make_rule(
    factory: sessionmaker[Session],
    resource_id: uuid.UUID,
    rule_type: AlertRuleType = AlertRuleType.CONSECUTIVE_FAILURES,
    *,
    name: str = "rule",
    consecutive_failures: int | None = None,
    operator: AlertOperator | None = None,
    threshold: float | None = None,
    status_condition: StatusCondition | None = None,
    recovery_successes: int = 1,
    severity: AlertSeverity = AlertSeverity.WARNING,
    enabled: bool = True,
) -> uuid.UUID:
    """Insert a rule directly. Sensible defaults per type: 3 failures / DOWN condition."""
    if rule_type is AlertRuleType.CONSECUTIVE_FAILURES and consecutive_failures is None:
        consecutive_failures = 3
    if rule_type is AlertRuleType.LATENCY_THRESHOLD:
        consecutive_failures = consecutive_failures or 3
        operator = operator or AlertOperator.GREATER_THAN
        threshold = 1000.0 if threshold is None else threshold
    if rule_type is AlertRuleType.STATUS and status_condition is None:
        status_condition = StatusCondition.DOWN
    rule = AlertRule(
        resource_id=resource_id,
        name=name,
        rule_type=rule_type,
        consecutive_failures=consecutive_failures,
        operator=operator,
        threshold=None if threshold is None else Decimal(str(threshold)),
        status_condition=status_condition,
        recovery_successes=recovery_successes,
        severity=severity,
        is_enabled=enabled,
    )
    with factory() as session:
        session.add(rule)
        session.commit()
        return rule.id


def fetch_rule(factory: sessionmaker[Session], rule_id: uuid.UUID) -> AlertRule:
    with factory() as session:
        row = session.get(AlertRule, rule_id)
        assert row is not None
        session.expunge(row)
        return row


def incidents(
    factory: sessionmaker[Session], *, rule_id: uuid.UUID | None = None
) -> list[Incident]:
    stmt = select(Incident).order_by(Incident.triggered_at, Incident.id)
    if rule_id is not None:
        stmt = stmt.where(Incident.alert_rule_id == rule_id)
    with factory() as session:
        rows = list(session.execute(stmt).scalars())
        for row in rows:
            session.expunge(row)
        return rows


def events(factory: sessionmaker[Session], incident_id: uuid.UUID) -> list[IncidentEvent]:
    stmt = (
        select(IncidentEvent)
        .where(IncidentEvent.incident_id == incident_id)
        .order_by(IncidentEvent.occurred_at, IncidentEvent.id)
    )
    with factory() as session:
        rows = list(session.execute(stmt).scalars())
        for row in rows:
            session.expunge(row)
        return rows


def make_result(
    status: ResourceStatus, response_time_ms: float | None = None, duration_ms: float = 20.0
) -> CheckResult:
    if status is ResourceStatus.DOWN:
        return CheckResult(
            status, None, None, "timeout: no response within 5s", ErrorCategory.TIMEOUT, duration_ms
        )
    http = 200 if status is ResourceStatus.HEALTHY else 503
    rt = 50.0 if response_time_ms is None else response_time_ms
    error = None if status is ResourceStatus.HEALTHY else f"expected status 200, got {http}"
    category = None if status is ResourceStatus.HEALTHY else ErrorCategory.UNEXPECTED_STATUS
    return CheckResult(status, http, rt, error, category, duration_ms)


def claim_resource(factory: sessionmaker[Session], resource_id: uuid.UUID) -> ClaimedCheck:
    with factory() as session:
        claim = _resource_repo.claim_by_id(session, resource_id, owner="t", grace_seconds=30)
        session.commit()
    assert isinstance(claim, ClaimedCheck), claim
    return claim


def feed(
    factory: sessionmaker[Session],
    resource_id: uuid.UUID,
    status: ResourceStatus,
    response_time_ms: float | None = None,
    source: CheckSource = CheckSource.SCHEDULED,
) -> bool:
    """Run one full accepted check (claim -> complete) and return whether it was accepted."""
    claim = claim_resource(factory, resource_id)
    with factory() as session:
        accepted = _resource_repo.complete(
            session, claim, make_result(status, response_time_ms), source
        )
        session.commit()
    return accepted


def feed_all(
    factory: sessionmaker[Session], resource_id: uuid.UUID, statuses: list[ResourceStatus]
) -> None:
    for status in statuses:
        feed(factory, resource_id, status)
