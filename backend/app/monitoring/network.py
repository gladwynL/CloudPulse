"""Policy-enforcing TCP connections for the monitoring HTTP client.

Validating a hostname and then letting the HTTP client resolve it again is a
time-of-check/time-of-use hole (DNS rebinding: the first answer is public, the second
is ``127.0.0.1``). ``GuardedNetworkBackend`` removes the second lookup: it resolves the
host once, validates *every* returned address against the policy, and then opens the
socket to one of those validated IP literals.

TLS is unaffected: httpcore derives the SNI/certificate hostname from the request URL,
not from the address passed to ``connect_tcp``, so certificates are still verified
against the original hostname.
"""

import asyncio
import socket
import typing
from collections.abc import Awaitable, Callable, Iterable

import httpcore

from app.monitoring.security import TargetNotAllowedError, TargetPolicy

Resolver = Callable[[str, int], Awaitable[list[str]]]


class DnsResolutionError(Exception):
    """The hostname could not be resolved."""


class BlockedTargetError(Exception):
    """The target (or an address it resolved to) is forbidden by the network policy."""


async def system_resolver(host: str, port: int) -> list[str]:
    """Resolve ``host`` using the OS resolver without blocking the event loop."""
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise DnsResolutionError("hostname could not be resolved") from exc
    seen: dict[str, None] = {}
    for info in infos:
        seen.setdefault(str(info[4][0]), None)
    if not seen:
        raise DnsResolutionError("hostname resolved to no addresses")
    return list(seen)


class GuardedNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(
        self,
        policy: TargetPolicy,
        resolver: Resolver = system_resolver,
        inner: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        self._policy = policy
        self._resolver = resolver
        self._inner = inner or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[typing.Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        addresses = await self._resolver(host, port)
        try:
            self._policy.assert_addresses_allowed(host, port, addresses)
        except TargetNotAllowedError as exc:
            raise BlockedTargetError(str(exc)) from exc

        last_error: Exception | None = None
        for address in addresses:
            try:
                # Connect to the validated literal; the client never re-resolves ``host``.
                return await self._inner.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
        assert last_error is not None  # addresses is non-empty (policy rejects empty)
        raise last_error

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[typing.Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise BlockedTargetError("unix socket targets are not permitted")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)
