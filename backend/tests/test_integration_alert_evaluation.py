"""Alert evaluation through the real completion path on PostgreSQL: rules, incidents, events."""

import uuid

import pytest
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.alerting import lifecycle
from app.alerting.engine import evaluate_accepted_result
from app.core.config import Settings
from app.models.alert import Incident
from app.models.enums import (
    Actor,
    AlertOperator,
    AlertRuleType,
    AlertSeverity,
    AlertState,
    CheckSource,
    IncidentEventType,
    IncidentStatus,
    ResolutionReason,
    StatusCondition,
)
from app.models.resource import ResourceStatus as S
from app.repositories import resources as repo
from app.services import alerts as alert_service
from tests.helpers import (
    claim_resource,
    count_checks,
    events,
    expire_lease,
    feed,
    feed_all,
    fetch,
    fetch_rule,
    history,
    incidents,
    insert_check,
    insert_resource,
    make_result,
    make_rule,
)

pytestmark = pytest.mark.integration

Factory = sessionmaker[Session]
H, D, X = S.HEALTHY, S.DEGRADED, S.DOWN
CF, LT, ST = (
    AlertRuleType.CONSECUTIVE_FAILURES,
    AlertRuleType.LATENCY_THRESHOLD,
    AlertRuleType.STATUS,
)


def event_types(factory: Factory, incident_id: uuid.UUID) -> list[IncidentEventType]:
    return [e.event_type for e in events(factory, incident_id)]


# --- Consecutive failures: trigger, continue, recover --------------------------------------


def test_rule_fires_at_the_threshold_and_opens_exactly_one_incident(session_factory: Factory):
    rid = insert_resource(session_factory, name="Billing API")
    rule_id = make_rule(
        session_factory, rid, name="Down 3x", severity=AlertSeverity.CRITICAL, recovery_successes=2
    )

    feed_all(session_factory, rid, [X, X])
    assert fetch_rule(session_factory, rule_id).current_state is AlertState.OK
    assert incidents(session_factory) == []

    feed(session_factory, rid, X)

    rule = fetch_rule(session_factory, rule_id)
    assert (rule.current_state, rule.failure_streak) == (AlertState.FIRING, 3)
    assert rule.last_triggered_at is not None
    (incident,) = incidents(session_factory)
    assert incident.status is IncidentStatus.OPEN
    assert incident.alert_rule_id == rule_id and incident.resource_id == rid
    assert incident.severity is AlertSeverity.CRITICAL
    assert incident.rule_type is CF
    # Snapshots keep it readable later; the title says what and where.
    assert (incident.resource_name, incident.rule_name) == ("Billing API", "Down 3x")
    assert incident.title == "CRITICAL: Down 3x on Billing API"
    assert incident.acknowledged_at is None and incident.resolved_at is None
    # The incident is tied to the exact accepted measurement that opened it.
    newest = history(session_factory, rid)[-1]
    assert incident.opened_by_check_id == newest.id
    assert incident.triggered_at == newest.checked_at == fetch(session_factory, rid).last_checked_at
    (opened,) = events(session_factory, incident.id)
    assert opened.event_type is IncidentEventType.OPENED and opened.actor is Actor.SYSTEM
    assert opened.details["check_id"] == str(newest.id)
    assert opened.details["observed_status"] == "DOWN"
    assert "http" not in str(opened.details).lower()  # no URLs in event metadata


def test_continued_failures_do_not_open_more_incidents(session_factory: Factory):
    rid = insert_resource(session_factory)
    rule_id = make_rule(session_factory, rid, consecutive_failures=2)
    feed_all(session_factory, rid, [X, X])
    (first,) = incidents(session_factory)

    feed_all(session_factory, rid, [X, D, X, X])

    (still,) = incidents(session_factory)
    assert still.id == first.id and still.status is IncidentStatus.OPEN
    assert still.last_observed_at > first.last_observed_at  # kept current while unhealthy
    assert fetch_rule(session_factory, rule_id).failure_streak == 6
    assert event_types(session_factory, first.id) == [IncidentEventType.OPENED]


def test_isolated_and_intermittent_failures_never_open_an_incident(session_factory: Factory):
    rid = insert_resource(session_factory)
    rule_id = make_rule(session_factory, rid, consecutive_failures=3)

    feed_all(session_factory, rid, [X, X, H, X, X, H, D, H, X])

    assert incidents(session_factory) == []
    assert fetch_rule(session_factory, rule_id).current_state is AlertState.OK


