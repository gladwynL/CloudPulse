"""HttpChecker against a real local server, exercising the full guarded transport."""

import asyncio
import ssl
import time
from collections.abc import AsyncIterator

import httpcore
import pytest

from app.models.resource import ResourceStatus
from app.monitoring.checker import HttpChecker
from app.monitoring.network import DnsResolutionError
from app.monitoring.security import TargetPolicy
from app.monitoring.types import CheckTarget, ErrorCategory
from tests.conftest import TargetServer

pytestmark = pytest.mark.anyio


def target(url: str, *, timeout: int = 5, expected: int = 200) -> CheckTarget:
    return CheckTarget(url=url, timeout_seconds=timeout, expected_status_code=expected)


@pytest.fixture
async def checker(target_server: TargetServer) -> AsyncIterator[HttpChecker]:
    # The local test server is on loopback, so it must be explicitly allowlisted. Every
    # other test in this module that wants the *default* policy builds its own checker.
    instance = HttpChecker(TargetPolicy(frozenset({target_server.allow_entry})))
    yield instance
    await instance.aclose()


async def test_expected_status_is_healthy(checker: HttpChecker, target_server: TargetServer):
    result = await checker.check(target(target_server.url("/ok")))

    assert result.status is ResourceStatus.HEALTHY
    assert result.http_status_code == 200
    assert result.error is None
    assert result.error_category is None
    assert result.response_time_ms is not None
    assert 0 <= result.response_time_ms <= result.duration_ms + 1


async def test_unexpected_status_is_degraded_not_down(
    checker: HttpChecker, target_server: TargetServer
):
    result = await checker.check(target(target_server.url("/status/503")))

    assert result.status is ResourceStatus.DEGRADED
    assert result.http_status_code == 503
    assert result.error_category is ErrorCategory.UNEXPECTED_STATUS
    assert result.error == "expected status 200, got 503"
    assert result.response_time_ms is not None  # the service did answer


async def test_non_200_expected_status_is_honoured(
    checker: HttpChecker, target_server: TargetServer
):
    result = await checker.check(target(target_server.url("/status/204"), expected=204))
    assert result.status is ResourceStatus.HEALTHY


async def test_redirects_are_not_followed(checker: HttpChecker, target_server: TargetServer):
    result = await checker.check(target(target_server.url("/redirect")))

    assert result.status is ResourceStatus.DEGRADED
    assert result.http_status_code == 302
    assert "redirect not followed" in (result.error or "")
    assert target_server.requests == ["/redirect"]  # /ok was never requested


async def test_redirect_can_be_the_expected_status(
    checker: HttpChecker, target_server: TargetServer
):
    result = await checker.check(target(target_server.url("/redirect"), expected=302))
    assert result.status is ResourceStatus.HEALTHY
    assert target_server.requests == ["/redirect"]


async def test_response_time_is_measured(checker: HttpChecker, target_server: TargetServer):
    result = await checker.check(target(target_server.url("/delay")))  # server waits 250 ms

    assert result.status is ResourceStatus.HEALTHY
    assert result.response_time_ms is not None
    assert 200 <= result.response_time_ms < 2000
    assert result.duration_ms >= result.response_time_ms


async def test_timeout_is_down_and_bounded(checker: HttpChecker, target_server: TargetServer):
    started = time.monotonic()
    result = await checker.check(target(target_server.url("/stall"), timeout=1))
    elapsed = time.monotonic() - started

    assert result.status is ResourceStatus.DOWN
    assert result.error_category is ErrorCategory.TIMEOUT
    assert result.error == "timeout: no response within 1s"
    assert result.http_status_code is None
    assert result.response_time_ms is None
    assert elapsed < 3  # hard deadline, not "eventually"


async def test_response_body_is_not_downloaded(checker: HttpChecker, target_server: TargetServer):
    # Headers promise 100 MB and the server then stalls; a body read would hit the timeout.
    started = time.monotonic()
    result = await checker.check(target(target_server.url("/bigbody"), timeout=5))

    assert result.status is ResourceStatus.HEALTHY
    assert time.monotonic() - started < 2


async def test_connection_refused_is_down_with_sanitized_error(target_server: TargetServer):
    checker = HttpChecker(TargetPolicy(frozenset({"127.0.0.1:1"})))
    try:
        result = await checker.check(target("http://127.0.0.1:1/health"))
    finally:
        await checker.aclose()

    assert result.status is ResourceStatus.DOWN
    assert result.error_category is ErrorCategory.CONNECTION
    assert result.error == "connection: could not connect to host"
    # Raw exception text (addresses, errno strings) must never be stored or returned.
    for leaked in ("127.0.0.1", "refused", "Errno", "WinError"):
        assert leaked not in (result.error or "")


async def test_dns_failure_is_down():
    async def no_such_host(host: str, port: int) -> list[str]:
        raise DnsResolutionError("nxdomain")

    checker = HttpChecker(TargetPolicy(), resolver=no_such_host)
    try:
        result = await checker.check(target("https://missing.example/health"))
    finally:
        await checker.aclose()

    assert result.status is ResourceStatus.DOWN
    assert result.error_category is ErrorCategory.DNS
    assert result.error == "dns: hostname could not be resolved"
    assert "missing.example" not in result.error


