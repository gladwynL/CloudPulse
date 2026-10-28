"""Domain-error to HTTP mapping. All errors use FastAPI's ``{"detail": ...}`` envelope."""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.services.resources import ResourceNotFoundError, ResourceValidationError


class CheckConflictError(Exception):
    """A manual check cannot run right now (disabled, busy, or result discarded)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ResourceNotFoundError)
    async def not_found(_: Request, __: ResourceNotFoundError) -> JSONResponse:
        return JSONResponse({"detail": "Resource not found"}, status.HTTP_404_NOT_FOUND)

    @app.exception_handler(CheckConflictError)
    async def conflict(_: Request, exc: CheckConflictError) -> JSONResponse:
        return JSONResponse({"detail": exc.message}, status.HTTP_409_CONFLICT)

    @app.exception_handler(ResourceValidationError)
    async def invalid(_: Request, exc: ResourceValidationError) -> JSONResponse:
        # Same shape as FastAPI's request-validation errors so clients handle one format.
        return JSONResponse(
            {
                "detail": [
                    {"type": "value_error", "loc": [exc.location, exc.field], "msg": exc.message}
                ]
            },
            422,
        )
