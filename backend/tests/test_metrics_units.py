"""Pure logic behind history and metrics: windows, bucket math, cursors, rounding."""

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import Settings
from app.services import metrics as m
from app.services.resources import ResourceValidationError

NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)


@pytest.fixture
def cfg() -> Settings:
    return Settings(_env_file=None)


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


# --- availability -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("healthy", "total", "expected"),
    [
        (0, 0, None),  # no observations: unknown, never 0 or 100
        (0, 5, 0.0),
        (5, 5, 100.0),
        (1, 3, 33.33),
        (2, 3, 66.67),
        (5, 8, 62.5),
        (1, 800, 0.13),  # 0.125 rounds half UP (banker's rounding would give 0.12)
    ],
)
def test_availability_percent(healthy: int, total: int, expected: float | None):
    assert m.availability_percent(healthy, total) == expected


# --- windows ----------------------------------------------------------------------------


def test_window_defaults_to_the_configured_trailing_window(cfg: Settings):
    window = m.resolve_window(None, None, cfg, now=NOW)
    assert window.end == NOW
    assert window.start == NOW - timedelta(hours=24)


def test_naive_timestamps_are_utc_and_offsets_are_converted(cfg: Settings):
    naive = m.resolve_window(datetime(2026, 3, 10, 1), datetime(2026, 3, 10, 2), cfg)
    assert naive.start == utc(2026, 3, 10, 1)
    offset = datetime(2026, 3, 10, 7, tzinfo=timezone_minus_5())
    assert m.resolve_window(offset, utc(2026, 3, 10, 13), cfg).start == utc(2026, 3, 10, 12)


def timezone_minus_5():
    from datetime import timezone

    return timezone(timedelta(hours=-5))


@pytest.mark.parametrize("start_offset_s", [0, 1, 3600])  # start == end, or after end
def test_empty_or_reversed_windows_are_rejected(cfg: Settings, start_offset_s: int):
    with pytest.raises(ResourceValidationError) as error:
        m.resolve_window(NOW + timedelta(seconds=start_offset_s), NOW, cfg)
    assert (error.value.location, error.value.field) == ("query", "start")


def test_window_may_span_exactly_the_maximum_but_not_a_microsecond_more(cfg: Settings):
    span = timedelta(days=cfg.metrics_max_query_days)
    assert m.resolve_window(NOW - span, NOW, cfg).start == NOW - span
    with pytest.raises(ResourceValidationError):
        m.resolve_window(NOW - span - timedelta(microseconds=1), NOW, cfg)


# --- bucket arithmetic ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end", "interval", "expected"),
    [
        (utc(2026, 3, 10, 12), utc(2026, 3, 10, 13), "1m", 60),
        (utc(2026, 3, 10, 12), utc(2026, 3, 10, 12, 5), "5m", 1),  # end is exclusive
        (utc(2026, 3, 10, 12), utc(2026, 3, 10, 12, 5, 0, 1), "5m", 2),
        (utc(2026, 3, 10, 12, 4), utc(2026, 3, 10, 12, 6), "5m", 2),  # straddles a boundary
        (utc(2026, 3, 10, 12, 1), utc(2026, 3, 10, 12, 2), "1h", 1),
        (utc(2026, 3, 1), utc(2026, 3, 31), "1d", 30),
        (utc(2026, 3, 10, 23), utc(2026, 3, 11, 1), "1d", 2),  # midnight UTC boundary
    ],
)
def test_bucket_count_is_exact(start, end, interval, expected):
    assert m.bucket_count(m.Window(start, end), m.INTERVALS[interval]) == expected


def test_auto_interval_picks_the_finest_that_fits(cfg: Settings):
    def pick(hours: float) -> str:
        return m.choose_interval(m.Window(NOW - timedelta(hours=hours), NOW), "auto", cfg)

    assert pick(1) == "1m"  # 60 buckets
    assert pick(12) == "1m"  # 720 <= 1000
    assert pick(24) == "5m"  # 1m would be 1440 > 1000
    assert pick(24 * 3) == "5m"  # 864 buckets
    assert pick(24 * 5) == "15m"  # 5m would be 1440; 15m is 480
    assert pick(24 * 30) == "1h"  # 720 buckets


