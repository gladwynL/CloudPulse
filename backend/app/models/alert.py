"""Alert rules, incidents, and incident events.

* **Alert rule**: a configured condition on one resource, plus its persisted evaluation progress.
* **Alert state** (``AlertRule.current_state``): whether that condition is firing right now.
* **Incident**: a durable record of a detected problem and its lifecycle.

Retention of history when things are deleted:

* Deleting a *resource* cascades to its rules, but incidents are **kept**: ``incidents.resource_id``
  and ``incidents.alert_rule_id`` become NULL and the incident keeps snapshots of the resource and
  rule names, so history stays readable. (The application first closes any active incident with an
  explicit resolution reason inside the same transaction.)
* Deleting or reconfiguring a *rule* never rewrites past incidents.
* ``incident_events`` belong to their incident and cascade only if the incident itself is removed
  (nothing removes incidents in Phase 3).

Timestamps are timezone-aware UTC. Within the check-completion transaction they use ``now()`` (the
same instant as the measurement that caused them); transitions caused by API calls use
``clock_timestamp()`` so a transaction that waited on a lock never records a time earlier than the
commit it waited for.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar

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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import (
    Actor,
    AlertOperator,
    AlertRuleType,
    AlertSeverity,
    AlertState,
    IncidentEventType,
    IncidentStatus,
    ResolutionReason,
    StatusCondition,
)


def _enum(enum_cls: type, name: str, length: int) -> Enum:
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        length=length,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda members: [m.name for m in members],
    )


class AlertRule(Base):
    __tablename__ = "alert_rules"
    __mapper_args__: ClassVar[dict[str, Any]] = {"eager_defaults": True}
    __table_args__ = (
        CheckConstraint("char_length(btrim(name)) > 0", name="name_not_blank"),
        CheckConstraint("recovery_successes BETWEEN 1 AND 20", name="recovery_successes_range"),
        CheckConstraint(
            "consecutive_failures IS NULL OR consecutive_failures BETWEEN 1 AND 20",
            name="consecutive_failures_range",
        ),
        CheckConstraint("threshold IS NULL OR threshold > 0", name="threshold_positive"),
        CheckConstraint(
            "failure_streak >= 0 AND recovery_streak >= 0", name="streaks_non_negative"
        ),
        # Each rule type has exactly the configuration it needs and nothing irrelevant.
        CheckConstraint(
            "(rule_type = 'CONSECUTIVE_FAILURES' AND consecutive_failures IS NOT NULL"
            " AND operator IS NULL AND threshold IS NULL AND status_condition IS NULL)"
            " OR (rule_type = 'LATENCY_THRESHOLD' AND consecutive_failures IS NOT NULL"
            " AND operator IS NOT NULL AND threshold IS NOT NULL AND status_condition IS NULL)"
            " OR (rule_type = 'STATUS' AND status_condition IS NOT NULL"
            " AND consecutive_failures IS NULL AND operator IS NULL AND threshold IS NULL)",
            name="config_matches_rule_type",
        ),
        # A disabled rule is never firing and carries no evaluation progress.
        CheckConstraint(
            "is_enabled OR (current_state = 'INSUFFICIENT_DATA'"
            " AND failure_streak = 0 AND recovery_streak = 0)",
            name="disabled_rule_is_reset",
        ),
        CheckConstraint(
            "current_state <> 'FIRING' OR last_triggered_at IS NOT NULL",
            name="firing_has_trigger_time",
        ),
        # Rule lookup on every accepted check: enabled rules of one resource.
        Index(
            "ix_alert_rules_resource_enabled",
            "resource_id",
            postgresql_where=text("is_enabled"),
        ),
        # Listing a resource's rules in deterministic order.
        Index("ix_alert_rules_resource_created_at_id", "resource_id", "created_at", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    resource_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("monitored_resources.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(500))
    is_enabled: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    rule_type: Mapped[AlertRuleType] = mapped_column(_enum(AlertRuleType, "alert_rule_type", 32))
    operator: Mapped[AlertOperator | None] = mapped_column(
        _enum(AlertOperator, "alert_rule_operator", 32)
    )
    threshold: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    status_condition: Mapped[StatusCondition | None] = mapped_column(
        _enum(StatusCondition, "alert_rule_status_condition", 16)
    )
    # Trigger streak length (CONSECUTIVE_FAILURES, LATENCY_THRESHOLD); NULL for STATUS (immediate).
    consecutive_failures: Mapped[int | None] = mapped_column(Integer)
    recovery_successes: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    severity: Mapped[AlertSeverity] = mapped_column(
        _enum(AlertSeverity, "alert_rule_severity", 16),
        default=AlertSeverity.WARNING,
        server_default=AlertSeverity.WARNING.name,
    )

    # Persisted evaluation progress: survives restarts; only accepted observations advance it.
    current_state: Mapped[AlertState] = mapped_column(
        _enum(AlertState, "alert_rule_state", 24),
        default=AlertState.INSUFFICIENT_DATA,
        server_default=AlertState.INSUFFICIENT_DATA.name,
    )
    failure_streak: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    recovery_streak: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # The last accepted measurement this rule evaluated (no FK: history is subject to retention).
    last_evaluated_check_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    last_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Incident(Base):
    __tablename__ = "incidents"
    __mapper_args__: ClassVar[dict[str, Any]] = {"eager_defaults": True}
    __table_args__ = (
        CheckConstraint("char_length(btrim(title)) > 0", name="title_not_blank"),
        CheckConstraint(
            "(status = 'OPEN' AND acknowledged_at IS NULL AND resolved_at IS NULL"
            " AND resolution_reason IS NULL)"
            " OR (status = 'ACKNOWLEDGED' AND acknowledged_at IS NOT NULL AND resolved_at IS NULL"
            " AND resolution_reason IS NULL)"
            " OR (status = 'RESOLVED' AND resolved_at IS NOT NULL"
            " AND resolution_reason IS NOT NULL)",
            name="status_matches_timestamps",
        ),
        CheckConstraint(
            "resolved_at IS NULL OR resolved_at >= triggered_at", name="resolved_after_triggered"
        ),
        CheckConstraint(
            "acknowledged_at IS NULL OR acknowledged_at >= triggered_at",
            name="acknowledged_after_triggered",
        ),
        # At most one active incident per rule, enforced by the database (the concurrency backstop
        # behind the resource-row lock the application takes).
        Index(
            "uq_incidents_one_active_per_rule",
            "alert_rule_id",
            unique=True,
            postgresql_where=text("status IN ('OPEN', 'ACKNOWLEDGED')"),
        ),
        # Newest first listing, with a stable tie-break (keyset pagination).
        Index("ix_incidents_triggered_at_id", desc("triggered_at"), desc("id")),
        Index(
            "ix_incidents_resource_triggered_at_id", "resource_id", desc("triggered_at"), desc("id")
        ),
        # "What is open right now?"
        Index(
            "ix_incidents_active_triggered_at_id",
            desc("triggered_at"),
            desc("id"),
            postgresql_where=text("status <> 'RESOLVED'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("monitored_resources.id", ondelete="SET NULL")
    )
    alert_rule_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("alert_rules.id", ondelete="SET NULL")
    )
    # Snapshots keep history readable after the resource or rule is renamed, reconfigured or
    # deleted. (The target URL is deliberately not copied.)
    resource_name: Mapped[str] = mapped_column(String(100))
    rule_name: Mapped[str] = mapped_column(String(100))
    rule_type: Mapped[AlertRuleType] = mapped_column(_enum(AlertRuleType, "incident_rule_type", 32))
    title: Mapped[str] = mapped_column(String(255))
    severity: Mapped[AlertSeverity] = mapped_column(_enum(AlertSeverity, "incident_severity", 16))
    status: Mapped[IncidentStatus] = mapped_column(_enum(IncidentStatus, "incident_status", 16))
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_reason: Mapped[ResolutionReason | None] = mapped_column(
        _enum(ResolutionReason, "incident_resolution_reason", 24)
    )
    # Most recent accepted observation that matched the rule while the incident was active.
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # The accepted measurement that opened the incident (nulled if retention later removes it).
    opened_by_check_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("resource_check_results.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IncidentEvent(Base):
    """Append-only timeline entry, written in the same transaction as the transition."""

    __tablename__ = "incident_events"
    __table_args__ = (
        Index("ix_incident_events_incident_occurred_at_id", "incident_id", "occurred_at", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("incidents.id", ondelete="CASCADE")
    )
    event_type: Mapped[IncidentEventType] = mapped_column(
        _enum(IncidentEventType, "incident_event_type", 24)
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor: Mapped[Actor] = mapped_column(_enum(Actor, "incident_event_actor", 16))
    # Small, safe structured context (ids, counts, status). Never URLs or raw error text.
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