async def test_real_tls_handshake_failure_is_down_and_categorized():
    # An HTTPS request to a plaintext server fails the real handshake inside httpcore.
    async def plaintext(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(plaintext, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    checker = HttpChecker(TargetPolicy(frozenset({f"127.0.0.1:{port}"})))
    try:
        result = await checker.check(target(f"https://127.0.0.1:{port}/"))
    finally:
        await checker.aclose()
        server.close()

    assert result.status is ResourceStatus.DOWN
    assert result.error_category is ErrorCategory.TLS
    assert result.error == "tls: TLS handshake or certificate verification failed"


async def test_certificate_error_in_cause_chain_is_categorized_as_tls():
    # Certificate errors surface from start_tls as ConnectError(cause=SSLCertVerificationError).
    class CertFailureBackend(httpcore.AsyncNetworkBackend):
        async def connect_tcp(
            self, host, port, timeout=None, local_address=None, socket_options=None
        ):
            try:
                raise ssl.SSLCertVerificationError("self-signed (secret-detail)")
            except ssl.SSLError as exc:
                # httpcore later re-raises with `from None`; the SSL error survives as context.
                raise httpcore.ConnectError("handshake failed (secret-detail)") from exc

        async def connect_unix_socket(self, path, timeout=None, socket_options=None):
            raise AssertionError

        async def sleep(self, seconds):
            return None

    async def public(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    checker = HttpChecker(TargetPolicy(), resolver=public, network_backend=CertFailureBackend())
    try:
        result = await checker.check(target("https://selfsigned.example/"))
    finally:
        await checker.aclose()

    assert result.error_category is ErrorCategory.TLS
    assert "secret-detail" not in (result.error or "")


# --- SSRF protection through the real transport -----------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:{port}/ok",
        "http://localhost:{port}/ok",
        "http://[::1]:{port}/ok",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/",
    ],
)
async def test_default_policy_blocks_unsafe_targets_without_connecting(
    target_server: TargetServer, url: str
):
    checker = HttpChecker(TargetPolicy())  # production default: empty allowlist
    try:
        result = await checker.check(target(url.format(port=target_server.port)))
    finally:
        await checker.aclose()

    assert result.status is ResourceStatus.DOWN
    assert result.error_category is ErrorCategory.BLOCKED
    assert result.error == "blocked: target address is not permitted"
    assert target_server.requests == []  # nothing ever reached the local server


async def test_public_looking_hostname_resolving_to_loopback_is_blocked(
    target_server: TargetServer,
):
    async def rebinding_dns(host: str, port: int) -> list[str]:
        return ["127.0.0.1"]

    checker = HttpChecker(TargetPolicy(), resolver=rebinding_dns)
    try:
        result = await checker.check(
            target(f"http://totally-public.example:{target_server.port}/ok")
        )
    finally:
        await checker.aclose()

    assert result.error_category is ErrorCategory.BLOCKED
    assert target_server.requests == []


async def test_allowlist_is_scoped_to_the_listed_host_and_port(target_server: TargetServer):
    other_port = target_server.port + 1
    checker = HttpChecker(TargetPolicy(frozenset({target_server.allow_entry})))
    try:
        allowed = await checker.check(target(target_server.url("/ok")))
        other = await checker.check(target(f"http://127.0.0.1:{other_port}/ok"))
    finally:
        await checker.aclose()

    assert allowed.status is ResourceStatus.HEALTHY
    assert other.error_category is ErrorCategory.BLOCKED


async def test_environment_proxies_are_ignored(
    monkeypatch: pytest.MonkeyPatch, target_server: TargetServer
):
    # A proxy would receive the connection instead of the validated address.
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")
    checker = HttpChecker(TargetPolicy(frozenset({target_server.allow_entry})))
    try:
        result = await checker.check(target(target_server.url("/ok")))
    finally:
        await checker.aclose()
    assert result.status is ResourceStatus.HEALTHY


# --- Cancellation and robustness --------------------------------------------------------


async def test_cancellation_propagates_promptly(checker: HttpChecker, target_server: TargetServer):
    task = asyncio.create_task(checker.check(target(target_server.url("/stall"), timeout=30)))
    while not target_server.requests:  # wait until the request is in flight
        await asyncio.sleep(0.01)

    task.cancel()
    started = time.monotonic()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert time.monotonic() - started < 1


async def test_unexpected_exception_becomes_internal_failure(monkeypatch: pytest.MonkeyPatch):
    checker = HttpChecker(TargetPolicy())

    def boom(*args, **kwargs):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(checker._client, "stream", boom)
    try:
        result = await checker.check(target("https://example.com"))
    finally:
        await checker.aclose()

    assert result.status is ResourceStatus.DOWN
    assert result.error_category is ErrorCategory.INTERNAL
    assert "secret" not in (result.error or "")
