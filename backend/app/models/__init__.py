"""ORM models.

Import every model module here so ``Base.metadata`` is fully populated for Alembic
autogenerate. No domain models exist yet (Phase 0).
"""

from app.db.base import Base

__all__ = ["Base"]
