"""Deterministic alert-rule state machine. Pure: no database, no clock, no I/O.

Every accepted observation is classified for a rule as one of:

* ``MATCH``    the observation satisfies the rule's *trigger* condition
* ``RECOVER``  the observation satisfies the rule's *recovery* condition
* ``NEUTRAL``  it is neither (it says nothing either way)

Classification per rule type:

==================== ============================================ =================================
rule type            MATCH                                        RECOVER
==================== ============================================ =================================
CONSECUTIVE_FAILURES status is DEGRADED or DOWN                   status is HEALTHY
STATUS               status is in the configured condition set    status is HEALTHY
LATENCY_THRESHOLD    response time present and ``rt <op> thr``    response time present and not so
==================== ============================================ =================================

Everything else is NEUTRAL: a DEGRADED observation under a ``DOWN`` STATUS rule, or any observation
without a response time under a LATENCY rule (a timeout or DNS failure is *not* evidence that
latency recovered, and it also breaks a building latency streak rather than counting as a success).

Progress is two persisted counters and a state:

* ``failure_streak``  consecutive MATCH observations (any other classification resets it to 0)
* ``recovery_streak`` consecutive RECOVER observations while FIRING (anything else resets it)

State transitions:

* INSUFFICIENT_DATA / OK -> FIRING   when ``failure_streak`` reaches the rule's trigger count
  (1 for STATUS rules, ``consecutive_failures`` otherwise)
* INSUFFICIENT_DATA -> OK            on the first accepted observation that does not trigger
* FIRING -> OK                       when ``recovery_streak`` reaches ``recovery_successes``
* FIRING stays FIRING otherwise      (more MATCHes do not open new incidents)
"""

import enum
from dataclasses import dataclass
from decimal import Decimal

from app.models.enums import (
    AlertOperator,
    AlertRuleType,
    AlertState,
    StatusCondition,
)
from app.models.resource import ResourceStatus

# Streaks are bounded so a long outage cannot overflow the integer column.
STREAK_CAP = 1_000_000


class Classification(enum.StrEnum):
    MATCH = "MATCH"
    RECOVER = "RECOVER"
    NEUTRAL = "NEUTRAL"


class Transition(enum.StrEnum):
    NONE = "NONE"
    TRIGGERED = "TRIGGERED"
    RECOVERED = "RECOVERED"


@dataclass(frozen=True, slots=True)
class RuleSpec:
    """The part of a rule's configuration that affects evaluation."""

    rule_type: AlertRuleType
    recovery_successes: int
    consecutive_failures: int | None = None
    operator: AlertOperator | None = None
    threshold: Decimal | None = None
    status_condition: StatusCondition | None = None

    @property
    def trigger_count(self) -> int:
        return 1 if self.consecutive_failures is None else self.consecutive_failures


@dataclass(frozen=True, slots=True)
class Progress:
    state: AlertState = AlertState.INSUFFICIENT_DATA
    failure_streak: int = 0
    recovery_streak: int = 0


@dataclass(frozen=True, slots=True)
class Observation:
    status: ResourceStatus
    # HTTP latency rounded to two decimals; None when no response arrived.
    response_time_ms: Decimal | None


@dataclass(frozen=True, slots=True)
class Evaluation:
    progress: Progress
    transition: Transition
    classification: Classification


_UNHEALTHY = frozenset({ResourceStatus.DEGRADED, ResourceStatus.DOWN})
_CONDITIONS: dict[StatusCondition, frozenset[ResourceStatus]] = {
    StatusCondition.DOWN: frozenset({ResourceStatus.DOWN}),
    StatusCondition.DEGRADED: frozenset({ResourceStatus.DEGRADED}),
    StatusCondition.UNHEALTHY: _UNHEALTHY,
}


def classify(spec: RuleSpec, observation: Observation) -> Classification:
    if spec.rule_type is AlertRuleType.CONSECUTIVE_FAILURES:
        if observation.status in _UNHEALTHY:
            return Classification.MATCH
        return (
            Classification.RECOVER
            if observation.status is ResourceStatus.HEALTHY
            else Classification.NEUTRAL
        )

    if spec.rule_type is AlertRuleType.STATUS:
        assert spec.status_condition is not None
        if observation.status in _CONDITIONS[spec.status_condition]:
            return Classification.MATCH
        return (
            Classification.RECOVER
            if observation.status is ResourceStatus.HEALTHY
            else Classification.NEUTRAL
        )

    # LATENCY_THRESHOLD
    assert spec.operator is not None and spec.threshold is not None
    latency = observation.response_time_ms
    if latency is None:
        return Classification.NEUTRAL
    exceeded = (
        latency > spec.threshold
        if spec.operator is AlertOperator.GREATER_THAN
        else latency >= spec.threshold
    )
    return Classification.MATCH if exceeded else Classification.RECOVER


def evaluate(spec: RuleSpec, progress: Progress, observation: Observation) -> Evaluation:
    kind = classify(spec, observation)
    firing = progress.state is AlertState.FIRING

    failure_streak = (
        min(progress.failure_streak + 1, STREAK_CAP) if kind is Classification.MATCH else 0
    )

    if not firing:
        if kind is Classification.MATCH and failure_streak >= spec.trigger_count:
            return Evaluation(
                Progress(AlertState.FIRING, failure_streak, 0), Transition.TRIGGERED, kind
            )
        return Evaluation(Progress(AlertState.OK, failure_streak, 0), Transition.NONE, kind)

    if kind is Classification.RECOVER:
        recovery_streak = min(progress.recovery_streak + 1, STREAK_CAP)
        if recovery_streak >= spec.recovery_successes:
            return Evaluation(Progress(AlertState.OK, 0, 0), Transition.RECOVERED, kind)
        return Evaluation(
            Progress(AlertState.FIRING, failure_streak, recovery_streak), Transition.NONE, kind
        )

    # Still firing: a MATCH extends the failure streak; MATCH and NEUTRAL both break recovery.
    return Evaluation(Progress(AlertState.FIRING, failure_streak, 0), Transition.NONE, kind)