def test_recovery_requires_consecutive_healthy_checks_and_resolves_the_incident(
    session_factory: Factory,
):
    rid = insert_resource(session_factory)
    rule_id = make_rule(session_factory, rid, consecutive_failures=2, recovery_successes=2)
    feed_all(session_factory, rid, [X, X, H, X, H])  # recovery attempt broken by a failure
    (incident,) = incidents(session_factory)
    assert incident.status is IncidentStatus.OPEN
    assert fetch_rule(session_factory, rule_id).current_state is AlertState.FIRING

    feed(session_factory, rid, H)  # second consecutive healthy check

    (resolved,) = incidents(session_factory)
    assert resolved.status is IncidentStatus.RESOLVED
    assert resolved.resolution_reason is ResolutionReason.RECOVERED
    assert resolved.resolved_at is not None and resolved.resolved_at >= resolved.triggered_at
    rule = fetch_rule(session_factory, rule_id)
    assert (rule.current_state, rule.failure_streak, rule.recovery_streak) == (
        AlertState.OK,
        0,
        0,
    )
    assert event_types(session_factory, incident.id) == [
        IncidentEventType.OPENED,
        IncidentEventType.RECOVERED,
    ]


def test_a_new_incident_opens_only_after_genuine_recovery(session_factory: Factory):
    rid = insert_resource(session_factory)
    make_rule(session_factory, rid, consecutive_failures=2)
    feed_all(session_factory, rid, [X, X, X])
    feed(session_factory, rid, H)  # recovers (recovery_successes=1)
    feed_all(session_factory, rid, [X, X])  # fires again

    first, second = incidents(session_factory)
    assert first.status is IncidentStatus.RESOLVED and second.status is IncidentStatus.OPEN
    assert first.id != second.id and second.triggered_at > first.triggered_at


def test_acknowledgement_does_not_stop_automatic_recovery(session_factory: Factory):
    rid = insert_resource(session_factory)
    rule_id = make_rule(session_factory, rid, consecutive_failures=1)
    feed(session_factory, rid, X)
    (incident,) = incidents(session_factory)

    with session_factory() as session:
        acked = alert_service.acknowledge(session, incident.id)
    assert acked.status is IncidentStatus.ACKNOWLEDGED
    # Acknowledging is a human action: the alert itself is still firing.
    assert fetch_rule(session_factory, rule_id).current_state is AlertState.FIRING

    feed(session_factory, rid, X)  # still unhealthy: stays acknowledged, not reopened
    (still,) = incidents(session_factory)
    assert still.status is IncidentStatus.ACKNOWLEDGED

    feed(session_factory, rid, H)

    (resolved,) = incidents(session_factory)
    assert resolved.status is IncidentStatus.RESOLVED
    assert resolved.resolution_reason is ResolutionReason.RECOVERED
    assert resolved.acknowledged_at is not None  # the acknowledgement is part of its history
    types = event_types(session_factory, incident.id)
    assert types == [
        IncidentEventType.OPENED,
        IncidentEventType.ACKNOWLEDGED,
        IncidentEventType.RECOVERED,
    ]
    assert [e.actor for e in events(session_factory, incident.id)] == [
        Actor.SYSTEM,
        Actor.MANUAL,
        Actor.SYSTEM,
    ]


# --- STATUS rules ---------------------------------------------------------------------------


def test_status_rule_fires_on_the_first_matching_result_only(session_factory: Factory):
    rid = insert_resource(session_factory)
    rule_id = make_rule(session_factory, rid, ST, status_condition=StatusCondition.DOWN)

    feed_all(session_factory, rid, [H, D])
    assert incidents(session_factory) == []  # DEGRADED does not match a DOWN rule
    assert fetch_rule(session_factory, rule_id).current_state is AlertState.OK

    feed(session_factory, rid, X)

    (incident,) = incidents(session_factory)
    assert incident.status is IncidentStatus.OPEN and incident.rule_type is ST


def test_status_rule_does_not_recover_on_a_neutral_status(session_factory: Factory):
    rid = insert_resource(session_factory)
    make_rule(session_factory, rid, ST, status_condition=StatusCondition.DOWN)
    feed_all(session_factory, rid, [X, D])
    assert incidents(session_factory)[0].status is IncidentStatus.OPEN
    feed(session_factory, rid, H)
    assert incidents(session_factory)[0].status is IncidentStatus.RESOLVED


# --- Latency rules --------------------------------------------------------------------------


def feed_latency(factory: Factory, rid: uuid.UUID, values: list[float | None]) -> None:
    for value in values:
        feed(factory, rid, X if value is None else H, response_time_ms=value)


