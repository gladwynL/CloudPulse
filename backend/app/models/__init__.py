"""ORM models.

Import every model module here so ``Base.metadata`` is fully populated for Alembic
autogenerate.
"""

from app.db.base import Base
from app.models.check_result import ResourceCheckResult
from app.models.resource import MonitoredResource, ResourceStatus

__all__ = ["Base", "MonitoredResource", "ResourceCheckResult", "ResourceStatus"]
