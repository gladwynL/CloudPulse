import ipaddress

import pytest

from app.core.config import Settings
from app.monitoring.security import (
    AddressClass,
    TargetNotAllowedError,
    TargetPolicy,
    classify_address,
)

DEFAULT = TargetPolicy()


def klass(value: str) -> AddressClass:
    return classify_address(ipaddress.ip_address(value))


@pytest.mark.parametrize(
    "address",
    ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700:4700::1111", "2001:4860:4860::8888"],
)
def test_public_addresses_are_public(address: str) -> None:
    assert klass(address) is AddressClass.PUBLIC


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "127.1.2.3",
        "::1",
        "10.0.0.5",
        "172.16.0.1",
        "172.31.255.254",
        "192.168.1.1",
        "100.64.0.1",  # CGNAT
        "fc00::1",
        "fd12:3456::1",  # unique local
    ],
)
def test_loopback_and_private_addresses_are_relaxable_only(address: str) -> None:
    assert klass(address) is AddressClass.RELAXABLE


@pytest.mark.parametrize(
    "address",
    [
        "169.254.169.254",  # cloud metadata (also link-local)
        "169.254.170.2",  # ECS task metadata
        "100.100.100.200",  # Alibaba metadata (inside CGNAT, still blocked outright)
        "192.0.0.192",  # Oracle legacy metadata
        "fd00:ec2::254",  # AWS IPv6 metadata (inside unique-local range)
        "169.254.1.1",  # link-local
        "fe80::1",  # IPv6 link-local
        "0.0.0.0",
        "::",
        "224.0.0.1",  # multicast
        "ff02::1",
        "240.0.0.1",  # reserved
        "255.255.255.255",
        "192.0.2.1",  # documentation
        "198.18.0.1",  # benchmarking
        "2001:db8::1",  # IPv6 documentation
    ],
)
def test_dangerous_and_non_global_addresses_are_blocked(address: str) -> None:
    assert klass(address) is AddressClass.BLOCKED


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("::ffff:127.0.0.1", AddressClass.RELAXABLE),  # IPv4-mapped loopback
        ("::ffff:10.1.1.1", AddressClass.RELAXABLE),
        ("::ffff:169.254.169.254", AddressClass.BLOCKED),  # mapped metadata
        ("::ffff:8.8.8.8", AddressClass.PUBLIC),
        ("64:ff9b::7f00:1", AddressClass.RELAXABLE),  # NAT64 -> 127.0.0.1
        ("64:ff9b::a9fe:a9fe", AddressClass.BLOCKED),  # NAT64 -> 169.254.169.254
        ("2002:7f00:1::", AddressClass.RELAXABLE),  # 6to4 -> 127.0.0.1
        ("2002:a9fe:a9fe::", AddressClass.BLOCKED),  # 6to4 -> 169.254.169.254
    ],
)
def test_embedded_ipv4_addresses_are_classified_by_their_ipv4(
    address: str, expected: AddressClass
) -> None:
    assert klass(address) is expected


# --- URL validation (registration time) -------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://example.com:8080/health?x=1",
        "https://api.example.co.uk/v1/status",
        "https://EXAMPLE.com./path",
        "https://xn--bcher-kva.example/",
    ],
)
def test_valid_public_hostnames_are_accepted(url: str) -> None:
    assert DEFAULT.validate_url(url).startswith("http")


def test_fragment_is_stripped() -> None:
    assert DEFAULT.validate_url("https://example.com/a#frag") == "https://example.com/a"


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com",
        "file:///etc/passwd",
        "gopher://example.com",
        "javascript:alert(1)",
        "//example.com",
        "example.com",
        "",
        "   ",
        "http://",
        "http:///path",
    ],
)
def test_unsupported_or_malformed_urls_are_rejected(url: str) -> None:
    with pytest.raises(TargetNotAllowedError):
        DEFAULT.validate_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://user:pass@example.com",
        "http://user@example.com",
        "http://:pass@example.com",
        "http://example.com@evil.example",
        "http://example.com%40evil.example@127.0.0.1",
    ],
)
def test_embedded_credentials_are_rejected(url: str) -> None:
    with pytest.raises(TargetNotAllowedError):
        DEFAULT.validate_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1",
        "http://127.0.0.1:8000/health",
        "http://localhost",
        "http://LOCALHOST:8000",
        "http://foo.localhost",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://10.0.0.1",
        "http://192.168.1.1",
        "http://169.254.169.254/latest/meta-data/",
        "http://[fe80::1]/",
        "http://8.8.8.8",  # even public IP literals: use a hostname
        "http://metadata.google.internal/",
        "http://printer.local/",
        "http://2130706433/",  # decimal-encoded 127.0.0.1
        "http://127.1/",
        "http://0x7f.0.0.1/",
        "http://0177.0.0.1/",
        "http://0/",
    ],
)
def test_local_and_literal_ip_targets_are_rejected_by_default(url: str) -> None:
    with pytest.raises(TargetNotAllowedError):
        DEFAULT.validate_url(url)


