"""Enums persisted by the models (kept free of imports from the rest of the app)."""

import enum


class CheckSource(enum.StrEnum):
    SCHEDULED = "SCHEDULED"
    MANUAL = "MANUAL"


class ErrorCategory(enum.StrEnum):
    TIMEOUT = "timeout"
    DNS = "dns"
    CONNECTION = "connection"
    TLS = "tls"
    BLOCKED = "blocked"
    PROTOCOL = "protocol"
    UNEXPECTED_STATUS = "unexpected_status"
    INTERNAL = "internal"


class AlertRuleType(enum.StrEnum):
    CONSECUTIVE_FAILURES = "CONSECUTIVE_FAILURES"
    LATENCY_THRESHOLD = "LATENCY_THRESHOLD"
    STATUS = "STATUS"


class AlertOperator(enum.StrEnum):
    GREATER_THAN = "GREATER_THAN"
    GREATER_THAN_OR_EQUAL = "GREATER_THAN_OR_EQUAL"


class StatusCondition(enum.StrEnum):
    """Which observed status triggers a STATUS rule."""

    DOWN = "DOWN"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"  # DEGRADED or DOWN


class AlertSeverity(enum.StrEnum):
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class AlertState(enum.StrEnum):
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    OK = "OK"
    FIRING = "FIRING"


class IncidentStatus(enum.StrEnum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


class ResolutionReason(enum.StrEnum):
    RECOVERED = "RECOVERED"
    RULE_DISABLED = "RULE_DISABLED"
    RULE_DELETED = "RULE_DELETED"
    RULE_RECONFIGURED = "RULE_RECONFIGURED"
    RESOURCE_DISABLED = "RESOURCE_DISABLED"
    RESOURCE_DELETED = "RESOURCE_DELETED"


class IncidentEventType(enum.StrEnum):
    """Timeline entries. Each resolution reason has a matching event type."""

    OPENED = "OPENED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RECOVERED = "RECOVERED"
    RULE_DISABLED = "RULE_DISABLED"
    RULE_DELETED = "RULE_DELETED"
    RULE_RECONFIGURED = "RULE_RECONFIGURED"
    RESOURCE_DISABLED = "RESOURCE_DISABLED"
    RESOURCE_DELETED = "RESOURCE_DELETED"


class Actor(enum.StrEnum):
    """Who caused a transition. Phase 3 has no authentication, so MANUAL means "an API
    caller" and is **not** a verified identity."""

    SYSTEM = "SYSTEM"
    MANUAL = "MANUAL"
