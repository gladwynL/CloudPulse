"""Outbound HTTP health check.

Semantics (kept deliberately small):

* GET only; redirects are never followed (a 3xx is just an unexpected status unless it
  is the configured expected status).
* Only response headers are read; the body is never downloaded.
* ``response_time_ms`` is the time until response headers arrive.
* Result status: HEALTHY (expected status), DEGRADED (completed, other status),
  DOWN (could not complete). Error text is fixed per category so that raw exception
  messages (which can contain internal addresses) never reach the database or API.
"""

import asyncio
import logging
import ssl
import time
from functools import lru_cache

import httpcore
import httpx

from app.models.resource import ResourceStatus
from app.monitoring.network import (
    BlockedTargetError,
    DnsResolutionError,
    GuardedNetworkBackend,
    Resolver,
    system_resolver,
)
from app.monitoring.security import TargetPolicy
from app.monitoring.types import CheckResult, CheckTarget, ErrorCategory

logger = logging.getLogger(__name__)


@lru_cache
def _ssl_context() -> ssl.SSLContext:
    # Loading the CA bundle is slow; one verifying context is safely shared by all checkers.
    return httpx.create_ssl_context()


USER_AGENT = "CloudPulse-Monitor/0.1 (+https://github.com/gladwynL/CloudPulse)"


class HttpChecker:
    def __init__(
        self,
        policy: TargetPolicy,
        *,
        max_connections: int = 20,
        resolver: Resolver = system_resolver,
        network_backend: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        backend = GuardedNetworkBackend(policy, resolver=resolver, inner=network_backend)
        transport = httpx.AsyncHTTPTransport(verify=_ssl_context(), retries=0)
        # httpx does not expose a network-backend hook, so swap in a pool that uses the
        # guarded backend. httpx is pinned (requirements.txt) and the connection tests
        # exercise this path end to end.
        transport._pool = httpcore.AsyncConnectionPool(
            ssl_context=_ssl_context(),  # certificate verification stays on
            max_connections=max_connections,
            max_keepalive_connections=0,  # fresh connection per check; honest timings
            http1=True,
            http2=False,
            network_backend=backend,
        )
        self._client = httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            trust_env=False,  # environment proxies would bypass address pinning
            headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def check(self, target: CheckTarget) -> CheckResult:
        started = time.monotonic()
        response_time_ms: float | None = None
        http_status: int | None = None
        try:
            # Hard wall-clock deadline covering DNS, connect, TLS, and headers. httpx's own
            # timeouts are per-phase and would allow a slow-drip target to run far longer.
            async with asyncio.timeout(target.timeout_seconds):
                async with self._client.stream(
                    "GET",
                    target.url,
                    timeout=httpx.Timeout(float(target.timeout_seconds)),
                ) as response:
                    response_time_ms = (time.monotonic() - started) * 1000
                    http_status = response.status_code
        except asyncio.CancelledError:
            raise
        except (TimeoutError, httpx.TimeoutException):
            return self._failure(
                started,
                ErrorCategory.TIMEOUT,
                f"no response within {target.timeout_seconds}s",
            )
        except BlockedTargetError:
            return self._failure(started, ErrorCategory.BLOCKED, "target address is not permitted")
        except DnsResolutionError:
            return self._failure(started, ErrorCategory.DNS, "hostname could not be resolved")
        except httpx.ConnectError as exc:
            if _is_tls_error(exc):
                return self._failure(
                    started, ErrorCategory.TLS, "TLS handshake or certificate verification failed"
                )
            return self._failure(started, ErrorCategory.CONNECTION, "could not connect to host")
        except httpx.HTTPError:
            return self._failure(
                started, ErrorCategory.PROTOCOL, "invalid or interrupted HTTP exchange"
            )
        except Exception:
            # A bug or unexpected condition must not kill the scheduler or leak details.
            logger.exception("unexpected error while checking target")
            return self._failure(started, ErrorCategory.INTERNAL, "internal error during check")

        duration_ms = (time.monotonic() - started) * 1000
        if http_status == target.expected_status_code:
            return CheckResult(
                status=ResourceStatus.HEALTHY,
                http_status_code=http_status,
                response_time_ms=response_time_ms,
                error=None,
                error_category=None,
                duration_ms=duration_ms,
            )
        note = " (redirect not followed)" if 300 <= http_status < 400 else ""
        return CheckResult(
            status=ResourceStatus.DEGRADED,
            http_status_code=http_status,
            response_time_ms=response_time_ms,
            error=f"expected status {target.expected_status_code}, got {http_status}{note}",
            error_category=ErrorCategory.UNEXPECTED_STATUS,
            duration_ms=duration_ms,
        )

    @staticmethod
    def _failure(started: float, category: ErrorCategory, detail: str) -> CheckResult:
        return CheckResult(
            status=ResourceStatus.DOWN,
            http_status_code=None,
            response_time_ms=None,
            error=f"{category.value}: {detail}",
            error_category=category,
            duration_ms=(time.monotonic() - started) * 1000,
        )


def _is_tls_error(exc: BaseException) -> bool:
    """True if ``exc`` or anything in its cause chain is an SSL error."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, ssl.SSLError):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False
