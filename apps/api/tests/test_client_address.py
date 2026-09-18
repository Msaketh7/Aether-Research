"""Which address a request came from (Phase 20).

Two security controls depend on this answer: the rate limiter keys a bucket by
it, and the audit log records it. Both directions of getting it wrong are real
failures, so both are tested - trusting the header too readily lets a caller
pick their own bucket and write fiction into the audit trail, and ignoring it
behind a load balancer makes every request in the deployment share one address.
"""

from __future__ import annotations

import pytest
from starlette.requests import Request

from app.security.forwarded import client_address

PEER = "198.51.100.9"


def _request(headers: dict[str, str] | None = None, *, peer: str | None = PEER) -> Request:
    scope: dict[str, object] = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [
            (key.lower().encode(), value.encode()) for key, value in (headers or {}).items()
        ],
        "client": (peer, 51234) if peer else None,
    }
    return Request(scope)


def test_with_no_declared_proxy_the_header_is_not_read_at_all():
    """The default. A caller that can set `X-Forwarded-For` would otherwise
    choose its own rate-limit bucket by changing one header per request, which
    is the same as having no limit."""
    request = _request({"x-forwarded-for": "203.0.113.1"})

    assert client_address(request, trusted_hops=0) == PEER


def test_with_one_declared_proxy_the_rightmost_entry_is_the_client():
    """Each proxy appends the address it saw, so with exactly one trusted hop
    in front the last entry is what that proxy observed - the genuine peer.
    Everything to the left of it is whatever the client chose to send."""
    request = _request({"x-forwarded-for": "10.0.0.1, 203.0.113.7"})

    assert client_address(request, trusted_hops=1) == "203.0.113.7"


def test_with_two_declared_proxies_the_count_is_taken_from_the_right():
    request = _request({"x-forwarded-for": "9.9.9.9, 203.0.113.7, 172.16.0.5"})

    assert client_address(request, trusted_hops=2) == "203.0.113.7"


def test_a_chain_shorter_than_the_declared_hops_is_not_evidence():
    """A request that did not traverse the declared proxies carries nothing a
    trusted hop appended, so nothing in the header is evidence of anything."""
    request = _request({"x-forwarded-for": "203.0.113.7"})

    assert client_address(request, trusted_hops=3) == PEER


def test_a_missing_header_behind_a_declared_proxy_falls_back_to_the_peer():
    assert client_address(_request(), trusted_hops=1) == PEER


@pytest.mark.parametrize("raw", ["not-an-address", "'; DROP TABLE audit_log; --", "x" * 5000])
def test_an_entry_that_is_not_an_address_is_discarded(raw: str):
    """The value ends up in an `inet` column and in a rate-limit key. Neither
    should be asked to hold arbitrary attacker-controlled text."""
    request = _request({"x-forwarded-for": f"10.0.0.1, {raw}"})

    assert client_address(request, trusted_hops=1) == PEER


def test_an_empty_entry_does_not_occupy_a_position():
    """A client can send an empty `X-Forwarded-For`, and the proxy then appends
    to it - so the header arrives with a blank entry that no hop wrote. Blanks
    are dropped before counting, which leaves the rightmost entry where it
    belongs: the address the trusted proxy actually observed."""
    request = _request({"x-forwarded-for": ", 203.0.113.7"})

    assert client_address(request, trusted_hops=1) == "203.0.113.7"


def test_a_port_suffix_is_stripped():
    """Some proxies write `203.0.113.7:51234`, and `inet` will not take it."""
    request = _request({"x-forwarded-for": "10.0.0.1, 203.0.113.7:51234"})

    assert client_address(request, trusted_hops=1) == "203.0.113.7"


def test_a_bracketed_ipv6_address_is_unwrapped():
    request = _request({"x-forwarded-for": "10.0.0.1, [2001:db8::1]:443"})

    assert client_address(request, trusted_hops=1) == "2001:db8::1"


def test_a_very_long_chain_is_not_parsed_in_full():
    """A header can be arbitrarily long; a thousand entries is an attempt to
    make this function expensive rather than a deployment with a thousand
    proxies."""
    request = _request({"x-forwarded-for": ", ".join(["10.0.0.1"] * 1000)})

    assert client_address(request, trusted_hops=1) == "10.0.0.1"


def test_a_transport_with_no_peer_has_no_address():
    """`None` is a real answer - an in-process ASGI transport has no peer -
    rather than something to invent a value for."""
    assert client_address(_request(peer=None), trusted_hops=0) is None