def test_latency_rule_needs_consecutive_slow_responses(session_factory: Factory):
    rid = insert_resource(session_factory)
    make_rule(session_factory, rid, LT, threshold=1000, consecutive_failures=3)

    feed_latency(session_factory, rid, [1500, 1200])
    assert incidents(session_factory) == []
    feed_latency(session_factory, rid, [1100])

    (incident,) = incidents(session_factory)
    assert incident.rule_type is LT


def test_latency_boundary_equality_depends_on_the_operator(session_factory: Factory):
    rid = insert_resource(session_factory)
    strict = make_rule(session_factory, rid, LT, name="gt", threshold=1000, consecutive_failures=1)
    inclusive = make_rule(
        session_factory,
        rid,
        LT,
        name="gte",
        threshold=1000,
        consecutive_failures=1,
        operator=AlertOperator.GREATER_THAN_OR_EQUAL,
    )

    feed_latency(session_factory, rid, [1000.00])

    assert fetch_rule(session_factory, strict).current_state is AlertState.OK
    assert fetch_rule(session_factory, inclusive).current_state is AlertState.FIRING
    assert [i.alert_rule_id for i in incidents(session_factory)] == [inclusive]


def test_missing_latency_resets_the_latency_streak_instead_of_counting(session_factory: Factory):
    rid = insert_resource(session_factory)
    rule_id = make_rule(session_factory, rid, LT, threshold=1000, consecutive_failures=3)

    feed_latency(session_factory, rid, [2000, 2000, None, 2000])  # the timeout broke the streak

    assert incidents(session_factory) == []
    assert fetch_rule(session_factory, rule_id).failure_streak == 1


def test_a_timeout_does_not_resolve_a_latency_incident(session_factory: Factory):
    rid = insert_resource(session_factory)
    make_rule(session_factory, rid, LT, threshold=1000, consecutive_failures=1)
    feed_latency(session_factory, rid, [2000, None, None, None])
    assert incidents(session_factory)[0].status is IncidentStatus.OPEN

    feed_latency(session_factory, rid, [200])  # a valid, fast response does

    assert incidents(session_factory)[0].status is IncidentStatus.RESOLVED


# --- Multiple rules on one resource ---------------------------------------------------------


def test_every_applicable_rule_is_evaluated_consistently_for_one_measurement(
    session_factory: Factory,
):
    rid = insert_resource(session_factory)
    streak = make_rule(session_factory, rid, CF, name="3x", consecutive_failures=3)
    immediate = make_rule(session_factory, rid, ST, name="down")
    slow = make_rule(session_factory, rid, LT, name="slow", threshold=500, consecutive_failures=1)
    off = make_rule(session_factory, rid, CF, name="off", consecutive_failures=1, enabled=False)

    feed_all(session_factory, rid, [X, X, X])

    assert fetch_rule(session_factory, immediate).current_state is AlertState.FIRING
    assert fetch_rule(session_factory, streak).current_state is AlertState.FIRING
    # DOWN has no latency: neutral for the latency rule, which therefore never even fires.
    assert fetch_rule(session_factory, slow).current_state is AlertState.OK
    off_rule = fetch_rule(session_factory, off)
    assert off_rule.last_evaluated_at is None and off_rule.failure_streak == 0
    assert {i.alert_rule_id for i in incidents(session_factory)} == {streak, immediate}


def test_all_rules_see_the_same_measurement(session_factory: Factory):
    rid = insert_resource(session_factory)
    a = make_rule(session_factory, rid, name="a")
    b = make_rule(session_factory, rid, ST, name="b")

    feed(session_factory, rid, X)

    newest = history(session_factory, rid)[-1]
    assert fetch_rule(session_factory, a).last_evaluated_check_id == newest.id
    assert fetch_rule(session_factory, b).last_evaluated_check_id == newest.id


# --- What a rule is allowed to see ----------------------------------------------------------


def test_new_rules_do_not_evaluate_existing_history(session_factory: Factory):
    from datetime import UTC, datetime, timedelta

    rid = insert_resource(session_factory)
    now = datetime.now(UTC)
    for minute in range(10):  # ten old failures already in history
        insert_check(session_factory, rid, now - timedelta(minutes=minute + 1), status=X)
    rule_id = make_rule(session_factory, rid, consecutive_failures=3)

    rule = fetch_rule(session_factory, rule_id)
    assert (rule.current_state, rule.failure_streak) == (AlertState.INSUFFICIENT_DATA, 0)

    feed(session_factory, rid, X)

    rule = fetch_rule(session_factory, rule_id)
    assert (rule.current_state, rule.failure_streak) == (AlertState.OK, 1)  # not 11
    assert incidents(session_factory) == []


