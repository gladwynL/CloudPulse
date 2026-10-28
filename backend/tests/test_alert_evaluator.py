"""The alert state machine in isolation: pure functions, no database."""

from decimal import Decimal

import pytest

from app.alerting.evaluator import (
    STREAK_CAP,
    Classification,
    Evaluation,
    Observation,
    Progress,
    RuleSpec,
    Transition,
    classify,
    evaluate,
)
from app.models.enums import (
    AlertOperator,
    AlertRuleType,
    AlertState,
    StatusCondition,
)
from app.models.resource import ResourceStatus as S

H, D, X = S.HEALTHY, S.DEGRADED, S.DOWN  # X = DOWN
FIRING, OK, INSUFFICIENT = AlertState.FIRING, AlertState.OK, AlertState.INSUFFICIENT_DATA


def obs(status: S, rt: str | None = None) -> Observation:
    return Observation(status, None if rt is None else Decimal(rt))


def failures(n: int = 3, recovery: int = 1) -> RuleSpec:
    return RuleSpec(
        AlertRuleType.CONSECUTIVE_FAILURES, recovery_successes=recovery, consecutive_failures=n
    )


def latency(
    thr: str = "1000",
    n: int = 3,
    recovery: int = 1,
    op: AlertOperator = AlertOperator.GREATER_THAN,
) -> RuleSpec:
    return RuleSpec(
        AlertRuleType.LATENCY_THRESHOLD,
        recovery_successes=recovery,
        consecutive_failures=n,
        operator=op,
        threshold=Decimal(thr),
    )


def status_rule(cond: StatusCondition = StatusCondition.DOWN, recovery: int = 1) -> RuleSpec:
    return RuleSpec(AlertRuleType.STATUS, recovery_successes=recovery, status_condition=cond)


def run(spec: RuleSpec, observations: list) -> tuple[Progress, list[Transition]]:
    """Feed observations (or bare statuses) in order from a fresh rule; return the final progress
    and each transition."""
    progress, transitions = Progress(), []
    for o in observations:
        o = o if isinstance(o, Observation) else obs(o)  # bare statuses are allowed
        result = evaluate(spec, progress, o)
        progress = result.progress
        transitions.append(result.transition)
    return progress, transitions


T, R, N = Transition.TRIGGERED, Transition.RECOVERED, Transition.NONE


# --- Classification ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (H, Classification.RECOVER),
        (D, Classification.MATCH),
        (X, Classification.MATCH),
    ],
)
def test_consecutive_failure_rule_counts_degraded_and_down_as_unhealthy(status, expected):
    assert classify(failures(), obs(status)) is expected


@pytest.mark.parametrize(
    ("condition", "status", "expected"),
    [
        (StatusCondition.DOWN, X, Classification.MATCH),
        (StatusCondition.DOWN, D, Classification.NEUTRAL),  # degraded neither triggers nor recovers
        (StatusCondition.DOWN, H, Classification.RECOVER),
        (StatusCondition.DEGRADED, D, Classification.MATCH),
        (StatusCondition.DEGRADED, X, Classification.NEUTRAL),
        (StatusCondition.DEGRADED, H, Classification.RECOVER),
        (StatusCondition.UNHEALTHY, D, Classification.MATCH),
        (StatusCondition.UNHEALTHY, X, Classification.MATCH),
        (StatusCondition.UNHEALTHY, H, Classification.RECOVER),
    ],
)
def test_status_rule_conditions(condition, status, expected):
    assert classify(status_rule(condition), obs(status)) is expected


@pytest.mark.parametrize(
    ("operator", "latency_ms", "expected"),
    [
        (AlertOperator.GREATER_THAN, "1000.01", Classification.MATCH),
        (AlertOperator.GREATER_THAN, "1000.00", Classification.RECOVER),  # boundary: not greater
        (AlertOperator.GREATER_THAN, "999.99", Classification.RECOVER),
        (AlertOperator.GREATER_THAN_OR_EQUAL, "1000.00", Classification.MATCH),  # boundary: equal
        (AlertOperator.GREATER_THAN_OR_EQUAL, "999.99", Classification.RECOVER),
        (AlertOperator.GREATER_THAN_OR_EQUAL, "5000", Classification.MATCH),
    ],
)
def test_latency_threshold_boundaries(operator, latency_ms, expected):
    assert classify(latency("1000", op=operator), obs(H, latency_ms)) is expected


@pytest.mark.parametrize("status", [H, D, X])
def test_observations_without_latency_are_neutral_for_latency_rules(status):
    assert classify(latency(), obs(status, None)) is Classification.NEUTRAL


def test_a_slow_degraded_response_counts_for_latency_rules():
    # Latency rules judge response time, whatever the status code said.
    assert classify(latency("500"), obs(D, "900")) is Classification.MATCH


# --- Consecutive-failure rule -----------------------------------------------------------


def test_first_observation_moves_insufficient_data_to_ok_even_when_not_triggering():
    result = evaluate(failures(3), Progress(), obs(X))
    assert result.progress == Progress(OK, 1, 0)
    assert result.transition is N


def test_triggers_exactly_at_the_configured_streak():
    progress, transitions = run(failures(3), [X, X, X])
    assert transitions == [N, N, T]
    assert progress == Progress(FIRING, 3, 0)


def test_a_healthy_check_breaks_the_failure_streak():
    progress, transitions = run(failures(3), [X, X, H, X, X])
    assert T not in transitions
    assert progress == Progress(OK, 2, 0)


def test_intermittent_failures_never_trigger():
    _, transitions = run(failures(3), [X, H] * 20)
    assert T not in transitions


