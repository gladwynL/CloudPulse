"""monitored resources: configuration, latest health, and check leases

Adds the monitored_resources table. Only the latest observation is stored on the
row; historical measurements are introduced in a later phase.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-28 18:56:05.506847+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "monitored_resources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("check_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("expected_status_code", sa.Integer(), server_default="200", nullable=False),
        sa.Column(
            "current_status",
            sa.Enum(
                "UNKNOWN",
                "HEALTHY",
                "DEGRADED",
                "DOWN",
                name="resource_status",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            server_default="UNKNOWN",
            nullable=False,
        ),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_response_time_ms", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("last_http_status_code", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.String(length=255), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_owner", sa.String(length=100), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
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
            "char_length(btrim(name)) > 0", name=op.f("ck_monitored_resources_name_not_blank")
        ),
        sa.CheckConstraint(
            "check_interval_seconds > 0", name=op.f("ck_monitored_resources_interval_positive")
        ),
        sa.CheckConstraint(
            "expected_status_code BETWEEN 100 AND 599",
            name=op.f("ck_monitored_resources_expected_status_code_range"),
        ),
        sa.CheckConstraint(
            "timeout_seconds <= check_interval_seconds",
            name=op.f("ck_monitored_resources_timeout_within_interval"),
        ),
        sa.CheckConstraint(
            "timeout_seconds > 0", name=op.f("ck_monitored_resources_timeout_positive")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_monitored_resources")),
    )
    op.create_index(
        "ix_monitored_resources_created_at_id",
        "monitored_resources",
        ["created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_monitored_resources_due",
        "monitored_resources",
        ["next_check_at"],
        unique=False,
        postgresql_where=sa.text("is_enabled"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_monitored_resources_due",
        table_name="monitored_resources",
        postgresql_where=sa.text("is_enabled"),
    )
    op.drop_index("ix_monitored_resources_created_at_id", table_name="monitored_resources")
    op.drop_table("monitored_resources")