def test_counters_persist_across_a_restart(session_factory: Factory, pg_engine):
    rid = insert_resource(session_factory)
    rule_id = make_rule(session_factory, rid, consecutive_failures=3)
    feed_all(session_factory, rid, [X, X])

    # A "restarted worker" is a brand new session factory with no in-memory state at all.
    restarted = sessionmaker(pg_engine, autoflush=False, expire_on_commit=False)
    assert fetch_rule(restarted, rule_id).failure_streak == 2
    feed(restarted, rid, X)

    assert fetch_rule(restarted, rule_id).current_state is AlertState.FIRING
    assert len(incidents(restarted)) == 1


def test_the_same_measurement_never_advances_a_rule_twice(session_factory: Factory):
    rid = insert_resource(session_factory)
    rule_id = make_rule(session_factory, rid, consecutive_failures=5)
    check_id = uuid.uuid4()

    for _ in range(3):
        with session_factory() as session:
            evaluate_accepted_result(
                session, resource_id=rid, check_id=check_id, status=X, response_time_ms=None
            )
            session.commit()

    assert fetch_rule(session_factory, rule_id).failure_streak == 1


def test_disabled_rules_are_never_evaluated(session_factory: Factory):
    rid = insert_resource(session_factory)
    rule_id = make_rule(session_factory, rid, consecutive_failures=1, enabled=False)
    feed_all(session_factory, rid, [X, X, X])
    rule = fetch_rule(session_factory, rule_id)
    assert (rule.current_state, rule.failure_streak, rule.last_evaluated_at) == (
        AlertState.INSUFFICIENT_DATA,
        0,
        None,
    )
    assert incidents(session_factory) == []


# --- Results that must not be evaluated -----------------------------------------------------


def test_a_stale_result_is_not_evaluated(session_factory: Factory):
    rid = insert_resource(session_factory)
    rule_id = make_rule(session_factory, rid, consecutive_failures=1)
    claim = claim_resource(session_factory, rid)
    with session_factory() as session:
        repo.release(session, claim)
        session.commit()

    with session_factory() as session:
        accepted = repo.complete(session, claim, make_result(X), CheckSource.SCHEDULED)
        session.commit()

    assert accepted is False
    rule = fetch_rule(session_factory, rule_id)
    assert (rule.current_state, rule.failure_streak) == (AlertState.INSUFFICIENT_DATA, 0)
    assert incidents(session_factory) == [] and count_checks(session_factory) == 0


def test_an_expired_lease_holder_is_not_evaluated_and_the_new_holder_is(session_factory: Factory):
    rid = insert_resource(session_factory)
    rule_id = make_rule(session_factory, rid, consecutive_failures=1)
    old = claim_resource(session_factory, rid)
    expire_lease(session_factory, rid)
    new = claim_resource(session_factory, rid)

    with session_factory() as session:
        assert repo.complete(session, new, make_result(X), CheckSource.SCHEDULED) is True
        session.commit()
    with session_factory() as session:
        assert repo.complete(session, old, make_result(X), CheckSource.SCHEDULED) is False
        session.commit()

    assert fetch_rule(session_factory, rule_id).failure_streak == 1  # counted once, not twice
    assert len(incidents(session_factory)) == 1


def test_completing_a_claim_twice_does_not_duplicate_the_incident(session_factory: Factory):
    rid = insert_resource(session_factory)
    make_rule(session_factory, rid, ST)
    claim = claim_resource(session_factory, rid)
    for expected in (True, False):
        with session_factory() as session:
            assert repo.complete(session, claim, make_result(X), CheckSource.MANUAL) is expected
            session.commit()
    assert len(incidents(session_factory)) == 1 and count_checks(session_factory) == 1


# --- Transactions: nothing partial ----------------------------------------------------------


def seed_conflicting_active_incident(factory: Factory, rid: uuid.UUID, rule_id: uuid.UUID) -> None:
    """An inconsistent state (active incident while the rule says OK) makes the next trigger hit
    the one-active-incident unique index: a stand-in for any failure while opening an incident."""
    with factory() as session:
        session.add(
            Incident(
                resource_id=rid,
                alert_rule_id=rule_id,
                resource_name="r",
                rule_name="rule",
                rule_type=ST,
                title="pre-existing",
                severity=AlertSeverity.WARNING,
                status=IncidentStatus.OPEN,
                triggered_at=func.now(),
                last_observed_at=func.now(),
            )
        )
        session.commit()


