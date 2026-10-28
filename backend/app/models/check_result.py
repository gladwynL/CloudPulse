"""Historical check results: one immutable row per accepted check.

Time and duration semantics (all timestamps are timezone-aware UTC, taken from the
database clock so every process agrees):

* ``checked_at``       when the check result was **accepted** (the transaction timestamp of
                       the write that also updates the resource's latest state, so it equals
                       ``monitored_resources.last_checked_at`` for that check). This is the
                       measurement event time used for every query and bucket.
* ``created_at``       when the row was physically inserted (``clock_timestamp()``); useful for
                       auditing, never for charts.
* ``response_time_ms`` HTTP latency: time until response headers arrived. NULL when no response
                       arrived (DOWN).
* ``duration_ms``      wall time the monitoring execution took, including failures and timeouts.

Numbers are stored with two decimals (``NUMERIC(10, 2)``); values are rounded once, in
Python, before insert. Only sanitized text is stored: a fixed category and summary, never raw
exception messages, response bodies, URLs, or credentials.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Uuid,
    desc,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import CheckSource, ErrorCategory
from app.models.resource import ResourceStatus


def _enum(enum_cls: type, name: str, length: int) -> Enum:
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        length=length,
        create_constraint=True,
        validate_strings=True,
        # Persist member *names* (HEALTHY, SCHEDULED, TIMEOUT...) for all three enums.
        values_callable=lambda members: [m.name for m in members],
    )


class ResourceCheckResult(Base):
    __tablename__ = "resource_check_results"
    __table_args__ = (
        CheckConstraint("status <> 'UNKNOWN'", name="status_is_a_result"),
        CheckConstraint(
            "http_status_code IS NULL OR http_status_code BETWEEN 100 AND 599",
            name="http_status_code_range",
        ),
        CheckConstraint(
            "response_time_ms IS NULL OR response_time_ms >= 0", name="response_time_non_negative"
        ),
        CheckConstraint("duration_ms >= 0", name="duration_non_negative"),
        # Primary access path: one resource, newest first, stable tie-break. Serves history
        # pages (keyset pagination), summary and time-series aggregates.
        Index(
            "ix_resource_check_results_resource_checked_at_id",
            "resource_id",
            desc("checked_at"),
            desc("id"),
        ),
        # Retention deletes by age across all resources.
        Index("ix_resource_check_results_checked_at", "checked_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # Deleting a resource deletes its history (enforced by the database, not by the ORM).
    resource_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("monitored_resources.id", ondelete="CASCADE")
    )
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[ResourceStatus] = mapped_column(_enum(ResourceStatus, "check_result_status", 16))
    http_status_code: Mapped[int | None] = mapped_column(Integer)
    response_time_ms: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    duration_ms: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    error_category: Mapped[ErrorCategory | None] = mapped_column(
        _enum(ErrorCategory, "check_result_error_category", 32)
    )
    error_summary: Mapped[str | None] = mapped_column(String(255))
    source: Mapped[CheckSource] = mapped_column(_enum(CheckSource, "check_result_source", 16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
