"""``GET /api/v1/overview``: aggregate counts for dashboards (read-only)."""

from fastapi import APIRouter

from app.api.deps import AppSettings, DbSession, reject_unknown_query_params
from app.repositories import overview as repo
from app.schemas.overview import (
    IncidentOverview,
    OverviewLimits,
    OverviewResponse,
    ResourceOverview,
    RuleOverview,
    StatusCounts,
)

router = APIRouter(tags=["overview"])


@router.get(
    "/overview",
    response_model=OverviewResponse,
    dependencies=[reject_unknown_query_params()],
)
def get_overview(session: DbSession, settings: AppSettings) -> OverviewResponse:
    """System-wide totals computed in SQL: resources by status, alert rules (enabled/firing),
    active incidents, and the configured per-resource rule limit."""
    resources = repo.resource_counts(session)
    rules = repo.rule_counts(session)
    incidents = repo.incident_counts(session)
    return OverviewResponse(
        resources=ResourceOverview(
            total=resources.total,
            enabled=resources.enabled,
            disabled=resources.total - resources.enabled,
            by_status=StatusCounts(
                UNKNOWN=resources.UNKNOWN,
                HEALTHY=resources.HEALTHY,
                DEGRADED=resources.DEGRADED,
                DOWN=resources.DOWN,
            ),
        ),
        alert_rules=RuleOverview(total=rules.total, enabled=rules.enabled, firing=rules.firing),
        incidents=IncidentOverview(
            open=incidents.open,
            acknowledged=incidents.acknowledged,
            active=incidents.open + incidents.acknowledged,
        ),
        limits=OverviewLimits(max_rules_per_resource=settings.alerting_max_rules_per_resource),
    )
