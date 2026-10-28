"""resource check results: durable history of accepted checks

Adds resource_check_results (one row per accepted check). Purely additive: existing
monitored_resources rows and the Phase 1 schema are untouched, and no data is deleted
(retention runs at worker runtime, never in a migration). Rows cascade-delete with their
resource.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-28 19:47:03.912722+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "resource_check_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "UNKNOWN",
                "HEALTHY",
                "DEGRADED",
                "DOWN",
                name="check_result_status",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("http_status_code", sa.Integer(), nullable=True),
        sa.Column("response_time_ms", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("duration_ms", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column(
            "error_category",
            sa.Enum(
                "TIMEOUT",
                "DNS",
                "CONNECTION",
                "TLS",
                "BLOCKED",
                "PROTOCOL",
                "UNEXPECTED_STATUS",
                "INTERNAL",
                name="check_result_error_category",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=True,
        ),
        sa.Column("error_summary", sa.String(length=255), nullable=True),
        sa.Column(
            "source",
            sa.Enum(
                "SCHEDULED",
                "MANUAL",
                name="check_result_source",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status <> 'UNKNOWN'", name=op.f("ck_resource_check_results_status_is_a_result")
        ),
        sa.CheckConstraint(
            "duration_ms >= 0", name=op.f("ck_resource_check_results_duration_non_negative")
        ),
        sa.CheckConstraint(
            "http_status_code IS NULL OR http_status_code BETWEEN 100 AND 599",
            name=op.f("ck_resource_check_results_http_status_code_range"),
        ),
        sa.CheckConstraint(
            "response_time_ms IS NULL OR response_time_ms >= 0",
            name=op.f("ck_resource_check_results_response_time_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["resource_id"],
            ["monitored_resources.id"],
            name=op.f("fk_resource_check_results_resource_id_monitored_resources"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resource_check_results")),
    )
    op.create_index(
        "ix_resource_check_results_checked_at",
        "resource_check_results",
        ["checked_at"],
        unique=False,
    )
    op.create_index(
        "ix_resource_check_results_resource_checked_at_id",
        "resource_check_results",
        ["resource_id", sa.literal_column("checked_at DESC"), sa.literal_column("id DESC")],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_resource_check_results_resource_checked_at_id", table_name="resource_check_results"
    )
    op.drop_index("ix_resource_check_results_checked_at", table_name="resource_check_results")
    op.drop_table("resource_check_results")
