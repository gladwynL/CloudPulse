"""Top-level API router aggregating all route modules."""

from fastapi import APIRouter

from app.api.routes import health, resources

api_router = APIRouter()
# Probes stay at the root (/health, /ready); the product API is versioned.
api_router.include_router(health.router)
api_router.include_router(resources.router, prefix="/api/v1")
