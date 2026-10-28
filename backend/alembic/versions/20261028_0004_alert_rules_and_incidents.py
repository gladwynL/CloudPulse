"""alert rules, incidents and incident events

Adds alert_rules (with persisted evaluation progress), incidents (a durable record that survives
the deletion of its rule or resource via nullable FKs plus name snapshots; one active incident per
rule is enforced by a partial unique index) and incident_events (append-only timeline).
Purely additive: no existing rows are modified or deleted.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-28 20:39:04.462070+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "alert_rules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "rule_type",
            sa.Enum(
                "CONSECUTIVE_FAILURES",
                "LATENCY_THRESHOLD",
                "STATUS",
                name="alert_rule_type",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "operator",
            sa.Enum(
                "GREATER_THAN",
                "GREATER_THAN_OR_EQUAL",
                name="alert_rule_operator",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=True,
        ),
        sa.Column("threshold", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column(
            "status_condition",
            sa.Enum(
                "DOWN",
                "DEGRADED",
                "UNHEALTHY",
                name="alert_rule_status_condition",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=True,
        ),
        sa.Column("consecutive_failures", sa.Integer(), nullable=True),
        sa.Column("recovery_successes", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "severity",
            sa.Enum(
                "WARNING",
                "CRITICAL",
                name="alert_rule_severity",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            server_default="WARNING",
            nullable=False,
        ),
        sa.Column(
            "current_state",
            sa.Enum(
                "INSUFFICIENT_DATA",
                "OK",
                "FIRING",
                name="alert_rule_state",
                native_enum=False,
                create_constraint=True,
                length=24,
            ),
            server_default="INSUFFICIENT_DATA",
            nullable=False,
        ),
        sa.Column("failure_streak", sa.Integer(), server_default="0", nullable=False),
        sa.Column("recovery_streak", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_evaluated_check_id", sa.Uuid(), nullable=True),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(rule_type = 'CONSECUTIVE_FAILURES' AND consecutive_failures IS NOT NULL AND operator IS NULL AND threshold IS NULL AND status_condition IS NULL) OR (rule_type = 'LATENCY_THRESHOLD' AND consecutive_failures IS NOT NULL AND operator IS NOT NULL AND threshold IS NOT NULL AND status_condition IS NULL) OR (rule_type = 'STATUS' AND status_condition IS NOT NULL AND consecutive_failures IS NULL AND operator IS NULL AND threshold IS NULL)",
            name=op.f("ck_alert_rules_config_matches_rule_type"),
        ),
        sa.CheckConstraint(
            "current_state <> 'FIRING' OR last_triggered_at IS NOT NULL",
            name=op.f("ck_alert_rules_firing_has_trigger_time"),
        ),
        sa.CheckConstraint(
            "is_enabled OR (current_state = 'INSUFFICIENT_DATA' AND failure_streak = 0 AND recovery_streak = 0)",
            name=op.f("ck_alert_rules_disabled_rule_is_reset"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(name)) > 0", name=op.f("ck_alert_rules_name_not_blank")
        ),
        sa.CheckConstraint(
            "consecutive_failures IS NULL OR consecutive_failures BETWEEN 1 AND 20",
            name=op.f("ck_alert_rules_consecutive_failures_range"),
        ),
        sa.CheckConstraint(
            "failure_streak >= 0 AND recovery_streak >= 0",
            name=op.f("ck_alert_rules_streaks_non_negative"),
        ),
        sa.CheckConstraint(
            "recovery_successes BETWEEN 1 AND 20",
            name=op.f("ck_alert_rules_recovery_successes_range"),
        ),
        sa.CheckConstraint(
            "threshold IS NULL OR threshold > 0", name=op.f("ck_alert_rules_threshold_positive")
        ),
        sa.ForeignKeyConstraint(
            ["resource_id"],
            ["monitored_resources.id"],
            name=op.f("fk_alert_rules_resource_id_monitored_resources"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_rules")),
    )
    op.create_index(
        "ix_alert_rules_resource_created_at_id",
        "alert_rules",
        ["resource_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_alert_rules_resource_enabled",
        "alert_rules",
        ["resource_id"],
        unique=False,
        postgresql_where=sa.text("is_enabled"),
    )
    op.create_table(
        "incidents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=True),
        sa.Column("alert_rule_id", sa.Uuid(), nullable=True),
        sa.Column("resource_name", sa.String(length=100), nullable=False),
        sa.Column("rule_name", sa.String(length=100), nullable=False),
        sa.Column(
            "rule_type",
            sa.Enum(
                "CONSECUTIVE_FAILURES",
                "LATENCY_THRESHOLD",
                "STATUS",
                name="incident_rule_type",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column(
            "severity",
            sa.Enum(
                "WARNING",
                "CRITICAL",
                name="incident_severity",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "OPEN",
                "ACKNOWLEDGED",
                "RESOLVED",
                name="incident_status",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "resolution_reason",
            sa.Enum(
                "RECOVERED",
                "RULE_DISABLED",
                "RULE_DELETED",
                "RULE_RECONFIGURED",
                "RESOURCE_DISABLED",
                "RESOURCE_DELETED",
                name="incident_resolution_reason",
                native_enum=False,
                create_constraint=True,
                length=24,
            ),
            nullable=True,
        ),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("opened_by_check_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'OPEN' AND acknowledged_at IS NULL AND resolved_at IS NULL AND resolution_reason IS NULL) OR (status = 'ACKNOWLEDGED' AND acknowledged_at IS NOT NULL AND resolved_at IS NULL AND resolution_reason IS NULL) OR (status = 'RESOLVED' AND resolved_at IS NOT NULL AND resolution_reason IS NOT NULL)",
            name=op.f("ck_incidents_status_matches_timestamps"),
        ),
        sa.CheckConstraint(
            "acknowledged_at IS NULL OR acknowledged_at >= triggered_at",
            name=op.f("ck_incidents_acknowledged_after_triggered"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(title)) > 0", name=op.f("ck_incidents_title_not_blank")
        ),
        sa.CheckConstraint(
            "resolved_at IS NULL OR resolved_at >= triggered_at",
            name=op.f("ck_incidents_resolved_after_triggered"),
        ),
        sa.ForeignKeyConstraint(
            ["alert_rule_id"],
            ["alert_rules.id"],
            name=op.f("fk_incidents_alert_rule_id_alert_rules"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["opened_by_check_id"],
            ["resource_check_results.id"],
            name=op.f("fk_incidents_opened_by_check_id_resource_check_results"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["resource_id"],
            ["monitored_resources.id"],
            name=op.f("fk_incidents_resource_id_monitored_resources"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incidents")),
    )
    op.create_index(
        "ix_incidents_active_triggered_at_id",
        "incidents",
        [sa.literal_column("triggered_at DESC"), sa.literal_column("id DESC")],
        unique=False,
        postgresql_where=sa.text("status <> 'RESOLVED'"),
    )
    op.create_index(
        "ix_incidents_resource_triggered_at_id",
        "incidents",
        ["resource_id", sa.literal_column("triggered_at DESC"), sa.literal_column("id DESC")],
        unique=False,
    )
    op.create_index(
        "ix_incidents_triggered_at_id",
        "incidents",
        [sa.literal_column("triggered_at DESC"), sa.literal_column("id DESC")],
        unique=False,
    )
    op.create_index(
        "uq_incidents_one_active_per_rule",
        "incidents",
        ["alert_rule_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('OPEN', 'ACKNOWLEDGED')"),
    )
    op.create_table(
        "incident_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column(
            "event_type",
            sa.Enum(
                "OPENED",
                "ACKNOWLEDGED",
                "RECOVERED",
                "RULE_DISABLED",
                "RULE_DELETED",
                "RULE_RECONFIGURED",
                "RESOURCE_DISABLED",
                "RESOURCE_DELETED",
                name="incident_event_type",
                native_enum=False,
                create_constraint=True,
                length=24,
            ),
            nullable=False,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "actor",
            sa.Enum(
                "SYSTEM",
                "MANUAL",
                name="incident_event_actor",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_incident_events_incident_id_incidents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incident_events")),
    )
    op.create_index(
        "ix_incident_events_incident_occurred_at_id",
        "incident_events",
        ["incident_id", "occurred_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_incident_events_incident_occurred_at_id", table_name="incident_events")
    op.drop_table("incident_events")
    op.drop_index(
        "uq_incidents_one_active_per_rule",
        table_name="incidents",
        postgresql_where=sa.text("status IN ('OPEN', 'ACKNOWLEDGED')"),
    )
    op.drop_index("ix_incidents_triggered_at_id", table_name="incidents")
    op.drop_index("ix_incidents_resource_triggered_at_id", table_name="incidents")
    op.drop_index(
        "ix_incidents_active_triggered_at_id",
        table_name="incidents",
        postgresql_where=sa.text("status <> 'RESOLVED'"),
    )
    op.drop_table("incidents")
    op.drop_index(
        "ix_alert_rules_resource_enabled",
        table_name="alert_rules",
        postgresql_where=sa.text("is_enabled"),
    )
    op.drop_index("ix_alert_rules_resource_created_at_id", table_name="alert_rules")
    op.drop_table("alert_rules")
