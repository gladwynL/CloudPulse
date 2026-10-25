"""Liveness (``/health``) and readiness (``/ready``) probes."""

from fastapi import APIRouter, Response, status

from app.api.deps import DbSession
from app.schemas.health import HealthResponse, ReadinessChecks, ReadinessResponse
from app.services.health import check_database

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
def health() -> HealthResponse:
    """Report that the API process is running. Does not touch external dependencies."""
    return HealthResponse()


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
def ready(session: DbSession, response: Response) -> ReadinessResponse:
    """Report whether the API can serve traffic, i.e. PostgreSQL is reachable."""
    if check_database(session):
        return ReadinessResponse(status="ready", checks=ReadinessChecks(database="ok"))
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(status="unavailable", checks=ReadinessChecks(database="unavailable"))
