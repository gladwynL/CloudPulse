"""FastAPI application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_error_handlers
from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.middleware import register_request_logging
from app.monitoring.checker import HttpChecker
from app.monitoring.security import TargetPolicy

logger = logging.getLogger("cloudpulse")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a configured FastAPI application. Pass ``settings`` to override for tests."""
    settings = settings or get_settings()
    configure_logging(
        level=settings.log_level,
        fmt=settings.log_format,
        environment=settings.environment,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "application startup",
            extra={"version": settings.app_version, "environment": settings.environment},
        )
        # Outbound client for manual checks. Scheduled checks run in the worker process.
        app.state.checker = HttpChecker(
            app.state.policy, max_connections=settings.monitoring_max_concurrent_checks
        )
        yield
        await app.state.checker.aclose()
        logger.info("application shutdown")

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
    )

    app.state.settings = settings
    app.state.policy = TargetPolicy(frozenset(settings.monitoring_allowed_local_hosts))

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    register_request_logging(app)
    register_error_handlers(app)
    app.include_router(api_router)
    return app


app = create_app()
