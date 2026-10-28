"""Races between workers, API calls and recovery. Real threads, real PostgreSQL.

Each race is repeated several times and judged on invariants that must hold whichever side wins:

* at most one active incident per rule; a rule is FIRING exactly when it has one
* a measurement is recorded at most once, and a discarded result leaves no trace
* no deadlocks (any would surface as an OperationalError from a thread)
"""

import asyncio
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.alerting import lifecycle
from app.models.alert import AlertRule, Incident
from app.models.enums import (
    AlertRuleType,
    AlertState,
    CheckSource,
    IncidentEventType,
    IncidentStatus,
    ResolutionReason,
)
from app.models.resource import ResourceStatus as S
from app.monitoring.scheduler import Scheduler
from app.monitoring.security import TargetPolicy
from app.monitoring.store import SqlCheckStore
from app.repositories import resources as repo
from app.schemas.resource import ResourceUpdate
from app.services import alerts as alert_service
from app.services import resources as resource_service
from tests.conftest import FakeChecker
from tests.helpers import (
    claim_resource,
    count_checks,
    events,
    feed,
    incidents,
    insert_check,
    insert_resource,
    make_result,
    make_rule,
)

pytestmark = pytest.mark.integration

Factory = sessionmaker[Session]
X, H = S.DOWN, S.HEALTHY
ST = AlertRuleType.STATUS
ROUNDS = 24


# Head starts (seconds) given to the first or second racer, cycled per round, so that both
# orderings and the interleavings in between actually happen instead of one side always winning.
STAGGERS = [0, 0.001, 0.003, 0.006, 0.01, 0.02, 0.04, 0.08]


def stagger(round_index: int) -> tuple[float, float]:
    delay = STAGGERS[round_index % len(STAGGERS)]
    return (0.0, delay) if round_index % 2 == 0 else (delay, 0.0)


def run_together(*callables, delays: tuple[float, ...] | None = None):
    """Start every callable at (nearly) the same instant, optionally offset by ``delays``;
    return their results (or the exception raised)."""
    barrier = threading.Barrier(len(callables))
    offsets = delays or (0.0,) * len(callables)

    def wrap(fn, offset):
        def inner():
            barrier.wait()
            if offset:
                time.sleep(offset)
            try:
                return fn()
            except Exception as exc:
                return exc

        return inner

    with ThreadPoolExecutor(len(callables)) as pool:
        futures = [pool.submit(wrap(fn, off)) for fn, off in zip(callables, offsets, strict=True)]
        return [f.result() for f in futures]


def assert_alert_invariants(factory: Factory) -> None:
    with factory() as session:
        rules = list(session.scalars(select(AlertRule)))
        active = list(session.scalars(select(Incident).where(Incident.status != "RESOLVED")))
        per_rule = Counter(i.alert_rule_id for i in active)
        assert all(count == 1 for count in per_rule.values()), "two active incidents for a rule"
        for rule in rules:
            has_incident = per_rule.get(rule.id, 0) == 1
            assert (rule.current_state is AlertState.FIRING) == has_incident, (
                f"rule {rule.name}: state {rule.current_state} vs active incident {has_incident}"
            )
            if not rule.is_enabled:
                assert not has_incident


def complete_with(factory: Factory, claim, status: S) -> bool:
    with factory() as session:
        accepted = repo.complete(session, claim, make_result(status), CheckSource.SCHEDULED)
        session.commit()
        return accepted


# --- Duplicate and concurrent completion ----------------------------------------------------


def test_simultaneous_completions_of_one_claim_open_exactly_one_incident(session_factory: Factory):
    rid = insert_resource(session_factory)
    rule_id = make_rule(session_factory, rid, ST)
    claim = claim_resource(session_factory, rid)

    outcomes = run_together(*[lambda: complete_with(session_factory, claim, X)] * 6)

    assert sorted(outcomes) == [False] * 5 + [True]
    assert count_checks(session_factory) == 1
    (incident,) = incidents(session_factory, rule_id=rule_id)
    assert [e.event_type for e in events(session_factory, incident.id)] == [
        IncidentEventType.OPENED
    ]
    with session_factory() as session:
        assert session.get(AlertRule, rule_id).failure_streak == 1
    assert_alert_invariants(session_factory)


def test_competing_workers_open_one_incident_per_rule(session_factory: Factory):
    rids = [insert_resource(session_factory, name=f"r{i}") for i in range(24)]
    rule_ids = [make_rule(session_factory, rid, ST, name=f"rule{i}") for i, rid in enumerate(rids)]

    def worker() -> None:
        while True:
            with session_factory() as session:
                claims = repo.claim_due(session, limit=3, owner="w", grace_seconds=30)
                session.commit()
            if not claims:
                return
            for claim in claims:
                complete_with(session_factory, claim, X)

    results = run_together(*[worker] * 6)

    assert all(r is None for r in results), results
    rows = incidents(session_factory)
    assert len(rows) == 24 and {i.alert_rule_id for i in rows} == set(rule_ids)
    assert count_checks(session_factory) == 24
    assert_alert_invariants(session_factory)


