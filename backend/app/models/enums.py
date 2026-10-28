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
