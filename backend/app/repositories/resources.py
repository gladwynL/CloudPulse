"""Queries for monitored resources, including the check-lease protocol.

Lease protocol (database-backed, safe with several workers or API replicas):

* ``claim_*`` atomically stamps a fresh ``lease_token`` and an expiry on the row. Rows
  already holding an unexpired lease are skipped (``FOR UPDATE SKIP LOCKED`` keeps
  concurrent claimers from blocking or double-claiming).
* ``complete`` writes the result only if the caller still holds the token, so a result
  can never overwrite newer state, resurrect a deleted row, or apply to a resource whose
  configuration changed mid-check (API updates clear the lease).
* A crashed executor simply stops renewing: once ``lease_expires_at`` passes, the
  resource is claimable again. The lease is ``timeout + grace`` long, and every check has
  a hard deadline of ``timeout``, so a live check never outlives its lease.

All timestamps come from the database clock (``now()``), so multiple processes agree on
"due" regardless of their own clock skew. Functions here never commit; callers own the
transaction.
"""

import uuid

from sqlalchemy import Interval, func, insert, literal_column, or_, select, update
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from app.alerting.engine import evaluate_accepted_result
from app.models.check_result import ResourceCheckResult
from app.models.enums import CheckSource
from app.models.resource import MonitoredResource as M
from app.models.resource import ResourceStatus
from app.monitoring.types import CheckResult, CheckTarget, ClaimedCheck, ClaimOutcome

_ONE_SECOND = literal_column("interval '1 second'", type_=Interval())

_CLAIM_COLUMNS = (M.id, M.url, M.timeout_seconds, M.expected_status_code, M.lease_token)


def _lease_free():
    return or_(M.lease_expires_at.is_(None), M.lease_expires_at <= func.now())


def _to_claim(row: Row, owner: str) -> ClaimedCheck:
    return ClaimedCheck(
        resource_id=row.id,
        target=CheckTarget(
            url=row.url,
            timeout_seconds=row.timeout_seconds,
            expected_status_code=row.expected_status_code,
        ),
        lease_token=row.lease_token,
        owner=owner,
    )


# --- CRUD ---------------------------------------------------------------------------


def add(session: Session, resource: M) -> M:
    session.add(resource)
    session.flush()
    return resource


def get(session: Session, resource_id: uuid.UUID, *, for_update: bool = False) -> M | None:
    stmt = select(M).where(M.id == resource_id)
    if for_update:
        stmt = stmt.with_for_update()
    return session.execute(stmt).scalar_one_or_none()


def list_page(
    session: Session,
    *,
    limit: int,
    offset: int,
    is_enabled: bool | None = None,
    status: ResourceStatus | None = None,
) -> tuple[list[M], int]:
    filters = []
    if is_enabled is not None:
        filters.append(M.is_enabled.is_(is_enabled))
    if status is not None:
        filters.append(M.current_status == status)

    total = session.execute(select(func.count()).select_from(M).where(*filters)).scalar_one()
    items = list(
        session.execute(
            select(M).where(*filters).order_by(M.created_at, M.id).limit(limit).offset(offset)
        ).scalars()
    )
    return items, total


def delete(session: Session, resource: M) -> None:
    session.delete(resource)
    session.flush()


# --- Leasing ------------------------------------------------------------------------


