"""baseline: empty schema

Establishes the migration history root. Creates no tables; CloudPulse domain
tables are introduced in later phases.

Revision ID: 0001
Revises:
Create Date: 2026-10-25 00:00:00+00:00

"""

from collections.abc import Sequence

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
