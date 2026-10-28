"""Monitored resource: configuration plus the latest observed health."""

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    Index,
    Integer,
    Numeric,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ResourceStatus(enum.StrEnum):
    """Health state of a resource, as of its most recent completed check.

    UNKNOWN   no completed check (new, or configuration changed since the last check)
    HEALTHY   request completed with the expected HTTP status
    DEGRADED  request completed but returned an unexpected HTTP status
    DOWN      the check could not complete (timeout, DNS, connection, TLS, blocked target)
    """

    UNKNOWN = "UNKNOWN"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"


class MonitoredResource(Base):
    __tablename__ = "monitored_resources"
    # Fetch server-generated values (now(), defaults) via RETURNING instead of re-selecting.
    __mapper_args__: ClassVar[dict[str, Any]] = {"eager_defaults": True}
    __table_args__ = (
        CheckConstraint("char_length(btrim(name)) > 0", name="name_not_blank"),
        CheckConstraint("check_interval_seconds > 0", name="interval_positive"),
        CheckConstraint("timeout_seconds > 0", name="timeout_positive"),
        CheckConstraint(
            "timeout_seconds <= check_interval_seconds", name="timeout_within_interval"
        ),
        CheckConstraint(
            "expected_status_code BETWEEN 100 AND 599", name="expected_status_code_range"
        ),
        # Serves the scheduler's "what is due?" scan; disabled rows never appear in it.
        Index(
            "ix_monitored_resources_due",
            "next_check_at",
            postgresql_where=text("is_enabled"),
        ),
        # Deterministic listing order.
        Index("ix_monitored_resources_created_at_id", "created_at", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(500))
    url: Mapped[str] = mapped_column(String(2048))
    is_enabled: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    check_interval_seconds: Mapped[int] = mapped_column(Integer)
    timeout_seconds: Mapped[int] = mapped_column(Integer)
    expected_status_code: Mapped[int] = mapped_column(Integer, default=200, server_default="200")

    # Latest observation only. Historical measurements are a later phase.
    current_status: Mapped[ResourceStatus] = mapped_column(
        Enum(
            ResourceStatus,
            name="resource_status",
            native_enum=False,
            length=16,
            create_constraint=True,
            validate_strings=True,
        ),
        default=ResourceStatus.UNKNOWN,
        server_default=ResourceStatus.UNKNOWN.value,
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_response_time_ms: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    last_http_status_code: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(String(255))

    # Lease: database-backed claim so only one executor works on a resource at a time.
    # The token identifies the claim; results are written only by the current token holder.
    lease_token: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    lease_owner: Mapped[str | None] = mapped_column(String(100))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