def test_a_failed_incident_insert_rolls_back_the_measurement_and_state(session_factory: Factory):
    rid = insert_resource(session_factory)
    rule_id = make_rule(session_factory, rid, ST)
    seed_conflicting_active_incident(session_factory, rid, rule_id)
    claim = claim_resource(session_factory, rid)

    with session_factory() as session, pytest.raises(IntegrityError):
        repo.complete(session, claim, make_result(X), CheckSource.SCHEDULED)
        session.commit()

    assert count_checks(session_factory) == 0  # no measurement
    resource = fetch(session_factory, rid)
    assert resource.current_status is S.UNKNOWN and resource.last_checked_at is None
    assert resource.lease_token == claim.lease_token  # lease held: the check will be retried
    rule = fetch_rule(session_factory, rule_id)
    assert (rule.current_state, rule.failure_streak) == (AlertState.INSUFFICIENT_DATA, 0)
    assert [i.title for i in incidents(session_factory)] == ["pre-existing"]


def test_a_failed_event_insert_rolls_back_the_incident_and_the_measurement(
    session_factory: Factory, monkeypatch: pytest.MonkeyPatch
):
    rid = insert_resource(session_factory)
    rule_id = make_rule(session_factory, rid, ST)
    claim = claim_resource(session_factory, rid)

    def boom(*args, **kwargs):
        raise RuntimeError("event insert failed")

    monkeypatch.setattr(lifecycle, "_event", boom)

    with session_factory() as session, pytest.raises(RuntimeError):
        repo.complete(session, claim, make_result(X), CheckSource.SCHEDULED)
        session.commit()

    assert incidents(session_factory) == [] and count_checks(session_factory) == 0
    assert fetch(session_factory, rid).current_status is S.UNKNOWN
    assert fetch_rule(session_factory, rule_id).current_state is AlertState.INSUFFICIENT_DATA


def test_after_a_failed_evaluation_the_retry_succeeds(
    session_factory: Factory, monkeypatch: pytest.MonkeyPatch
):
    rid = insert_resource(session_factory)
    rule_id = make_rule(session_factory, rid, ST)
    claim = claim_resource(session_factory, rid)
    monkeypatch.setattr(lifecycle, "_event", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    with session_factory() as session, pytest.raises(OSError):
        repo.complete(session, claim, make_result(X), CheckSource.SCHEDULED)
    monkeypatch.undo()

    expire_lease(session_factory, rid)  # the existing recovery path for an unfinished check
    assert feed(session_factory, rid, X) is True

    (incident,) = incidents(session_factory)
    assert incident.status is IncidentStatus.OPEN
    assert fetch_rule(session_factory, rule_id).current_state is AlertState.FIRING
    assert count_checks(session_factory) == 1


def test_a_failed_recovery_write_leaves_the_incident_open(
    session_factory: Factory, monkeypatch: pytest.MonkeyPatch
):
    rid = insert_resource(session_factory)
    rule_id = make_rule(session_factory, rid, ST)
    feed(session_factory, rid, X)
    claim = claim_resource(session_factory, rid)
    monkeypatch.setattr(lifecycle, "_event", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))

    with session_factory() as session, pytest.raises(OSError):
        repo.complete(session, claim, make_result(H), CheckSource.SCHEDULED)

    (incident,) = incidents(session_factory)
    assert incident.status is IncidentStatus.OPEN and incident.resolved_at is None
    assert fetch_rule(session_factory, rule_id).current_state is AlertState.FIRING
    assert count_checks(session_factory) == 1  # only the first (accepted) failure


def test_unhandled_evaluation_errors_are_logged_without_details(
    session_factory: Factory, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    import logging

    rid = insert_resource(session_factory, url="https://example.com/p?token=SECRET123")
    make_rule(session_factory, rid, ST)
    claim = claim_resource(session_factory, rid)
    monkeypatch.setattr(lifecycle, "_event", lambda *a, **k: (_ for _ in ()).throw(OSError("db")))

    with (
        caplog.at_level(logging.ERROR, logger="cloudpulse.alerting"),
        session_factory() as session,
        pytest.raises(OSError),
    ):
        repo.complete(session, claim, make_result(X), CheckSource.SCHEDULED)

    (record,) = [r for r in caplog.records if r.getMessage() == "alert evaluation failed"]
    assert (record.resource_id, record.error) == (str(rid), "OSError")
    assert "SECRET123" not in caplog.text


# --- Defaults & settings --------------------------------------------------------------------


def test_default_rule_cap_setting_is_bounded(settings: Settings):
    assert settings.alerting_max_rules_per_resource == 20
