"""History, summary and time-series endpoints against PostgreSQL with a hand-computed dataset.

Dataset (resource ``R``, all UTC on 2026-03-10; latency in ms):

    12:00 HEALTHY  100        12:06 HEALTHY  400        13:05 HEALTHY  600
    12:01 HEALTHY  200        12:07 HEALTHY  500        13:06 DOWN     (no response)
    12:02 DEGRADED 300 (503)
    12:03 DOWN     (no response)

Expected, whole dataset: total 8 = 5 healthy + 1 degraded + 2 down; availability 5/8 = 62.5;
latency samples 6 (100..600): avg 350, min 100, max 600, p50 350 and p95 575
(percentile_cont: position 0.95 * 5 = 4.75 -> 500 + 0.75 * 100).
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.models.enums import CheckSource, ErrorCategory
from app.models.resource import ResourceStatus as S
from tests.helpers import insert_check, insert_resource

pytestmark = pytest.mark.integration

Factory = sessionmaker[Session]
BASE = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
START, END = "2026-03-10T12:00:00Z", "2026-03-10T14:00:00Z"


def at(minutes: int, seconds: int = 0) -> datetime:
    return BASE + timedelta(minutes=minutes, seconds=seconds)


@pytest.fixture
def resource(session_factory: Factory) -> uuid.UUID:
    resource_id = insert_resource(session_factory, name="R")
    rows = [
        (0, S.HEALTHY, 100.0, 200, None),
        (1, S.HEALTHY, 200.0, 200, None),
        (2, S.DEGRADED, 300.0, 503, "unexpected_status"),
        (3, S.DOWN, None, None, "timeout"),
        (6, S.HEALTHY, 400.0, 200, None),
        (7, S.HEALTHY, 500.0, 200, None),
        (65, S.HEALTHY, 600.0, 200, None),
        (66, S.DOWN, None, None, "dns"),
    ]
    for minute, status, rt, http, category in rows:
        insert_check(
            session_factory,
            resource_id,
            at(minute),
            status=status,
            response_time_ms=rt,
            http_status_code=http,
            error_category=ErrorCategory(category) if category else None,
            error_summary=f"{category}: x" if category else None,
        )
    return resource_id


def url(resource_id, suffix: str, **params) -> str:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"/api/v1/resources/{resource_id}{suffix}" + (f"?{query}" if query else "")


# --- Summary --------------------------------------------------------------------------------


def test_summary_matches_hand_computed_values(pg_client: TestClient, resource):
    body = pg_client.get(url(resource, "/metrics/summary", start=START, end=END)).json()

    assert body["resource_id"] == str(resource)
    assert body["window"] == {"start": "2026-03-10T12:00:00Z", "end": "2026-03-10T14:00:00Z"}
    assert body["checks"] == {"total": 8, "healthy": 5, "degraded": 1, "down": 2}
    assert body["availability_percent"] == 62.5
    assert body["latency"] == {
        "samples": 6,
        "avg_ms": 350.0,
        "min_ms": 100.0,
        "max_ms": 600.0,
        "p50_ms": 350.0,
        "p95_ms": 575.0,
    }
    assert body["first_checked_at"] == "2026-03-10T12:00:00Z"
    assert body["last_checked_at"] == "2026-03-10T13:06:00Z"


def test_degraded_is_not_healthy_for_availability_but_counts_for_latency(
    pg_client: TestClient, session_factory: Factory
):
    rid = insert_resource(session_factory)
    insert_check(session_factory, rid, at(0), status=S.HEALTHY, response_time_ms=100.0)
    insert_check(
        session_factory, rid, at(1), status=S.DEGRADED, response_time_ms=300.0, http_status_code=500
    )

    body = pg_client.get(url(rid, "/metrics/summary", start=START, end=END)).json()

    assert body["availability_percent"] == 50.0  # 1 healthy / 2 checks
    assert body["latency"]["samples"] == 2  # the DEGRADED response time is included
    assert body["latency"]["avg_ms"] == 200.0


def test_down_checks_are_excluded_from_latency_statistics(
    pg_client: TestClient, session_factory: Factory
):
    rid = insert_resource(session_factory)
    insert_check(session_factory, rid, at(0), status=S.DOWN)
    insert_check(session_factory, rid, at(1), status=S.DOWN)

    body = pg_client.get(url(rid, "/metrics/summary", start=START, end=END)).json()

    assert body["checks"] == {"total": 2, "healthy": 0, "degraded": 0, "down": 2}
    assert body["availability_percent"] == 0.0  # real observations, all failed: 0, not null
    assert body["latency"] == {
        "samples": 0,
        "avg_ms": None,
        "min_ms": None,
        "max_ms": None,
        "p50_ms": None,
        "p95_ms": None,
    }


def test_summary_of_a_resource_with_no_checks_has_null_aggregates(
    pg_client: TestClient, session_factory: Factory
):
    rid = insert_resource(session_factory)
    body = pg_client.get(url(rid, "/metrics/summary", start=START, end=END)).json()

    assert body["checks"] == {"total": 0, "healthy": 0, "degraded": 0, "down": 0}
    assert body["availability_percent"] is None  # unknown, not 0% and not 100%
    assert all(v is None for k, v in body["latency"].items() if k != "samples")
    assert body["latency"]["samples"] == 0
    assert body["first_checked_at"] is None and body["last_checked_at"] is None


def test_availability_is_rounded_half_up_to_two_decimals(
    pg_client: TestClient, session_factory: Factory
):
    rid = insert_resource(session_factory)
    for minute, status in enumerate([S.HEALTHY, S.HEALTHY, S.DOWN]):
        insert_check(session_factory, rid, at(minute), status=status)

    body = pg_client.get(url(rid, "/metrics/summary", start=START, end=END)).json()
    assert body["availability_percent"] == 66.67  # 2/3


def test_window_is_inclusive_of_start_and_exclusive_of_end(pg_client: TestClient, resource):
    # Checks exist exactly at 12:01 and 12:03 (and 12:02 between them).
    def total(start: str, end: str) -> int:
        r = pg_client.get(url(resource, "/metrics/summary", start=start, end=end)).json()
        return r["checks"]["total"]

    assert total("2026-03-10T12:01:00Z", "2026-03-10T12:03:00Z") == 2  # 12:01, 12:02; not 12:03
    assert total("2026-03-10T12:01:00Z", "2026-03-10T12:03:00.000001Z") == 3  # now includes 12:03
    assert total("2026-03-10T12:03:00Z", "2026-03-10T12:04:00Z") == 1  # start inclusive
    # Adjacent windows partition the data: nothing counted twice, nothing dropped.
    assert total(START, "2026-03-10T12:30:00Z") + total("2026-03-10T12:30:00Z", END) == 8


def test_timestamps_with_offsets_are_normalised_to_utc(pg_client: TestClient, resource):
    behind = pg_client.get(
        url(
            resource,
            "/metrics/summary",
            start="2026-03-10T07:00:00-05:00",
            end="2026-03-10T09:00:00-05:00",
        )
    ).json()
    # A literal "+" must be URL-encoded as %2B; 14:00+02:00 is 12:00Z and 16:00+02:00 is 14:00Z.
    ahead = pg_client.get(
        url(
            resource,
            "/metrics/summary",
            start="2026-03-10T14:00:00%2B02:00",
            end="2026-03-10T16:00:00%2B02:00",
        )
    ).json()
    for body in (behind, ahead):
        assert body["window"] == {"start": "2026-03-10T12:00:00Z", "end": "2026-03-10T14:00:00Z"}
        assert body["checks"]["total"] == 8


def test_naive_timestamps_are_read_as_utc(pg_client: TestClient, resource):
    body = pg_client.get(
        url(resource, "/metrics/summary", start="2026-03-10T12:00:00", end="2026-03-10T14:00:00")
    ).json()
    assert body["window"]["start"] == "2026-03-10T12:00:00Z"
    assert body["checks"]["total"] == 8


def test_other_resources_history_is_never_included(
    pg_client: TestClient, resource, session_factory: Factory
):
    other = insert_resource(session_factory, name="other")
    insert_check(session_factory, other, at(0), status=S.DOWN)

    body = pg_client.get(url(resource, "/metrics/summary", start=START, end=END)).json()
    assert body["checks"]["total"] == 8


# --- Time series ----------------------------------------------------------------------------


def test_five_minute_buckets_are_utc_aligned_sparse_and_correct(pg_client: TestClient, resource):
    body = pg_client.get(
        url(resource, "/metrics/timeseries", start=START, end=END, interval="5m")
    ).json()

    assert body["interval"] == "5m"
    starts = [b["bucket_start"] for b in body["buckets"]]
    # 12:00 (m0-m3), 12:05 (m6,m7), 13:05 (m65,m66). Nothing in between: no empty buckets.
    assert starts == ["2026-03-10T12:00:00Z", "2026-03-10T12:05:00Z", "2026-03-10T13:05:00Z"]
    first, second, third = body["buckets"]
    assert first == {
        "bucket_start": "2026-03-10T12:00:00Z",
        "total": 4,
        "healthy": 2,
        "degraded": 1,
        "down": 1,
        "availability_percent": 50.0,
        "avg_response_time_ms": 200.0,
        "p95_response_time_ms": 290.0,  # [100,200,300]: 200 + 0.9 * 100
    }
    assert second["total"] == 2 and second["availability_percent"] == 100.0
    assert second["avg_response_time_ms"] == 450.0 and second["p95_response_time_ms"] == 495.0
    assert third["total"] == 2 and third["healthy"] == 1 and third["down"] == 1
    assert third["availability_percent"] == 50.0
    assert third["avg_response_time_ms"] == 600.0  # the DOWN check has no latency


def test_hourly_buckets_and_aggregation_is_consistent_with_the_summary(
    pg_client: TestClient, resource
):
    series = pg_client.get(
        url(resource, "/metrics/timeseries", start=START, end=END, interval="1h")
    ).json()
    summary = pg_client.get(url(resource, "/metrics/summary", start=START, end=END)).json()

    hours = {b["bucket_start"]: b for b in series["buckets"]}
    assert set(hours) == {"2026-03-10T12:00:00Z", "2026-03-10T13:00:00Z"}
    assert hours["2026-03-10T12:00:00Z"]["total"] == 6
    assert hours["2026-03-10T12:00:00Z"]["availability_percent"] == 66.67
    assert hours["2026-03-10T12:00:00Z"]["avg_response_time_ms"] == 300.0
    assert (
        hours["2026-03-10T12:00:00Z"]["p95_response_time_ms"] == 480.0
    )  # [100..500]: 400 + .8*100
    for key in ("total", "healthy", "degraded", "down"):
        assert sum(b[key] for b in series["buckets"]) == summary["checks"][key]


def test_daily_buckets_align_to_utc_midnight(pg_client: TestClient, session_factory: Factory):
    rid = insert_resource(session_factory)
    insert_check(
        session_factory, rid, datetime(2026, 3, 9, 23, 59, 59, tzinfo=UTC), response_time_ms=1.0
    )
    insert_check(
        session_factory, rid, datetime(2026, 3, 10, 0, 0, 0, tzinfo=UTC), response_time_ms=2.0
    )
    insert_check(
        session_factory, rid, datetime(2026, 3, 10, 23, 59, 59, tzinfo=UTC), response_time_ms=3.0
    )

    body = pg_client.get(
        url(
            rid,
            "/metrics/timeseries",
            start="2026-03-09T00:00:00Z",
            end="2026-03-11T00:00:00Z",
            interval="1d",
        )
    ).json()

    assert [(b["bucket_start"], b["total"]) for b in body["buckets"]] == [
        ("2026-03-09T00:00:00Z", 1),
        ("2026-03-10T00:00:00Z", 2),
    ]


def test_a_partial_first_bucket_only_counts_checks_inside_the_window(
    pg_client: TestClient, resource
):
    # Window starts mid-bucket (12:02): the 12:00 bucket then holds only 12:02 and 12:03.
    body = pg_client.get(
        url(resource, "/metrics/timeseries", start="2026-03-10T12:02:00Z", end=END, interval="5m")
    ).json()
    assert body["buckets"][0]["bucket_start"] == "2026-03-10T12:00:00Z"
    assert body["buckets"][0]["total"] == 2


def test_auto_interval_picks_the_finest_interval_within_the_bucket_limit(
    pg_client: TestClient, resource
):
    one_hour = pg_client.get(
        url(
            resource,
            "/metrics/timeseries",
            start="2026-03-10T12:00:00Z",
            end="2026-03-10T13:00:00Z",
        )
    ).json()
    one_day = pg_client.get(
        url(
            resource,
            "/metrics/timeseries",
            start="2026-03-10T00:00:00Z",
            end="2026-03-11T00:00:00Z",
        )
    ).json()
    assert one_hour["interval"] == "1m"  # 60 buckets
    assert one_day["interval"] == "5m"  # 1m would be 1440 > 1000 buckets


def test_empty_window_returns_no_buckets(pg_client: TestClient, resource):
    body = pg_client.get(
        url(
            resource,
            "/metrics/timeseries",
            start="2026-03-01T00:00:00Z",
            end="2026-03-02T00:00:00Z",
        )
    ).json()
    assert body["buckets"] == []


# --- History --------------------------------------------------------------------------------


def test_history_is_newest_first_with_the_documented_fields(pg_client: TestClient, resource):
    body = pg_client.get(url(resource, "/checks", start=START, end=END)).json()

    assert body["resource_id"] == str(resource)
    assert body["limit"] == 50 and body["next_cursor"] is None
    times = [i["checked_at"] for i in body["items"]]
    assert times == sorted(times, reverse=True) and len(times) == 8
    newest = body["items"][0]
    assert set(newest) == {
        "id", "checked_at", "status", "http_status_code", "response_time_ms", "duration_ms",
        "error_category", "error_summary", "source", "created_at",
    }  # fmt: skip
    assert newest["status"] == "DOWN" and newest["error_category"] == "dns"
    assert newest["response_time_ms"] is None and newest["source"] == "SCHEDULED"
    degraded = next(i for i in body["items"] if i["status"] == "DEGRADED")
    assert degraded["http_status_code"] == 503 and degraded["response_time_ms"] == 300.0
    assert not {"lease_token", "lease_owner", "resource_id"} & degraded.keys()


def test_history_status_filter(pg_client: TestClient, resource):
    down = pg_client.get(url(resource, "/checks", start=START, end=END, status="DOWN")).json()
    assert [i["status"] for i in down["items"]] == ["DOWN", "DOWN"]
    assert (
        pg_client.get(url(resource, "/checks", start=START, end=END, status="UNKNOWN")).status_code
        == 422
    )
    assert pg_client.get(url(resource, "/checks", status="BROKEN")).status_code == 422


def test_keyset_pagination_is_stable_across_equal_timestamps(
    pg_client: TestClient, session_factory: Factory
):
    rid = insert_resource(session_factory)
    expected: list[tuple[datetime, uuid.UUID]] = []
    for minute in (0, 1, 1, 1, 1, 1, 2):  # five rows share 12:01 exactly
        row_id = insert_check(session_factory, rid, at(minute), response_time_ms=1.0)
        expected.append((at(minute), row_id))
    expected.sort(reverse=True)  # (checked_at desc, id desc)

    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        params = {"start": START, "end": END, "limit": 2}
        if cursor:
            params["cursor"] = cursor
        body = pg_client.get(url(rid, "/checks", **params)).json()
        seen += [i["id"] for i in body["items"]]
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert pages == 4  # 7 rows at 2 per page
    assert seen == [str(row_id) for _, row_id in expected]  # no duplicates, no gaps, same order


def test_pagination_with_a_page_exactly_filling_the_data_has_no_dangling_cursor(
    pg_client: TestClient, resource
):
    body = pg_client.get(url(resource, "/checks", start=START, end=END, limit=8)).json()
    assert len(body["items"]) == 8 and body["next_cursor"] is None


def test_large_histories_page_through_completely(pg_client: TestClient, session_factory: Factory):
    rid = insert_resource(session_factory)
    from sqlalchemy import text

    with session_factory() as session:
        session.execute(
            text(
                """INSERT INTO resource_check_results
                   (id, resource_id, checked_at, status, response_time_ms, duration_ms, source)
                   SELECT gen_random_uuid(), :rid, :base + (g * interval '1 second'), 'HEALTHY',
                          g % 500, 1, 'SCHEDULED'
                   FROM generate_series(0, 1199) g"""
            ),
            {"rid": rid, "base": BASE},
        )
        session.commit()

    seen: list[str] = []
    cursor = None
    while True:
        params = {"start": START, "end": END, "limit": 200}
        if cursor:
            params["cursor"] = cursor
        body = pg_client.get(url(rid, "/checks", **params)).json()
        seen += [i["id"] for i in body["items"]]
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == 1200 and len(set(seen)) == 1200


def test_history_boundaries_follow_the_half_open_window(pg_client: TestClient, resource):
    body = pg_client.get(
        url(resource, "/checks", start="2026-03-10T12:01:00Z", end="2026-03-10T12:03:00Z")
    ).json()
    assert [i["checked_at"] for i in body["items"]] == [
        "2026-03-10T12:02:00Z",
        "2026-03-10T12:01:00Z",
    ]


def test_empty_history_is_an_empty_page_not_an_error(
    pg_client: TestClient, session_factory: Factory
):
    rid = insert_resource(session_factory)
    body = pg_client.get(url(rid, "/checks", start=START, end=END)).json()
    assert body["items"] == [] and body["next_cursor"] is None


def test_disabled_resources_keep_their_history(pg_client: TestClient, resource):
    pg_client.patch(f"/api/v1/resources/{resource}", json={"is_enabled": False})
    body = pg_client.get(url(resource, "/checks", start=START, end=END)).json()
    assert len(body["items"]) == 8
    summary = pg_client.get(url(resource, "/metrics/summary", start=START, end=END)).json()
    assert summary["checks"]["total"] == 8


def test_deleting_a_resource_removes_its_history_endpoints(pg_client: TestClient, resource):
    assert pg_client.delete(f"/api/v1/resources/{resource}").status_code == 204
    for suffix in ("/checks", "/metrics/summary", "/metrics/timeseries"):
        assert pg_client.get(url(resource, suffix)).status_code == 404


# --- Errors ---------------------------------------------------------------------------------


@pytest.mark.parametrize("suffix", ["/checks", "/metrics/summary", "/metrics/timeseries"])
def test_unknown_resource_is_404(pg_client: TestClient, suffix: str):
    response = pg_client.get(url(uuid.uuid4(), suffix, start=START, end=END))
    assert response.status_code == 404
    assert response.json() == {"detail": "Resource not found"}


# --- Manual and scheduled checks feed history ----------------------------------------------


def test_a_manual_check_through_the_api_appears_in_history(
    pg_client: TestClient, session_factory: Factory
):
    created = pg_client.post(
        "/api/v1/resources", json={"name": "m", "url": "https://example.com/health"}
    ).json()
    status = pg_client.post(f"/api/v1/resources/{created['id']}/check").json()

    now = datetime.now(UTC)
    body = pg_client.get(
        url(
            created["id"],
            "/checks",
            start=(now - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            end=(now + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        )
    ).json()
    (item,) = body["items"]
    assert item["source"] == CheckSource.MANUAL.value
    assert item["status"] == "HEALTHY" and item["response_time_ms"] == 12.5
    assert (
        item["checked_at"].replace("Z", "+00:00")
        == datetime.fromisoformat(status["last_checked_at"]).astimezone(UTC).isoformat()
    )  # identical to the resource's last_checked_at
