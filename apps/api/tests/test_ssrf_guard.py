"""The SSRF guard.

The most security-sensitive file in the project so far, and the one where a
passing test suite is least reassuring on its own - a guard is only as good as
the bypasses someone thought to try. So these are written as *attacks*, not as
coverage: each case is a technique that works against a naive implementation,
and the docstring says which one.

Threat model 3.2 is the specification. The cases below cover its named targets
(cloud metadata, localhost, RFC 1918) plus the encodings and indirections that
are how those targets are actually reached in practice.
"""

from __future__ import annotations

import ipaddress

import pytest

from app.sources import canonicalize, is_blocked_address, registrable_domain, validate_url
from app.sources.errors import UrlRefused
from app.sources.urls import BLOCKED_PORTS


async def refusal(url: str) -> str:
    """Validate a URL that must be refused, and return why."""
    with pytest.raises(UrlRefused) as raised:
        await validate_url(url)
    return raised.value.reason


# --- the named targets ----------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
        "http://169.254.169.254/computeMetadata/v1/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://100.100.100.200/latest/meta-data/",
    ],
)
async def test_cloud_metadata_endpoints_are_refused(url: str):
    """The highest-value SSRF target in any cloud deployment: one successful
    fetch here hands over the worker's own credentials."""
    assert await refusal(url) in {"blocked_address", "blocked_hostname"}


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8000/admin",
        "http://127.0.0.1/",
        "http://127.1/",
        "http://0.0.0.0/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
    ],
)
async def test_loopback_is_refused_in_every_spelling(url: str):
    """`127.0.0.1` is the obvious one. `127.1`, `::1` and the IPv4-mapped IPv6
    form are the same host and are what a bypass actually uses."""
    assert await refusal(url) in {"blocked_address", "blocked_hostname"}


@pytest.mark.parametrize(
    "url",
    [
        "http://10.0.0.1/",
        "http://10.255.255.254/",
        "http://172.16.0.1/",
        "http://172.31.255.254/",  # the end of the range people forget
        "http://192.168.1.1/",
        "http://[fc00::1]/",  # IPv6 unique-local
        "http://[fe80::1]/",  # IPv6 link-local
    ],
)
async def test_private_ranges_are_refused(url: str):
    """Including 172.16/12, which is the range hand-written blocklists miss."""
    assert await refusal(url) == "blocked_address"


# --- encodings and indirections -------------------------------------------


@pytest.mark.parametrize(
    ("url", "note"),
    [
        ("http://2130706433/", "decimal-encoded 127.0.0.1"),
        ("http://0x7f000001/", "hex-encoded 127.0.0.1"),
        ("http://3232235777/", "decimal-encoded 192.168.1.1"),
        ("http://0177.0.0.1/", "octal-encoded 127.0.0.1"),
        ("http://0x7f.1/", "mixed hex and short form"),
        ("http://192.168.257/", "short form: the last part absorbs two bytes"),
    ],
)
async def test_numerically_encoded_addresses_are_refused(url: str, note: str):
    """`ipaddress.ip_address("2130706433")` raises, so a guard that only tries
    that function lets these through to DNS - where the resolver resolves them
    perfectly well."""
    assert await refusal(url) == "blocked_address", note


async def test_a_url_with_credentials_is_refused():
    """`http://good.example.com@evil.example.com/` reads as the first host to a
    human and resolves to the second. Credentials also leak into logs."""
    assert await refusal("http://good.example.com@127.0.0.1/") == "credentials_in_url"


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://127.0.0.1:6379/_SET%20key%20value",
        "ftp://example.com/",
        "data:text/html,<script>alert(1)</script>",
        "javascript:alert(1)",
        "jar:http://example.com!/",
    ],
)
async def test_non_http_schemes_are_refused(url: str):
    """An allowlist, not a denylist: gopher:// against a Redis port was a real
    RCE, and the set of schemes keeps growing."""
    assert await refusal(url) == "scheme_not_allowed"


async def test_a_single_label_host_is_refused():
    """`http://jenkins/` or `http://grafana/` resolves on a corporate network
    and nowhere else. A research fetcher has no business asking."""
    assert await refusal("http://jenkins/") == "invalid_hostname"


