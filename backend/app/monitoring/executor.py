"""Run one claimed check end to end: execute, persist, log.

This is the single code path for both scheduled and manual checks, and the natural seam
for later phases: the ``CheckResult`` produced here is what a history table will record.
"""

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from app.models.resource import ResourceStatus
from app.monitoring.store import CheckStore
from app.monitoring.types import (
    CheckResult,
    CheckSource,
    CheckTarget,
    ClaimedCheck,
    ErrorCategory,
)

logger = logging.getLogger("cloudpulse.monitoring")


class Checker(Protocol):
    async def check(self, target: CheckTarget) -> CheckResult: ...


@dataclass(frozen=True, slots=True)
class CheckOutcome:
    result: CheckResult
    # False when the lease was lost (resource deleted, disabled, reconfigured, or the lease
    # expired) and the result was therefore discarded rather than written.
    stored: bool


async def execute_claimed_check(
    store: CheckStore,
    checker: Checker,
    claim: ClaimedCheck,
    source: CheckSource,
) -> CheckOutcome:
    """Execute a check for a lease we hold and persist the result.

    On cancellation the lease is released (best effort) and ``CancelledError`` propagates.
    Persistence errors propagate to the caller, which owns the retry policy; the lease then
    simply expires and the resource becomes due again.
    """
    started = time.monotonic()
    try:
        try:
            result = await checker.check(claim.target)
        except Exception:
            # Checkers are expected to return failures, not raise; never let one escape.
            logger.exception(
                "checker raised unexpectedly",
                extra={"resource_id": str(claim.resource_id), "check_source": source.value},
            )
            result = CheckResult(
                status=ResourceStatus.DOWN,
                http_status_code=None,
                response_time_ms=None,
                error="internal: internal error during check",
                error_category=ErrorCategory.INTERNAL,
                duration_ms=(time.monotonic() - started) * 1000,
            )
        stored = await store.complete(claim, result, source)
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            await store.release(claim)
        raise
    except Exception as exc:
        # Nothing was committed (state and history roll back together); the lease expires
        # and the resource is retried. The caller decides how to surface the failure.
        logger.error(
            "failed to persist check result",
            extra={
                "resource_id": str(claim.resource_id),
                "check_source": source.value,
                "error": type(exc).__name__,
            },
        )
        raise

    _log_outcome(claim, source, result, stored)
    return CheckOutcome(result=result, stored=stored)


def _log_outcome(
    claim: ClaimedCheck, source: CheckSource, result: CheckResult, stored: bool
) -> None:
    # Host only: full URLs may carry tokens in the path or query string.
    host = urlsplit(claim.target.url).hostname
    logger.log(
        logging.INFO if stored else logging.WARNING,
        "check completed" if stored else "check result discarded (lease lost)",
        extra={
            "resource_id": str(claim.resource_id),
            "target_host": host,
            "check_source": source.value,
            "check_status": result.status.value,
            "error_category": result.error_category.value if result.error_category else None,
            "http_status_code": result.http_status_code,
            "response_time_ms": (
                round(result.response_time_ms, 2) if result.response_time_ms is not None else None
            ),
            "duration_ms": round(result.duration_ms, 2),
            "stored": stored,
        },
    )
