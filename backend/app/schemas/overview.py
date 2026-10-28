"""Read-only system overview: totals the dashboard needs, computed in SQL.

Provided because no combination of the paginated list endpoints can give accurate system-wide
counts without fetching everything (and a client that counted only the first page would silently
under-report).
"""

from pydantic import BaseModel


class StatusCounts(BaseModel):
    UNKNOWN: int
    HEALTHY: int
    DEGRADED: int
    DOWN: int


class ResourceOverview(BaseModel):
    total: int
    enabled: int
    disabled: int
    # Over *all* resources, including disabled ones (a disabled resource keeps its last status).
    by_status: StatusCounts


class RuleOverview(BaseModel):
    total: int
    enabled: int
    firing: int


class IncidentOverview(BaseModel):
    open: int
    acknowledged: int
    # open + acknowledged
    active: int


class OverviewLimits(BaseModel):
    max_rules_per_resource: int


class OverviewResponse(BaseModel):
    resources: ResourceOverview
    alert_rules: RuleOverview
    incidents: IncidentOverview
    limits: OverviewLimits
