"""History, summary, and time-series rules: windows, limits, intervals, and cursors.

Semantics (documented in the README):

* Windows are UTC and half-open ``[start, end)``. Naive timestamps are interpreted as UTC.
  Defaults: ``end`` = now, ``start`` = ``end - METRICS_DEFAULT_WINDOW_HOURS``.
* Availability is **check-based**: ``healthy / total * 100`` over the checks that happened in the
  window. DEGRADED does not count as healthy. It says nothing about time between checks, and is
  null (not 0 or 100) when there are no checks.
* Latency statistics use checks that received an HTTP response (HEALTHY and DEGRADED);
  DOWN checks have no response time and are excluded.
"""

import base64
import binascii
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import NamedTuple

from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.check_result import ResourceCheckResult
from app.models.resource import ResourceStatus
from app.repositories import check_results as repo
from app.services.resources import ResourceNotFoundError, ResourceValidationError

INTERVALS: dict[str, timedelta] = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "1d": timedelta(days=1),
}
# Smallest first: "auto" picks the finest interval that fits the bucket limit.
_AUTO_ORDER = ("1m", "5m", "15m", "1h", "1d")


def _invalid(field: str, message: str) -> ResourceValidationError:
    return ResourceValidationError(field, message, location="query")


@dataclass(frozen=True, slots=True)
class Window:
    start: datetime
    end: datetime


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def resolve_window(
    start: datetime | None,
    end: datetime | None,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> Window:
    end_utc = _as_utc(end) if end is not None else (now or datetime.now(UTC))
    start_utc = (
        _as_utc(start)
        if start is not None
        else end_utc - timedelta(hours=settings.metrics_default_window_hours)
    )
    if start_utc >= end_utc:
        raise _invalid("start", "start must be earlier than end")
    max_span = timedelta(days=settings.metrics_max_query_days)
    if end_utc - start_utc > max_span:
        raise _invalid("start", f"window must not exceed {settings.metrics_max_query_days} days")
    return Window(start_utc, end_utc)


def availability_percent(healthy: int, total: int) -> float | None:
    """healthy / total * 100, rounded half-up to two decimals; None when total is 0."""
    if total == 0:
        return None
    value = Decimal(healthy) * 100 / Decimal(total)
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _f(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _require_resource(session: Session, resource_id: uuid.UUID) -> None:
    if not repo.resource_exists(session, resource_id):
        raise ResourceNotFoundError


# --- Cursor (keyset pagination) ---------------------------------------------------------


def encode_cursor(checked_at: datetime, row_id: uuid.UUID) -> str:
    payload = json.dumps({"t": checked_at.astimezone(UTC).isoformat(), "i": str(row_id)})
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        data = json.loads(raw)
        moment = datetime.fromisoformat(data["t"])
        if moment.tzinfo is None:
            raise ValueError("naive timestamp")
        return moment.astimezone(UTC), uuid.UUID(data["i"])
    except (binascii.Error, ValueError, KeyError, TypeError, UnicodeDecodeError):
        raise _invalid("cursor", "cursor is invalid") from None


# --- History ----------------------------------------------------------------------------


class HistoryPage(NamedTuple):
    window: Window
    items: list[ResourceCheckResult]
    limit: int
    next_cursor: str | None


def get_history(
    session: Session,
    resource_id: uuid.UUID,
    settings: Settings,
    *,
    start: datetime | None,
    end: datetime | None,
    status: ResourceStatus | None,
    limit: int | None,
    cursor: str | None,
) -> HistoryPage:
    # Pure input validation first (no database), then existence (404).
    window = resolve_window(start, end, settings)
    page_size = limit if limit is not None else settings.metrics_default_page_size
    if not 1 <= page_size <= settings.metrics_max_page_size:
        raise _invalid("limit", f"must be between 1 and {settings.metrics_max_page_size}")
    after = decode_cursor(cursor) if cursor else None
    _require_resource(session, resource_id)

    rows = repo.list_checks(
        session,
        resource_id,
        start=window.start,
        end=window.end,
        status=status,
        limit=page_size + 1,
        after=after,
    )
    has_more = len(rows) > page_size
    items = rows[:page_size]
    next_cursor = encode_cursor(items[-1].checked_at, items[-1].id) if has_more else None
    return HistoryPage(window, items, page_size, next_cursor)


# --- Summary ----------------------------------------------------------------------------


class Summary(NamedTuple):
    window: Window
    row: Row
    availability_percent: float | None


def get_summary(
    session: Session,
    resource_id: uuid.UUID,
    settings: Settings,
    *,
    start: datetime | None,
    end: datetime | None,
) -> Summary:
    window = resolve_window(start, end, settings)
    _require_resource(session, resource_id)
    row = repo.summary(session, resource_id, window.start, window.end)
    return Summary(window, row, availability_percent(row.healthy, row.total))


# --- Time series ------------------------------------------------------------------------


def bucket_count(window: Window, interval: timedelta) -> int:
    """Number of aligned buckets the window can touch (an upper bound on the response size)."""
    origin = repo.BUCKET_ORIGIN
    step = interval // timedelta(microseconds=1)
    first = (window.start - origin) // timedelta(microseconds=1) // step
    # ``end`` is exclusive, so the last instant in the window is one microsecond earlier.
    last = ((window.end - origin) // timedelta(microseconds=1) - 1) // step
    return int(last - first + 1)


def choose_interval(window: Window, requested: str, settings: Settings) -> str:
    limit = settings.metrics_max_timeseries_buckets
    if requested != "auto":
        count = bucket_count(window, INTERVALS[requested])
        if count > limit:
            raise _invalid(
                "interval",
                f"{requested} would produce up to {count} buckets for this window "
                f"(maximum {limit}); use a coarser interval or a shorter window",
            )
        return requested
    for name in _AUTO_ORDER:
        if bucket_count(window, INTERVALS[name]) <= limit:
            return name
    raise _invalid(
        "start", f"window is too large for {limit} buckets even at the coarsest interval (1d)"
    )


class Series(NamedTuple):
    window: Window
    interval: str
    rows: list[Row]


def get_timeseries(
    session: Session,
    resource_id: uuid.UUID,
    settings: Settings,
    *,
    start: datetime | None,
    end: datetime | None,
    interval: str,
) -> Series:
    window = resolve_window(start, end, settings)
    chosen = choose_interval(window, interval, settings)
    _require_resource(session, resource_id)
    rows = repo.timeseries(session, resource_id, window.start, window.end, INTERVALS[chosen])
    return Series(window, chosen, rows)


def to_float(value: Decimal | None) -> float | None:
    return _f(value)