@pytest.mark.parametrize("port", sorted(BLOCKED_PORTS)[:6])
async def test_dangerous_ports_are_refused(port: int):
    """Defence in depth. The address checks already cover the internal cases;
    this covers protocol smuggling to a public host."""
    assert await refusal(f"http://example.com:{port}/") == "port_not_allowed"


# --- the address classifier ------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.20.0.1",
        "192.168.0.1",
        "169.254.169.254",
        "100.64.0.1",  # CGNAT
        "0.0.0.0",  # noqa: S104 - the unspecified address is the point of the case
        "224.0.0.1",  # multicast
        "::1",
        "fc00::1",
        "fe80::1",
        "::ffff:10.0.0.1",  # IPv4-mapped private
    ],
)
def test_blocked_addresses(address: str):
    assert is_blocked_address(ipaddress.ip_address(address)) is True


@pytest.mark.parametrize("address", ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700::1"])
def test_public_addresses_are_allowed(address: str):
    """A guard that blocks everything is not a guard, it is an outage."""
    assert is_blocked_address(ipaddress.ip_address(address)) is False


# --- domain policy ---------------------------------------------------------


async def test_a_blocked_domain_covers_its_subdomains():
    reason = await refusal_with(
        "https://tracker.evil.example/", blocked_domains=frozenset({"evil.example"})
    )
    assert reason == "domain_blocked"


async def test_an_allowlist_refuses_everything_outside_it():
    """What a locked-down deployment configures: research only these sites."""
    reason = await refusal_with(
        "https://example.com/", allowed_domains=frozenset({"sec.gov", "arxiv.org"})
    )
    assert reason == "domain_not_allowed"


async def refusal_with(url: str, **policy: object) -> str:
    with pytest.raises(UrlRefused) as raised:
        await validate_url(url, **policy)  # type: ignore[arg-type]
    return raised.value.reason


# --- the allowed path ------------------------------------------------------


async def test_a_public_url_is_allowed_and_carries_its_addresses():
    """The addresses come back so the fetcher can check the peer it actually
    connected to - which is what makes DNS rebinding detectable.

    Uses a real hostname and a real resolver, because the resolution step is
    part of what is being tested. Skips rather than fails without DNS: an
    offline machine has not found a bug.
    """
    try:
        validated = await validate_url("https://example.com/article?id=1")
    except UrlRefused as exc:
        if exc.reason.startswith("dns_resolution"):
            pytest.skip("no DNS available in this environment")
        raise

    assert validated.host == "example.com"
    assert validated.port == 443
    assert validated.addresses
    assert all(not is_blocked_address(address) for address in validated.addresses)


async def test_permits_recognises_an_approved_peer():
    """An IP literal, so this needs no resolver: what is under test is the
    comparison the fetcher makes after connecting."""
    validated = await validate_url("https://93.184.216.34/")

    assert validated.permits("93.184.216.34") is True
    assert validated.permits("127.0.0.1") is False
    assert validated.permits("not-an-address") is False


# --- canonicalisation ------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HTTPS://Example.COM/Path", "https://example.com/Path"),
        ("https://example.com:443/a", "https://example.com/a"),
        ("https://example.com/a/", "https://example.com/a"),
        ("https://example.com/a#section", "https://example.com/a"),
        ("https://example.com/a?utm_source=x&utm_medium=y", "https://example.com/a"),
        ("https://example.com/a?b=2&a=1", "https://example.com/a?a=1&b=2"),
        ("https://example.com/a?id=42&fbclid=z", "https://example.com/a?id=42"),
    ],
)
def test_canonicalisation(raw: str, expected: str):
    """Ten reposts of one wire story must count as one source. Corroboration is
    counted per source, so failing to collapse them inflates confidence."""
    assert canonicalize(raw) == expected


def test_canonicalisation_keeps_parameters_that_identify_the_article():
    """Conservative on purpose: `?id=42` usually *is* the article."""
    assert canonicalize("https://example.com/view?id=42") == "https://example.com/view?id=42"


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("www.example.com", "example.com"),
        ("example.com", "example.com"),
        ("a.b.example.co.uk", "example.co.uk"),
        ("sec.gov", "sec.gov"),
    ],
)
def test_registrable_domain(host: str, expected: str):
    assert registrable_domain(host) == expected