@pytest.mark.anyio
async def test_two_schedulers_produce_one_incident_per_rule(session_factory: Factory):
    rids = [insert_resource(session_factory, name=f"r{i}") for i in range(15)]
    for i, rid in enumerate(rids):
        make_rule(session_factory, rid, ST, name=f"rule{i}")
    down = FakeChecker(result=make_result(X))
    schedulers = [
        Scheduler(
            SqlCheckStore(session_factory, owner=f"w{n}", lease_grace_seconds=30),
            down,
            max_concurrent=4,
            poll_interval=0.01,
            shutdown_grace=2,
        )
        for n in range(2)
    ]
    stop = asyncio.Event()
    tasks = [asyncio.create_task(s.run(stop)) for s in schedulers]

    async def until_all_checked() -> None:
        while count_checks(session_factory) < 15:
            await asyncio.sleep(0.02)

    await asyncio.wait_for(until_all_checked(), 20)
    await asyncio.sleep(0.2)
    stop.set()
    await asyncio.wait_for(asyncio.gather(*tasks), 5)

    assert len(incidents(session_factory)) == 15
    assert_alert_invariants(session_factory)


def test_the_database_itself_blocks_a_second_incident_for_a_rule(session_factory: Factory):
    """Bypass the application's resource lock: two writers open an incident for one rule at once."""
    rid = insert_resource(session_factory)
    rule_id = make_rule(session_factory, rid, ST)
    check_id = insert_check(
        session_factory, rid, datetime(2026, 3, 10, 12, tzinfo=UTC), status=X
    )  # a real measurement: opened_by_check_id is a foreign key

    def open_one() -> uuid.UUID:
        with session_factory() as session:
            rule = session.get(AlertRule, rule_id)
            incident = lifecycle.open_incident(
                session,
                rule,
                resource_name="r",
                check_id=check_id,
                at=func.now(),
                details={},
            )
            session.commit()
            return incident.id

    results = run_together(open_one, open_one, open_one)

    assert sum(isinstance(r, uuid.UUID) for r in results) == 1
    assert all(isinstance(r, (uuid.UUID, IntegrityError)) for r in results)
    assert len(incidents(session_factory)) == 1


# --- Acknowledge vs automatic recovery ------------------------------------------------------


def test_acknowledgement_racing_recovery_stays_consistent(session_factory: Factory):
    outcomes: Counter[str] = Counter()
    for round_index in range(ROUNDS):
        rid = insert_resource(session_factory, name=f"r-{uuid.uuid4().hex[:6]}")
        rule_id = make_rule(session_factory, rid, ST, name="rule")
        feed(session_factory, rid, X)
        (incident,) = incidents(session_factory, rule_id=rule_id)
        claim = claim_resource(session_factory, rid)

        def ack(incident_id=incident.id):
            with session_factory() as session:
                return alert_service.acknowledge(session, incident_id)

        ack_result, recovered = run_together(
            ack, lambda c=claim: complete_with(session_factory, c, H), delays=stagger(round_index)
        )

        assert recovered is True
        (final,) = incidents(session_factory, rule_id=rule_id)
        types = [e.event_type for e in events(session_factory, final.id)]
        # Recovery always wins eventually: an acknowledged incident still resolves.
        assert final.status is IncidentStatus.RESOLVED
        assert final.resolution_reason is ResolutionReason.RECOVERED
        if isinstance(ack_result, alert_service.StateConflictError):
            outcomes["recovered-first"] += 1
            assert types == [IncidentEventType.OPENED, IncidentEventType.RECOVERED]
            assert final.acknowledged_at is None
        else:
            outcomes["acknowledged-first"] += 1
            assert not isinstance(ack_result, Exception), ack_result
            assert types == [
                IncidentEventType.OPENED,
                IncidentEventType.ACKNOWLEDGED,
                IncidentEventType.RECOVERED,
            ]
            assert final.acknowledged_at is not None
        assert final.resolved_at >= final.triggered_at
        # Clean slate for the next round.
        with session_factory() as session:
            resource_service.delete_resource(session, rid)
    print(f"\n  ack/recovery race outcomes over {ROUNDS} rounds: {dict(outcomes)}")


# --- Rule changes vs completion -------------------------------------------------------------


