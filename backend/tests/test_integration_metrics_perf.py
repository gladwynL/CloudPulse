"""Modest, reproducible performance check on synthetic data (not a production benchmark).

Run with ``-s`` to see timings:  pytest tests/test_integration_metrics_perf.py -s

Dataset: 3 resources x 8,640 checks (30 days at one check per 5 minutes) = 25,920 rows, with a
realistic status mix. Assertions are deliberately generous on time (this is a laptop and a
container) and strict on what matters: the index is used for narrow windows and the memory
held by the application stays small because aggregation happens in PostgreSQL.
"""

import json
import time
import tracemalloc
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session, sessionmaker

from app.repositories import check_results as repo
from tests.helpers import insert_resource

pytestmark = pytest.mark.integration

Factory = sessionmaker[Session]
ROWS_PER_RESOURCE = 8640
INDEX = "ix_resource_check_results_resource_checked_at_id"


@pytest.fixture
def dataset(session_factory: Factory) -> tuple[uuid.UUID, datetime]:
    ids = [insert_resource(session_factory, name=f"perf-{i}") for i in range(3)]
    with session_factory() as session:
        for rid in ids:
            session.execute(
                text(
                    """INSERT INTO resource_check_results
                       (id, resource_id, checked_at, status, http_status_code, response_time_ms,
                        duration_ms, source)
                       SELECT gen_random_uuid(), :rid, now() - (g * interval '5 minutes'),
                              CASE WHEN g % 20 = 0 THEN 'DOWN'
                                   WHEN g % 10 = 1 THEN 'DEGRADED' ELSE 'HEALTHY' END,
                              CASE WHEN g % 20 = 0 THEN NULL
                                   WHEN g % 10 = 1 THEN 503 ELSE 200 END,
                              CASE WHEN g % 20 = 0 THEN NULL ELSE 50 + (g * 37 % 900) END,
                              60, 'SCHEDULED'
                       FROM generate_series(0, :n - 1) g"""
                ),
                {"rid": rid, "n": ROWS_PER_RESOURCE},
            )
        session.execute(text("ANALYZE resource_check_results"))
        session.commit()
    with session_factory() as session:
        now = session.execute(text("SELECT now()")).scalar_one()
    return ids[0], now


def explain(session: Session, stmt) -> str:
    compiled = stmt.compile(dialect=postgresql.dialect())
    plan = session.connection().exec_driver_sql(
        "EXPLAIN (FORMAT JSON) " + str(compiled), dict(compiled.params)
    )
    return json.dumps(plan.scalar_one())


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def test_narrow_window_queries_use_the_composite_index(
    session_factory: Factory, dataset: tuple[uuid.UUID, datetime]
):
    rid, now = dataset
    end, start = now + timedelta(minutes=1), now - timedelta(hours=24)
    statements = {
        "history first page": repo.list_checks_stmt(
            rid, start=start, end=end, status=None, limit=51, after=None
        ),
        "history with cursor": repo.list_checks_stmt(
            rid, start=start, end=end, status=None, limit=51, after=(now, uuid.uuid4())
        ),
        "summary 24h": repo.summary_stmt(rid, start, end),
        "timeseries 24h/5m": repo.timeseries_stmt(rid, start, end, timedelta(minutes=5)),
    }
    with session_factory() as session:
        for name, stmt in statements.items():
            plan = explain(session, stmt)
            assert INDEX in plan, f"{name}: expected the composite index, plan was {plan[:300]}"
            assert "Seq Scan" not in plan, f"{name}: sequential scan on a narrow window"


def test_retention_delete_uses_the_checked_at_index(
    session_factory: Factory, dataset: tuple[uuid.UUID, datetime]
):
    with session_factory() as session:
        plan = json.dumps(
            session.execute(
                text(
                    "EXPLAIN (FORMAT JSON) SELECT id FROM resource_check_results "
                    "WHERE checked_at < now() - interval '29 days' ORDER BY checked_at LIMIT 1000"
                )
            ).scalar_one()
        )
    assert "ix_resource_check_results_checked_at" in plan


def test_api_latency_and_memory_with_thousands_of_rows(
    pg_client: TestClient, dataset: tuple[uuid.UUID, datetime]
):
    rid, now = dataset
    end = now + timedelta(minutes=1)
    day_ago, month_ago = end - timedelta(hours=24), end - timedelta(days=30)
    requests = {
        "history page (200 rows)": f"/checks?start={iso(month_ago)}&end={iso(end)}&limit=200",
        "summary 24h": f"/metrics/summary?start={iso(day_ago)}&end={iso(end)}",
        "summary 30d (8.6k rows)": f"/metrics/summary?start={iso(month_ago)}&end={iso(end)}",
        "timeseries 30d/1h": (
            f"/metrics/timeseries?start={iso(month_ago)}&end={iso(end)}&interval=1h"
        ),
        "timeseries 24h/5m": (
            f"/metrics/timeseries?start={iso(day_ago)}&end={iso(end)}&interval=5m"
        ),
    }
    pg_client.get(f"/api/v1/resources/{rid}/metrics/summary")  # warm up connections and imports

    tracemalloc.start()
    print()
    for name, path in requests.items():
        tracemalloc.reset_peak()
        started = time.perf_counter()
        response = pg_client.get(f"/api/v1/resources/{rid}{path}")
        elapsed_ms = (time.perf_counter() - started) * 1000
        _, peak = tracemalloc.get_traced_memory()
        assert response.status_code == 200, response.text
        print(f"  {name:<26} {elapsed_ms:7.1f} ms   peak Python alloc {peak / 1024:8.0f} KiB")
        assert elapsed_ms < 2000, f"{name} took {elapsed_ms:.0f} ms"
        assert peak < 8 * 1024 * 1024, f"{name} allocated {peak / 1e6:.1f} MB"
    tracemalloc.stop()

    # Sanity: the 30-day summary really aggregated all 8,640 rows without returning them.
    body = pg_client.get(f"/api/v1/resources/{rid}{requests['summary 30d (8.6k rows)']}").json()
    assert body["checks"]["total"] == ROWS_PER_RESOURCE
    assert body["checks"]["down"] == ROWS_PER_RESOURCE // 20
