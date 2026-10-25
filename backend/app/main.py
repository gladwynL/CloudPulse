"""FastAPI application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.middleware import register_request_logging

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
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "application startup",
            extra={"version": settings.app_version, "environment": settings.environment},
        )
        yield
        logger.info("application shutdown")

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    register_request_logging(app)
    app.include_router(api_router)
    return app


app = create_app()