def test_auto_interval_respects_a_smaller_bucket_limit():
    small = Settings(_env_file=None, metrics_max_timeseries_buckets=40)
    window = m.Window(NOW - timedelta(days=30), NOW)
    assert m.choose_interval(window, "auto", small) == "1d"  # 30-31 daily buckets fit
    tiny = Settings(_env_file=None, metrics_max_timeseries_buckets=5)
    with pytest.raises(ResourceValidationError):
        m.choose_interval(window, "auto", tiny)  # even 1d does not fit


def test_explicit_interval_that_would_exceed_the_limit_is_rejected(cfg: Settings):
    window = m.Window(NOW - timedelta(days=2), NOW)
    with pytest.raises(ResourceValidationError) as error:
        m.choose_interval(window, "1m", cfg)
    assert error.value.field == "interval"
    assert m.choose_interval(window, "1h", cfg) == "1h"


# --- cursor -----------------------------------------------------------------------------


def test_cursor_round_trips_with_microsecond_precision():
    moment = datetime(2026, 3, 10, 12, 0, 0, 123456, tzinfo=UTC)
    row_id = uuid.uuid4()
    assert m.decode_cursor(m.encode_cursor(moment, row_id)) == (moment, row_id)


def _b64(payload: str) -> str:
    return base64.urlsafe_b64encode(payload.encode()).decode()


@pytest.mark.parametrize(
    "cursor",
    [
        "garbage!!",
        "",
        _b64("not json"),
        _b64(json.dumps({"t": "2026-03-10T12:00:00", "i": str(uuid.uuid4())})),  # naive
        _b64(json.dumps({"t": "nope", "i": str(uuid.uuid4())})),
        _b64(json.dumps({"t": "2026-03-10T12:00:00+00:00", "i": "not-a-uuid"})),
        _b64(json.dumps({"t": "2026-03-10T12:00:00+00:00"})),
        _b64(json.dumps([1, 2])),
    ],
)
def test_malformed_cursors_are_rejected(cursor: str):
    with pytest.raises(ResourceValidationError) as error:
        m.decode_cursor(cursor)
    assert (error.value.location, error.value.field) == ("query", "cursor")


# --- settings ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"metrics_default_page_size": 500, "metrics_max_page_size": 200},
        {"metrics_max_query_days": 60, "metrics_retention_days": 30},
        {"metrics_default_window_hours": 24 * 31, "metrics_max_query_days": 30},
        {"metrics_retention_days": 0},
        {"metrics_cleanup_batch_size": 0},
        {"metrics_cleanup_interval_hours": 0},
    ],
)
def test_contradictory_metrics_settings_are_rejected(overrides: dict):
    with pytest.raises(ValueError):
        Settings(_env_file=None, **overrides)


def test_metrics_defaults_are_consistent():
    s = Settings(_env_file=None)
    assert (s.metrics_retention_days, s.metrics_cleanup_interval_hours) == (30, 24.0)
    assert (s.metrics_cleanup_batch_size, s.metrics_max_query_days) == (1000, 30)
    assert s.metrics_max_query_days <= s.metrics_retention_days


# --- query shape ------------------------------------------------------------------------


def test_history_ordering_has_a_unique_tie_break_regardless_of_the_chosen_plan():
    from sqlalchemy.dialects import postgresql

    from app.repositories import check_results as repo

    stmt = repo.list_checks_stmt(
        uuid.uuid4(),
        start=utc(2026, 3, 10),
        end=utc(2026, 3, 11),
        status=None,
        limit=10,
        after=(utc(2026, 3, 10, 12), uuid.uuid4()),
    )
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    # The keyset cursor compares (checked_at, id), so the sort must be exactly that, newest first;
    # the index happens to provide it today, but correctness must not depend on the plan.
    assert "ORDER BY resource_check_results.checked_at DESC, resource_check_results.id DESC" in sql
    assert "(resource_check_results.checked_at, resource_check_results.id) <" in sql