def test_degraded_and_down_mix_counts_toward_the_same_streak():
    _, transitions = run(failures(3), [D, X, D])
    assert transitions == [N, N, T]


def test_continued_failures_while_firing_never_retrigger():
    progress, transitions = run(failures(2), [X] * 10)
    assert transitions.count(T) == 1
    assert progress.state is FIRING and progress.failure_streak == 10


def test_trigger_count_of_one_fires_on_the_first_unhealthy_check():
    _, transitions = run(failures(1), [H, X])
    assert transitions == [N, T]


# --- Recovery ---------------------------------------------------------------------------


def test_recovers_after_the_configured_number_of_healthy_checks():
    progress, transitions = run(failures(2, recovery=3), [X, X, H, H, H])
    assert transitions == [N, T, N, N, R]
    assert progress == Progress(OK, 0, 0)


def test_a_failure_during_recovery_restarts_the_recovery_streak():
    progress, transitions = run(failures(2, recovery=3), [X, X, H, H, X, H, H])
    assert R not in transitions
    assert progress == Progress(FIRING, 0, 2)  # only two consecutive healthy so far
    _, more = run(failures(2, recovery=3), [X, X, H, H, X, H, H, H])
    assert more[-1] is R


def test_after_recovery_a_new_streak_is_needed_and_fires_again():
    _, transitions = run(failures(2), [X, X, H, X, X])
    assert transitions == [N, T, R, N, T]


def test_recovery_counters_do_not_leak_into_the_next_firing():
    spec = failures(2, recovery=2)
    progress, _ = run(spec, [X, X, H, H])  # fired then recovered
    assert progress == Progress(OK, 0, 0)
    again = evaluate(spec, progress, obs(X))
    assert again.progress == Progress(OK, 1, 0)


# --- STATUS rule ------------------------------------------------------------------------


def test_status_rule_fires_on_a_single_matching_result():
    progress, transitions = run(status_rule(StatusCondition.DOWN), [H, X])
    assert transitions == [N, T]
    assert progress.state is FIRING


def test_status_rule_recovers_only_on_healthy_not_on_a_neutral_status():
    spec = status_rule(StatusCondition.DOWN, recovery=1)
    _, transitions = run(spec, [X, D, H])
    assert transitions == [T, N, R]  # DEGRADED did not recover a DOWN rule


def test_status_rule_with_multiple_recovery_successes_needs_them_consecutive():
    spec = status_rule(StatusCondition.DOWN, recovery=2)
    _, transitions = run(spec, [X, H, D, H, H])
    assert transitions == [T, N, N, N, R]


# --- Latency rule -----------------------------------------------------------------------


def test_latency_rule_needs_consecutive_slow_responses():
    _, transitions = run(latency("1000", n=3), [obs(H, "1500"), obs(H, "1200"), obs(H, "1100")])
    assert transitions == [N, N, T]


def test_a_missing_latency_resets_a_building_latency_streak():
    spec = latency("1000", n=3)
    progress, transitions = run(
        spec, [obs(H, "2000"), obs(H, "2000"), obs(X, None), obs(H, "2000")]
    )
    assert T not in transitions
    assert progress.failure_streak == 1  # the timeout broke the streak instead of counting


def test_a_fast_response_resets_the_latency_streak():
    _, transitions = run(latency("1000", n=2), [obs(H, "2000"), obs(H, "100"), obs(H, "2000")])
    assert T not in transitions


def test_latency_incident_is_not_resolved_by_a_timeout():
    spec = latency("1000", n=1, recovery=1)
    _, transitions = run(spec, [obs(H, "2000"), obs(X, None), obs(X, None)])
    assert transitions == [T, N, N]  # still firing: no latency evidence of recovery


def test_latency_incident_recovers_on_valid_fast_observations():
    spec = latency("1000", n=1, recovery=2)
    _, transitions = run(spec, [obs(H, "2000"), obs(H, "100"), obs(X, None), obs(H, "100")])
    # the timeout between the two fast checks broke the recovery streak
    assert transitions == [T, N, N, N]
    _, again = run(spec, [obs(H, "2000"), obs(H, "100"), obs(H, "100")])
    assert again == [T, N, R]


def test_degraded_but_fast_response_counts_as_latency_recovery():
    spec = latency("1000", n=1, recovery=1)
    _, transitions = run(spec, [obs(H, "2000"), obs(D, "200")])
    assert transitions == [T, R]


# --- Invariants -------------------------------------------------------------------------


def test_evaluation_is_a_pure_function_of_its_inputs():
    spec, progress, o = failures(3), Progress(OK, 2, 0), obs(X)
    first = evaluate(spec, progress, o)
    assert evaluate(spec, progress, o) == first
    assert progress == Progress(OK, 2, 0)  # inputs are never mutated (frozen dataclasses)


def test_streaks_are_capped():
    result = evaluate(failures(1), Progress(FIRING, STREAK_CAP, 0), obs(X))
    assert result.progress.failure_streak == STREAK_CAP


def test_at_most_one_trigger_per_streak_over_random_sequences():
    import random

    rng = random.Random(1234)
    for _ in range(200):
        spec = failures(rng.randint(1, 4), recovery=rng.randint(1, 3))
        sequence = [rng.choice([H, D, X]) for _ in range(60)]
        progress = Progress()
        firing = False
        for status in sequence:
            result: Evaluation = evaluate(spec, progress, obs(status))
            if result.transition is T:
                assert not firing, "triggered while already firing"
                firing = True
            elif result.transition is R:
                assert firing, "recovered while not firing"
                firing = False
            progress = result.progress
            assert (progress.state is FIRING) == firing
