"""The guarded backend must connect to the *validated* address, never re-resolve."""

import httpcore
import pytest

from app.monitoring.network import (
    BlockedTargetError,
    DnsResolutionError,
    GuardedNetworkBackend,
    system_resolver,
)
from app.monitoring.security import TargetPolicy

pytestmark = pytest.mark.anyio


class RecordingBackend(httpcore.AsyncNetworkBackend):
    """Inner backend that records connect targets; configured addresses fail to connect."""

    def __init__(self, failing: frozenset[str] = frozenset()) -> None:
        self.connected: list[tuple[str, int]] = []
        self._failing = failing

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.connected.append((host, port))
        if host in self._failing:
            raise httpcore.ConnectError("refused")
        return object()  # a stand-in stream; the guard only returns it

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise AssertionError("must never be reached")

    async def sleep(self, seconds):
        return None


def resolver_returning(*answers: list[str]):
    """Resolver whose successive calls return successive answers (simulates rebinding)."""
    calls: list[tuple[str, int]] = []
    remaining = list(answers)

    async def resolve(host: str, port: int) -> list[str]:
        calls.append((host, port))
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    resolve.calls = calls  # type: ignore[attr-defined]
    return resolve


async def test_connects_to_validated_ip_not_hostname() -> None:
    inner = RecordingBackend()
    resolver = resolver_returning(["93.184.216.34"])
    backend = GuardedNetworkBackend(TargetPolicy(), resolver=resolver, inner=inner)

    await backend.connect_tcp("example.com", 443)

    assert inner.connected == [("93.184.216.34", 443)]
    assert len(resolver.calls) == 1  # exactly one resolution per connection


async def test_dns_rebinding_cannot_swap_in_a_private_address() -> None:
    # A rebinding attacker answers public first, then 127.0.0.1. The backend resolves once,
    # so only the validated first answer is ever connected to.
    inner = RecordingBackend()
    resolver = resolver_returning(["93.184.216.34"], ["127.0.0.1"])
    backend = GuardedNetworkBackend(TargetPolicy(), resolver=resolver, inner=inner)

    await backend.connect_tcp("rebind.example", 80)

    assert inner.connected == [("93.184.216.34", 80)]
    assert len(resolver.calls) == 1


async def test_private_answer_is_blocked_before_any_connection() -> None:
    inner = RecordingBackend()
    backend = GuardedNetworkBackend(
        TargetPolicy(), resolver=resolver_returning(["10.0.0.7"]), inner=inner
    )
    with pytest.raises(BlockedTargetError):
        await backend.connect_tcp("internal.example", 80)
    assert inner.connected == []


async def test_mixed_public_and_private_answer_is_blocked_outright() -> None:
    inner = RecordingBackend()
    backend = GuardedNetworkBackend(
        TargetPolicy(),
        resolver=resolver_returning(["93.184.216.34", "169.254.169.254"]),
        inner=inner,
    )
    with pytest.raises(BlockedTargetError):
        await backend.connect_tcp("mixed.example", 80)
    assert inner.connected == []


async def test_falls_back_to_next_validated_address_when_connect_fails() -> None:
    inner = RecordingBackend(failing=frozenset({"93.184.216.34"}))
    backend = GuardedNetworkBackend(
        TargetPolicy(),
        resolver=resolver_returning(["93.184.216.34", "93.184.216.35"]),
        inner=inner,
    )
    await backend.connect_tcp("example.com", 80)
    assert inner.connected == [("93.184.216.34", 80), ("93.184.216.35", 80)]


async def test_connect_error_surfaces_when_every_address_fails() -> None:
    inner = RecordingBackend(failing=frozenset({"93.184.216.34"}))
    backend = GuardedNetworkBackend(
        TargetPolicy(), resolver=resolver_returning(["93.184.216.34"]), inner=inner
    )
    with pytest.raises(httpcore.ConnectError):
        await backend.connect_tcp("example.com", 80)


async def test_allowlisted_host_may_connect_to_private_address() -> None:
    inner = RecordingBackend()
    backend = GuardedNetworkBackend(
        TargetPolicy(frozenset({"demo-target"})),
        resolver=resolver_returning(["172.18.0.9"]),
        inner=inner,
    )
    await backend.connect_tcp("demo-target", 8080)
    assert inner.connected == [("172.18.0.9", 8080)]


async def test_unix_sockets_are_never_allowed() -> None:
    backend = GuardedNetworkBackend(TargetPolicy(), inner=RecordingBackend())
    with pytest.raises(BlockedTargetError):
        await backend.connect_unix_socket("/var/run/docker.sock")


async def test_system_resolver_reports_failure_as_dns_error() -> None:
    with pytest.raises(DnsResolutionError):
        await system_resolver("nonexistent.invalid", 80)  # .invalid is reserved (RFC 2606)


async def test_system_resolver_resolves_literals_without_network() -> None:
    assert await system_resolver("127.0.0.1", 80) == ["127.0.0.1"]
