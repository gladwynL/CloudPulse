"""Alert evaluation cost per accepted check (modest, reproducible; not a production benchmark).

Run with ``-s`` to see the numbers:  pytest tests/test_integration_alert_perf.py -s
"""

import json
import statistics
import time
from collections import Counter

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session, sessionmaker

from app.models.enums import AlertOperator, AlertRuleType
from app.models.resource import ResourceStatus as S
from tests.helpers import feed, insert_resource, make_rule

pytestmark = pytest.mark.integration

Factory = sessionmaker[Session]
SAMPLES = 15


def add_rules(factory: Factory, rid, count: int) -> None:
    kinds = [
        dict(rule_type=AlertRuleType.CONSECUTIVE_FAILURES, consecutive_failures=3),
        dict(rule_type=AlertRuleType.STATUS),
        dict(
            rule_type=AlertRuleType.LATENCY_THRESHOLD,
            threshold=5000,
            operator=AlertOperator.GREATER_THAN,
            consecutive_failures=3,
        ),
    ]
    for index in range(count):
        make_rule(factory, rid, name=f"rule-{index}", **kinds[index % len(kinds)])


def measure(factory: Factory, engine, rid) -> tuple[Counter, float]:
    """Statement counts (by verb) and mean wall time for one accepted check, over several checks."""
    statements: Counter = Counter()

    def count(conn, cursor, statement, params, context, executemany):
        statements[statement.lstrip().split(None, 1)[0].upper()] += 1

    durations = []
    event.listen(engine, "before_cursor_execute", count)
    try:
        for _ in range(SAMPLES):
            started = time.perf_counter()
            feed(factory, rid, S.HEALTHY, 50.0)  # includes the claim; same for every variant
            durations.append((time.perf_counter() - started) * 1000)
    finally:
        event.remove(engine, "before_cursor_execute", count)
    per_check = Counter({verb: n / SAMPLES for verb, n in statements.items()})
    return per_check, statistics.mean(durations)


def test_reads_do_not_grow_with_the_number_of_rules(session_factory: Factory, pg_engine):
    one = insert_resource(session_factory, name="one-rule")
    twenty = insert_resource(session_factory, name="twenty-rules")
    none = insert_resource(session_factory, name="no-rules")
    add_rules(session_factory, one, 1)
    add_rules(session_factory, twenty, 20)

    base_stmts, base_ms = measure(session_factory, pg_engine, none)
    one_stmts, one_ms = measure(session_factory, pg_engine, one)
    many_stmts, many_ms = measure(session_factory, pg_engine, twenty)

    print()
    for label, stmts, ms in (
        ("no rules", base_stmts, base_ms),
        ("1 rule", one_stmts, one_ms),
        ("20 rules", many_stmts, many_ms),
    ):
        print(f"  {label:<9} {ms:6.1f} ms/check   statements/check: {dict(sorted(stmts.items()))}")

    # No N+1 reads: the number of SELECTs per check is the same for 1 and 20 rules.
    assert many_stmts["SELECT"] == one_stmts["SELECT"]
    # Writes are one UPDATE per evaluated rule at most (plus a constant), never more.
    assert many_stmts["UPDATE"] <= one_stmts["UPDATE"] + 20
    assert many_stmts["INSERT"] == one_stmts["INSERT"]
    # Generous wall-time ceiling for 20 rules on a laptop with PostgreSQL in Docker.
    assert many_ms < 400, f"{many_ms:.0f} ms per accepted check with 20 rules"


def test_rule_lookup_uses_the_partial_index_among_thousands_of_rules(session_factory: Factory):
    with session_factory() as session:
        session.execute(
            text(
                """INSERT INTO monitored_resources
                   (id, name, url, check_interval_seconds, timeout_seconds)
                   SELECT gen_random_uuid(), 'r' || g, 'https://example.com', 60, 10
                   FROM generate_series(1, 600) g"""
            )
        )
        session.execute(
            text(
                """INSERT INTO alert_rules
                   (id, resource_id, name, rule_type, status_condition, recovery_successes)
                   SELECT gen_random_uuid(), r.id, 'rule' || n, 'STATUS', 'DOWN', 1
                   FROM monitored_resources r, generate_series(1, 5) n"""
            )
        )
        session.execute(text("ANALYZE alert_rules"))
        session.commit()
        target = session.execute(text("SELECT resource_id FROM alert_rules LIMIT 1")).scalar_one()
        plan = json.dumps(
            session.execute(
                text(
                    "EXPLAIN (FORMAT JSON) SELECT * FROM alert_rules "
                    "WHERE resource_id = :r AND is_enabled ORDER BY id FOR UPDATE"
                ),
                {"r": target},
            ).scalar_one()
        )
        total = session.execute(text("SELECT count(*) FROM alert_rules")).scalar_one()
    assert total == 3000
    assert "ix_alert_rules_resource_enabled" in plan and "Seq Scan" not in plan


def test_active_and_listing_incident_queries_use_their_indexes(session_factory: Factory):
    with session_factory() as session:
        session.execute(
            text(
                """INSERT INTO incidents
                   (id, resource_name, rule_name, rule_type, title, severity, status,
                    triggered_at, resolved_at, resolution_reason, last_observed_at)
                   SELECT gen_random_uuid(), 'r', 'n', 'STATUS', 't', 'WARNING', 'RESOLVED',
                          now() - (g * interval '1 minute'), now(), 'RECOVERED', now()
                   FROM generate_series(1, 5000) g"""
            )
        )
        session.execute(text("ANALYZE incidents"))

        def plan(sql: str) -> str:
            return json.dumps(session.execute(text("EXPLAIN (FORMAT JSON) " + sql)).scalar_one())

        newest = plan("SELECT * FROM incidents ORDER BY triggered_at DESC, id DESC LIMIT 51")
        active = plan(
            "SELECT * FROM incidents WHERE status <> 'RESOLVED' "
            "ORDER BY triggered_at DESC, id DESC LIMIT 51"
        )
    assert "ix_incidents_triggered_at_id" in newest and "Seq Scan" not in newest
    assert "Seq Scan" not in active  # tiny partial index or the main one; never a table scan
