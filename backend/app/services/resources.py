"""Resource management rules: validation, scheduling fields, and update semantics.

Semantics worth knowing (also documented in the README):

* New enabled resources are due immediately (``next_check_at = now``); status is UNKNOWN.
* Disabling clears ``next_check_at`` and any in-flight lease, so a check already running is
  discarded when it finishes and nothing further is scheduled. The last observed status and
  result are retained (a disabled resource shows what it last looked like).
* Re-enabling makes the resource due immediately.
* Changing ``url``, ``timeout_seconds`` or ``expected_status_code`` invalidates the previous
  observation: status resets to UNKNOWN, last_* fields clear, any in-flight check is
  discarded, and a fresh check is scheduled immediately.
* Changing only the interval keeps the observation and reschedules relative to the last check.
"""

import uuid
from datetime import timedelta
from typing import NamedTuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.alerting.management import close_resource_incidents
from app.core.config import Settings
from app.models.enums import ResolutionReason
from app.models.resource import MonitoredResource, ResourceStatus
from app.monitoring.security import TargetNotAllowedError, TargetPolicy
from app.repositories import resources as repo
from app.schemas.resource import ResourceCreate, ResourceUpdate

# Settings changes that invalidate the previous observation.
_OBSERVATION_FIELDS = ("url", "timeout_seconds", "expected_status_code")


class ResourceNotFoundError(Exception):
    pass


class ResourceValidationError(Exception):
    def __init__(self, field: str, message: str, location: str = "body") -> None:
        super().__init__(message)
        self.field = field
        self.message = message
        self.location = location


class Page(NamedTuple):
    items: list[MonitoredResource]
    total: int


def _validated_config(
    *,
    url: str,
    interval: int,
    timeout: int,
    settings: Settings,
    policy: TargetPolicy,
) -> str:
    try:
        normalized_url = policy.validate_url(url)
    except TargetNotAllowedError as exc:
        raise ResourceValidationError("url", str(exc)) from None
    lo, hi = settings.monitoring_min_interval_seconds, settings.monitoring_max_interval_seconds
    if not lo <= interval <= hi:
        raise ResourceValidationError(
            "check_interval_seconds", f"must be between {lo} and {hi} seconds"
        )
    max_timeout = settings.monitoring_max_timeout_seconds
    if not 1 <= timeout <= max_timeout:
        raise ResourceValidationError("timeout_seconds", f"must be between 1 and {max_timeout}")
    if timeout > interval:
        raise ResourceValidationError("timeout_seconds", "must not exceed check_interval_seconds")
    return normalized_url


def create_resource(
    session: Session, data: ResourceCreate, settings: Settings, policy: TargetPolicy
) -> MonitoredResource:
    interval = data.check_interval_seconds or settings.monitoring_default_interval_seconds
    timeout = data.timeout_seconds or min(settings.monitoring_default_timeout_seconds, interval)
    url = _validated_config(
        url=data.url, interval=interval, timeout=timeout, settings=settings, policy=policy
    )
    resource = MonitoredResource(
        name=data.name,
        description=data.description,
        url=url,
        is_enabled=data.is_enabled,
        check_interval_seconds=interval,
        timeout_seconds=timeout,
        expected_status_code=data.expected_status_code,
        current_status=ResourceStatus.UNKNOWN,
        next_check_at=func.now() if data.is_enabled else None,
    )
    repo.add(session, resource)
    session.commit()
    return resource


def get_resource(session: Session, resource_id: uuid.UUID) -> MonitoredResource:
    resource = repo.get(session, resource_id)
    if resource is None:
        raise ResourceNotFoundError
    return resource


def list_resources(
    session: Session,
    *,
    limit: int,
    offset: int,
    is_enabled: bool | None,
    status: ResourceStatus | None,
    name_contains: str | None = None,
) -> Page:
    items, total = repo.list_page(
        session,
        limit=limit,
        offset=offset,
        is_enabled=is_enabled,
        status=status,
        name_contains=name_contains,
    )
    return Page(items, total)


def update_resource(
    session: Session,
    resource_id: uuid.UUID,
    data: ResourceUpdate,
    settings: Settings,
    policy: TargetPolicy,
) -> MonitoredResource:
    # Row lock: serializes with a check completing at the same moment (see repository docs).
    resource = repo.get(session, resource_id, for_update=True)
    if resource is None:
        raise ResourceNotFoundError

    changes = data.model_dump(exclude_unset=True)
    merged_url = changes.get("url", resource.url)
    merged_interval = changes.get("check_interval_seconds", resource.check_interval_seconds)
    merged_timeout = changes.get("timeout_seconds", resource.timeout_seconds)
    if {"url", "check_interval_seconds", "timeout_seconds"} & changes.keys():
        changes["url"] = _validated_config(
            url=merged_url,
            interval=merged_interval,
            timeout=merged_timeout,
            settings=settings,
            policy=policy,
        )

    observation_invalidated = any(
        field in changes and changes[field] != getattr(resource, field)
        for field in _OBSERVATION_FIELDS
    )
    interval_changed = (
        "check_interval_seconds" in changes
        and changes["check_interval_seconds"] != resource.check_interval_seconds
    )
    enabled_changed = "is_enabled" in changes and changes["is_enabled"] != resource.is_enabled
    any_change = any(getattr(resource, field) != value for field, value in changes.items())
    if not any_change:
        return resource

    for field, value in changes.items():
        setattr(resource, field, value)

    if observation_invalidated:
        resource.current_status = ResourceStatus.UNKNOWN
        resource.last_checked_at = None
        resource.last_response_time_ms = None
        resource.last_http_status_code = None
        resource.last_error = None

    if enabled_changed or observation_invalidated:
        # Dropping the lease makes any in-flight check's result a no-op when it completes.
        resource.lease_token = None
        resource.lease_owner = None
        resource.lease_expires_at = None

    if not resource.is_enabled:
        resource.next_check_at = None
    elif enabled_changed or observation_invalidated:
        resource.next_check_at = func.now()
    elif interval_changed:
        resource.next_check_at = func.coalesce(
            MonitoredResource.last_checked_at, func.now()
        ) + timedelta(seconds=resource.check_interval_seconds)

    if enabled_changed and not resource.is_enabled:
        # Monitoring stopped: close active incidents explicitly and reset rule progress, in this
        # same transaction (we hold the resource row lock).
        close_resource_incidents(
            session, resource_id, ResolutionReason.RESOURCE_DISABLED, reset_rules=True
        )

    resource.updated_at = func.now()
    session.flush()
    session.commit()
    return resource


def delete_resource(session: Session, resource_id: uuid.UUID) -> None:
    resource = repo.get(session, resource_id, for_update=True)
    if resource is None:
        raise ResourceNotFoundError
    # History is kept: incidents survive with name snapshots (their FKs are nulled by the database),
    # but none may stay active, so close them explicitly first. Rules cascade with the resource.
    close_resource_incidents(
        session, resource_id, ResolutionReason.RESOURCE_DELETED, reset_rules=False
    )
    repo.delete(session, resource)
    session.commit()