@pytest.mark.parametrize(
    "url",
    ["http://exa mple.com", "http://example.com/a b", "http://exa\x00mple.com", "http://a\nb.com"],
)
def test_whitespace_and_control_characters_are_rejected(url: str) -> None:
    with pytest.raises(TargetNotAllowedError):
        DEFAULT.validate_url(url)


def test_invalid_ports_are_rejected() -> None:
    for url in ("http://example.com:99999", "http://example.com:0", "http://example.com:abc"):
        with pytest.raises(TargetNotAllowedError):
            DEFAULT.validate_url(url)


def test_overlong_url_is_rejected() -> None:
    with pytest.raises(TargetNotAllowedError):
        DEFAULT.validate_url("https://example.com/" + "a" * 2100)


# --- Resolved-address validation (connection time) --------------------------------------


def test_public_resolution_is_allowed() -> None:
    DEFAULT.assert_addresses_allowed("example.com", 443, ["93.184.216.34", "2606:2800::1"])


@pytest.mark.parametrize(
    "addresses",
    [
        ["127.0.0.1"],
        ["10.0.0.5"],
        ["::1"],
        ["169.254.169.254"],
        ["93.184.216.34", "127.0.0.1"],  # mixed answer: one bad address poisons the set
        ["127.0.0.1", "93.184.216.34"],
        ["not-an-ip"],
        [],
    ],
)
def test_hostnames_resolving_to_unsafe_addresses_fail_closed(addresses: list[str]) -> None:
    with pytest.raises(TargetNotAllowedError):
        DEFAULT.assert_addresses_allowed("example.com", 443, addresses)


# --- Development allowlist --------------------------------------------------------------


def test_allowlist_permits_private_addresses_for_that_host_only() -> None:
    policy = TargetPolicy(frozenset({"demo-target"}))
    policy.assert_addresses_allowed("demo-target", 8080, ["172.18.0.5"])
    policy.assert_addresses_allowed("DEMO-TARGET.", 8080, ["172.18.0.5"])
    with pytest.raises(TargetNotAllowedError):
        policy.assert_addresses_allowed("other-host", 8080, ["172.18.0.5"])


def test_allowlist_with_port_is_scoped_to_that_port() -> None:
    policy = TargetPolicy(frozenset({"demo-target:8080"}))
    policy.assert_addresses_allowed("demo-target", 8080, ["172.18.0.5"])
    with pytest.raises(TargetNotAllowedError):
        policy.assert_addresses_allowed("demo-target", 9090, ["172.18.0.5"])


@pytest.mark.parametrize(
    "address", ["169.254.169.254", "fe80::1", "fd00:ec2::254", "224.0.0.1", "0.0.0.0"]
)
def test_allowlist_never_permits_metadata_link_local_or_multicast(address: str) -> None:
    policy = TargetPolicy(frozenset({"demo-target"}))
    with pytest.raises(TargetNotAllowedError):
        policy.assert_addresses_allowed("demo-target", 80, [address])


def test_allowlist_does_not_exempt_a_public_name_that_resolves_to_metadata() -> None:
    policy = TargetPolicy(frozenset({"127.0.0.1"}))
    with pytest.raises(TargetNotAllowedError):
        policy.assert_addresses_allowed("evil.example", 80, ["169.254.169.254"])


def test_allowlisted_hosts_may_be_registered_by_name_or_loopback_literal() -> None:
    policy = TargetPolicy(frozenset({"localhost:8000", "demo-target"}))
    assert policy.validate_url("http://localhost:8000/health").startswith("http://localhost")
    assert policy.validate_url("http://demo-target/").startswith("http://demo-target")
    with pytest.raises(TargetNotAllowedError):
        policy.validate_url("http://localhost:9999/")  # port not on the allowlist


# --- Configuration guard ----------------------------------------------------------------


@pytest.mark.parametrize("environment", ["production", "staging"])
def test_allowlist_is_rejected_in_deployed_environments(environment: str) -> None:
    with pytest.raises(ValueError, match="development-only"):
        Settings(
            _env_file=None, environment=environment, monitoring_allowed_local_hosts="demo-target"
        )


@pytest.mark.parametrize("environment", ["local", "development", "test"])
def test_allowlist_is_accepted_in_development_environments(environment: str) -> None:
    settings = Settings(
        _env_file=None, environment=environment, monitoring_allowed_local_hosts="Demo-Target, a:80"
    )
    assert settings.monitoring_allowed_local_hosts == ["demo-target", "a:80"]


def test_allowlist_is_empty_by_default_so_default_policy_is_strict() -> None:
    assert Settings(_env_file=None).monitoring_allowed_local_hosts == []


def test_blank_allowlist_env_value_means_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONITORING_ALLOWED_LOCAL_HOSTS", "")
    assert Settings(_env_file=None, environment="production").monitoring_allowed_local_hosts == []
