"""Plain data types shared by the checker, executor, scheduler, and store."""

import enum
import uuid
from dataclasses import dataclass

from app.models.resource import ResourceStatus


class CheckSource(enum.StrEnum):
    SCHEDULED = "scheduled"
    MANUAL = "manual"


class ErrorCategory(enum.StrEnum):
    TIMEOUT = "timeout"
    DNS = "dns"
    CONNECTION = "connection"
    TLS = "tls"
    BLOCKED = "blocked"
    PROTOCOL = "protocol"
    UNEXPECTED_STATUS = "unexpected_status"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class CheckTarget:
    url: str
    timeout_seconds: int
    expected_status_code: int


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Outcome of one check.

    This is the unit a later phase will append to a measurement history; Phase 1 only
    persists the latest one on the resource row.
    """

    status: ResourceStatus
    http_status_code: int | None
    # Time until response headers were received; None when no response arrived.
    response_time_ms: float | None
    # Sanitized summary, e.g. "timeout: no response within 5s". Never contains the URL.
    error: str | None
    error_category: ErrorCategory | None
    # Wall time spent executing the check, including failures.
    duration_ms: float


@dataclass(frozen=True, slots=True)
class ClaimedCheck:
    """A resource this executor holds the lease for."""

    resource_id: uuid.UUID
    target: CheckTarget
    lease_token: uuid.UUID
    owner: str


class ClaimOutcome(enum.StrEnum):
    """Why a by-id claim did not produce a lease."""

    NOT_FOUND = "not_found"
    DISABLED = "disabled"
    BUSY = "busy"
