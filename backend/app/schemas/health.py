"""Schemas for liveness and readiness probes."""

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ReadinessChecks(BaseModel):
    database: Literal["ok", "unavailable"]


class ReadinessResponse(BaseModel):
    status: Literal["ready", "unavailable"]
    checks: ReadinessChecks