def test_disabling_a_rule_racing_a_completion_never_leaves_a_firing_disabled_rule(
    session_factory: Factory,
):
    outcomes: Counter[str] = Counter()
    for round_index in range(ROUNDS):
        rid = insert_resource(session_factory, name=f"r-{uuid.uuid4().hex[:6]}")
        rule_id = make_rule(session_factory, rid, ST, name="rule")
        claim = claim_resource(session_factory, rid)

        def disable(rid_=rule_id):
            with session_factory() as session:
                from app.schemas.alerts import AlertRuleUpdate

                return alert_service.update_rule(session, rid_, AlertRuleUpdate(is_enabled=False))

        disabled, accepted = run_together(
            disable,
            lambda c=claim: complete_with(session_factory, c, X),
            delays=stagger(round_index),
        )

        assert not isinstance(disabled, Exception), disabled
        assert accepted is True  # the check itself is always accepted
        assert_alert_invariants(session_factory)
        rows = incidents(session_factory, rule_id=rule_id)
        if rows:
            outcomes["fired-then-disabled"] += 1
            assert rows[0].resolution_reason is ResolutionReason.RULE_DISABLED
        else:
            outcomes["disabled-first"] += 1
        with session_factory() as session:
            resource_service.delete_resource(session, rid)
    print(f"\n  disable/completion race outcomes over {ROUNDS} rounds: {dict(outcomes)}")


def test_deleting_a_rule_racing_a_completion_is_consistent(session_factory: Factory):
    for round_index in range(ROUNDS):
        rid = insert_resource(session_factory, name=f"r-{uuid.uuid4().hex[:6]}")
        rule_id = make_rule(session_factory, rid, ST, name="rule")
        claim = claim_resource(session_factory, rid)

        def delete(rule=rule_id):
            with session_factory() as session:
                alert_service.delete_rule(session, rule)

        deleted, accepted = run_together(
            delete,
            lambda c=claim: complete_with(session_factory, c, X),
            delays=stagger(round_index),
        )

        assert deleted is None and accepted is True
        assert_alert_invariants(session_factory)
        for incident in incidents(session_factory):
            if incident.resource_id == rid or incident.resource_name.startswith("r-"):
                assert incident.status is IncidentStatus.RESOLVED
                assert incident.resolution_reason is ResolutionReason.RULE_DELETED
        with session_factory() as session:
            resource_service.delete_resource(session, rid)


def test_deleting_a_resource_racing_a_completion_leaves_no_active_incident(
    session_factory: Factory,
):
    outcomes: Counter[str] = Counter()
    for round_index in range(ROUNDS):
        rid = insert_resource(session_factory, name=f"r-{uuid.uuid4().hex[:6]}")
        make_rule(session_factory, rid, ST, name="rule")
        claim = claim_resource(session_factory, rid)

        def delete(rid_=rid):
            with session_factory() as session:
                resource_service.delete_resource(session, rid_)

        deleted, accepted = run_together(
            delete,
            lambda c=claim: complete_with(session_factory, c, X),
            delays=stagger(round_index),
        )

        assert deleted is None, deleted  # no deadlock, no integrity error
        assert isinstance(accepted, bool)
        assert_alert_invariants(session_factory)
        with session_factory() as session:
            assert session.scalars(select(AlertRule)).all() == []  # rules went with the resource
        if accepted:
            outcomes["completed-then-deleted"] += 1
            (incident,) = incidents(session_factory)
            assert incident.resolution_reason is ResolutionReason.RESOURCE_DELETED
            assert incident.resource_id is None
        else:
            outcomes["deleted-first"] += 1
            assert incidents(session_factory) == []
        with session_factory() as session:
            session.execute(Incident.__table__.delete())  # reset between rounds
            session.commit()
    print(f"\n  delete/completion race outcomes over {ROUNDS} rounds: {dict(outcomes)}")


def test_disabling_a_resource_racing_a_completion_is_consistent(session_factory: Factory, settings):
    for round_index in range(ROUNDS):
        rid = insert_resource(session_factory, name=f"r-{uuid.uuid4().hex[:6]}")
        make_rule(session_factory, rid, ST, name="rule")
        claim = claim_resource(session_factory, rid)

        def disable(rid_=rid):
            with session_factory() as session:
                resource_service.update_resource(
                    session, rid_, ResourceUpdate(is_enabled=False), settings, TargetPolicy()
                )

        disabled, accepted = run_together(
            disable,
            lambda c=claim: complete_with(session_factory, c, X),
            delays=stagger(round_index),
        )

        assert disabled is None, disabled
        assert isinstance(accepted, bool)
        assert_alert_invariants(session_factory)
        assert not any(i.status is not IncidentStatus.RESOLVED for i in incidents(session_factory))
        with session_factory() as session:
            resource_service.delete_resource(session, rid)
            session.execute(Incident.__table__.delete())
            session.commit()
