"""Request/response schemas for monitored resources."""

import uuid
from datetime import datetime
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.models.resource import ResourceStatus

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
Url = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]
StatusCode = Annotated[int, Field(ge=100, le=599)]


class ResourceCreate(BaseModel):
    """Register a service for monitoring.

    ``check_interval_seconds`` and ``timeout_seconds`` default from server settings and are
    validated against the configured bounds. The URL is checked against the network policy.
    """

    model_config = ConfigDict(extra="forbid")

    name: Name
    description: Description | None = None
    url: Url
    is_enabled: bool = True
    check_interval_seconds: int | None = Field(default=None, ge=1)
    timeout_seconds: int | None = Field(default=None, ge=1)
    expected_status_code: StatusCode = 200


class ResourceUpdate(BaseModel):
    """Partial update; omitted fields are unchanged. Only ``description`` accepts null."""

    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    description: Description | None = None
    url: Url | None = None
    is_enabled: bool | None = None
    check_interval_seconds: int | None = Field(default=None, ge=1)
    timeout_seconds: int | None = Field(default=None, ge=1)
    expected_status_code: StatusCode | None = None

    @model_validator(mode="after")
    def _reject_explicit_nulls(self) -> Self:
        for field in self.model_fields_set - {"description"}:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class ResourceStatusResponse(BaseModel):
    """Current health of a resource (the latest observation only)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    is_enabled: bool
    current_status: ResourceStatus
    last_checked_at: datetime | None
    next_check_at: datetime | None
    last_response_time_ms: float | None
    last_http_status_code: int | None
    last_error: str | None


class ResourceResponse(ResourceStatusResponse):
    description: str | None
    url: str
    check_interval_seconds: int
    timeout_seconds: int
    expected_status_code: int
    created_at: datetime
    updated_at: datetime


class ResourceListResponse(BaseModel):
    items: list[ResourceResponse]
    total: int
    limit: int
    offset: int
