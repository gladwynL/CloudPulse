"""Network safety policy for user-supplied monitoring targets (SSRF defense).

Two layers:

1. ``validate_target_url`` rejects obviously unsafe URLs when a resource is saved
   (no DNS lookups; registration must not block on, or depend on, name resolution).
2. ``classify_address`` / ``TargetPolicy.assert_addresses_allowed`` are applied at
   connection time to the addresses a hostname actually resolves to. That is the
   check that matters; see ``app.monitoring.network`` for how it is bound to the
   socket that is really opened.

Address classes:

* PUBLIC     globally routable; always allowed.
* RELAXABLE  loopback, RFC 1918, CGNAT, IPv6 unique-local. Blocked unless the target
             host is on the development-only allowlist.
* BLOCKED    link-local, cloud metadata, multicast, unspecified, reserved, and any other
             non-global range. Blocked even for allowlisted hosts.
"""

import enum
import ipaddress
import re
from dataclasses import dataclass, field
from urllib.parse import SplitResult, urlsplit

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

MAX_URL_LENGTH = 2048

_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_SIX_TO_FOUR = ipaddress.ip_network("2002::/16")

_METADATA_ADDRESSES = frozenset(
    ipaddress.ip_address(a)
    for a in (
        "169.254.169.254",  # AWS, Azure, GCP, OpenStack, DigitalOcean (also link-local)
        "169.254.170.2",  # AWS ECS task metadata
        "100.100.100.200",  # Alibaba Cloud
        "192.0.0.192",  # Oracle Cloud (legacy)
        "fd00:ec2::254",  # AWS IMDS over IPv6
    )
)

_RELAXABLE_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "127.0.0.0/8",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",
        "::1/128",
        "fc00::/7",
    )
)

# Names that are internal by convention; rejected at registration (resolution-time
# checks still apply to everything else).
_INTERNAL_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain", ".home.arpa")
_INTERNAL_NAMES = frozenset({"localhost", "ip6-localhost", "ip6-loopback", "broadcasthost"})

_HOST_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


class AddressClass(enum.Enum):
    PUBLIC = "public"
    RELAXABLE = "relaxable"
    BLOCKED = "blocked"


class TargetNotAllowedError(ValueError):
    """A URL or resolved address violates the network safety policy."""


def classify_address(ip: IPAddress) -> AddressClass:
    """Classify a single resolved address. Unknown or odd ranges fail closed (BLOCKED)."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return classify_address(ip.ipv4_mapped)
        if ip in _NAT64:
            return classify_address(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
        if ip in _SIX_TO_FOUR:
            return classify_address(ipaddress.IPv4Address((int(ip) >> 80) & 0xFFFFFFFF))

    if ip in _METADATA_ADDRESSES:
        return AddressClass.BLOCKED
    if ip.is_multicast or ip.is_unspecified or ip.is_link_local:
        return AddressClass.BLOCKED
    # Before the reserved check: Python flags ::1 as reserved (it lies inside ::/8).
    if any(ip in network for network in _RELAXABLE_NETWORKS):
        return AddressClass.RELAXABLE
    if ip.is_reserved:
        return AddressClass.BLOCKED
    return AddressClass.PUBLIC if ip.is_global else AddressClass.BLOCKED


def parse_ip(value: str) -> IPAddress | None:
    """Parse a literal IP, tolerating an IPv6 zone suffix (``fe80::1%eth0``)."""
    try:
        return ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return None


def _default_port(parts: SplitResult) -> int:
    return parts.port or (443 if parts.scheme == "https" else 80)


@dataclass(frozen=True, slots=True)
class TargetPolicy:
    """The network safety policy. Default (empty allowlist) is the production policy."""

    # Lower-case "host" or "host:port" entries exempt from the private-range block.
    allowed_local_hosts: frozenset[str] = field(default_factory=frozenset)

    def is_allowlisted(self, host: str, port: int) -> bool:
        host = host.lower().rstrip(".")
        return host in self.allowed_local_hosts or f"{host}:{port}" in self.allowed_local_hosts

    def validate_url(self, url: str) -> str:
        """Validate and normalize a target URL; raises ``TargetNotAllowedError``."""
        url = url.strip()
        if not url or len(url) > MAX_URL_LENGTH:
            raise TargetNotAllowedError(f"URL must be 1-{MAX_URL_LENGTH} characters")
        if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in url):
            raise TargetNotAllowedError("URL must not contain whitespace or control characters")
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError:
            raise TargetNotAllowedError("URL is malformed") from None

        if parts.scheme not in ("http", "https"):
            raise TargetNotAllowedError("URL scheme must be http or https")
        if parts.username is not None or parts.password is not None or "@" in parts.netloc:
            raise TargetNotAllowedError("URL must not contain embedded credentials")
        host = (parts.hostname or "").lower().rstrip(".")
        if not host:
            raise TargetNotAllowedError("URL must include a host")
        if port == 0:
            raise TargetNotAllowedError("URL port is invalid")

        if self.is_allowlisted(host, _default_port(parts)):
            return parts._replace(fragment="").geturl()

        if parse_ip(host) is not None:
            raise TargetNotAllowedError("IP address targets are not permitted; use a hostname")
        if host in _INTERNAL_NAMES or host.endswith(_INTERNAL_SUFFIXES):
            raise TargetNotAllowedError("internal hostnames are not permitted")
        labels = host.split(".")
        if not all(_HOST_LABEL.match(label) for label in labels):
            raise TargetNotAllowedError("URL host is not a valid hostname")
        # A numeric final label ("127.1", "2130706433") is parsed as an IP by resolvers.
        if labels[-1].isdigit():
            raise TargetNotAllowedError("URL host is not a valid hostname")
        return parts._replace(fragment="").geturl()

    def assert_addresses_allowed(self, host: str, port: int, addresses: list[str]) -> None:
        """Fail closed unless *every* resolved address is permitted.

        Requiring all addresses (not just the one we connect to) means a mixed answer such
        as ``[public, 127.0.0.1]`` is rejected outright instead of relying on connect order.
        """
        if not addresses:
            raise TargetNotAllowedError("host did not resolve to any address")
        allowlisted = self.is_allowlisted(host, port)
        for raw in addresses:
            ip = parse_ip(raw)
            if ip is None:
                raise TargetNotAllowedError("host resolved to an unparseable address")
            klass = classify_address(ip)
            if klass is AddressClass.BLOCKED:
                raise TargetNotAllowedError("target address is not permitted")
            if klass is AddressClass.RELAXABLE and not allowlisted:
                raise TargetNotAllowedError("target address is not permitted")