def claim_due(
    session: Session, *, limit: int, owner: str, grace_seconds: int
) -> list[ClaimedCheck]:
    """Lease up to ``limit`` enabled resources whose ``next_check_at`` has passed."""
    due = (
        select(M.id)
        .where(M.is_enabled.is_(True), M.next_check_at <= func.now(), _lease_free())
        .order_by(M.next_check_at, M.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    stmt = (
        update(M)
        .where(M.id.in_(due))
        .values(
            lease_token=func.gen_random_uuid(),
            lease_owner=owner,
            lease_expires_at=func.now() + (M.timeout_seconds + grace_seconds) * _ONE_SECOND,
        )
        .returning(*_CLAIM_COLUMNS)
        .execution_options(synchronize_session=False)
    )
    return [_to_claim(row, owner) for row in session.execute(stmt)]


def claim_by_id(
    session: Session, resource_id: uuid.UUID, *, owner: str, grace_seconds: int
) -> ClaimedCheck | ClaimOutcome:
    """Lease one specific resource (manual check). Ignores ``next_check_at``."""
    stmt = (
        update(M)
        .where(M.id == resource_id, M.is_enabled.is_(True), _lease_free())
        .values(
            lease_token=func.gen_random_uuid(),
            lease_owner=owner,
            lease_expires_at=func.now() + (M.timeout_seconds + grace_seconds) * _ONE_SECOND,
        )
        .returning(*_CLAIM_COLUMNS)
        .execution_options(synchronize_session=False)
    )
    row = session.execute(stmt).first()
    if row is not None:
        return _to_claim(row, owner)

    enabled = session.execute(select(M.is_enabled).where(M.id == resource_id)).scalar_one_or_none()
    if enabled is None:
        return ClaimOutcome.NOT_FOUND
    return ClaimOutcome.BUSY if enabled else ClaimOutcome.DISABLED


def complete(
    session: Session, claim: ClaimedCheck, result: CheckResult, source: CheckSource
) -> bool:
    """Accept ``result``: update the latest state, append a history row, and evaluate alert rules,
    all atomically.

    The lease-guarded UPDATE is the gate. It matches only while this executor still holds the
    lease; if it matches nothing (resource deleted, disabled, reconfigured, lease expired or
    already completed) we return False and write **no** history, so a discarded result can
    never appear as a measurement and a repeated completion can never duplicate one.

    Both writes happen in the caller's single transaction (this function never commits): if
    the insert fails, the caller's rollback also undoes the state update and the lease stays
    held until it expires. ``checked_at`` is the transaction timestamp, so it equals the
    resource's ``last_checked_at`` for this check. Alert evaluation (and any incident change) is
    part of the same transaction and runs only for accepted results.
    """
    stmt = (
        update(M)
        .where(M.id == claim.resource_id, M.lease_token == claim.lease_token)
        .values(
            current_status=result.status,
            last_checked_at=func.now(),
            # Uses the row's *current* interval, so an interval edited mid-check applies.
            next_check_at=func.now() + M.check_interval_seconds * _ONE_SECOND,
            last_response_time_ms=_rounded(result.response_time_ms),
            last_http_status_code=result.http_status_code,
            last_error=result.error[:255] if result.error else None,
            lease_token=None,
            lease_owner=None,
            lease_expires_at=None,
        )
        .execution_options(synchronize_session=False)
    )
    if session.execute(stmt).rowcount != 1:
        return False

    check_id = uuid.uuid4()
    session.execute(
        insert(ResourceCheckResult).values(
            id=check_id,
            resource_id=claim.resource_id,
            checked_at=func.now(),
            status=result.status,
            http_status_code=result.http_status_code,
            response_time_ms=_rounded(result.response_time_ms),
            duration_ms=_rounded(result.duration_ms),
            error_category=result.error_category,
            error_summary=result.error[:255] if result.error else None,
            source=source,
        )
    )
    # Alert evaluation joins this transaction: it sees only accepted results, and if it fails the
    # measurement and the state update roll back with it (the lease then expires and retries).
    evaluate_accepted_result(
        session,
        resource_id=claim.resource_id,
        check_id=check_id,
        status=result.status,
        response_time_ms=_rounded(result.response_time_ms),
    )
    return True


def _rounded(value: float | None) -> float | None:
    """Round once, here, to the column's two decimals so the database never re-rounds."""
    return None if value is None else round(value, 2)


def release(session: Session, claim: ClaimedCheck) -> None:
    """Give up a lease without a result (e.g. shutdown); the resource stays due."""
    session.execute(
        update(M)
        .where(M.id == claim.resource_id, M.lease_token == claim.lease_token)
        .values(lease_token=None, lease_owner=None, lease_expires_at=None)
        .execution_options(synchronize_session=False)
    )
