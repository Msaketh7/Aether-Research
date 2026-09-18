"""Which address the request actually came from.

Two things use this answer and both of them are security controls: the rate
limiter keys a bucket by it, and the audit log records it. Getting it wrong in
either direction is a real failure - trust the header too readily and a caller
picks their own rate-limit bucket and writes fiction into the audit trail;
ignore it behind a load balancer and every request in the deployment shares one
address, which makes the limiter a global throttle and the audit log useless.

So the number of trusted hops is *declared*, not detected. ``TRUSTED_PROXY_HOPS
= 0`` - the default - means the socket peer is the client and
``X-Forwarded-For`` is not read at all. ``1`` means exactly one trusted proxy
appends to the header, so the client is the entry *one from the right*: each
proxy appends the address it saw, therefore the rightmost entry is what the
nearest trusted proxy observed, and everything left of the trusted hops is
whatever the client chose to send.
"""

from __future__ import annotations

import ipaddress

from starlette.requests import Request

FORWARDED_FOR = "x-forwarded-for"

#: Entries beyond this are not parsed. A header can be arbitrarily long, and a
#: chain of a thousand proxies is an attempt to make this function expensive.
MAX_FORWARDED_ENTRIES = 20


def client_address(request: Request, *, trusted_hops: int) -> str | None:
    """The caller's address, or ``None`` when there is no usable one.

    ``None`` is a real answer: an ASGI transport in a test has no peer, and a
    request over a Unix socket has no address either. Callers treat it as an
    identity of its own rather than inventing one.
    """
    peer = request.client.host if request.client else None

    if trusted_hops <= 0:
        return _valid(peer)

    raw = request.headers.get(FORWARDED_FOR)
    if not raw:
        # The header is absent although a proxy was declared. That is a
        # misconfiguration or a request that reached the process directly; the
        # peer is the only thing actually observed, so it is the honest answer.
        return _valid(peer)

    chain = [entry.strip() for entry in raw.split(",")[:MAX_FORWARDED_ENTRIES] if entry.strip()]
    # Count from the right: index -1 is what the nearest proxy saw, -2 what the
    # one before it saw, and so on outwards through the trusted hops.
    index = len(chain) - trusted_hops
    if 0 <= index < len(chain):
        return _valid(chain[index]) or _valid(peer)
    # A chain shorter than the declared hops means the request did not traverse
    # them. Nothing in it was appended by a trusted proxy, so none of it is
    # evidence.
    return _valid(peer)


def _valid(address: str | None) -> str | None:
    """Keep only something that is genuinely an IP address.

    An `X-Forwarded-For` entry is attacker-controlled text. It ends up in an
    ``inet`` column and in a rate-limit key, and neither should be asked to
    hold ``'; DROP`` or a megabyte of anything.
    """
    if not address:
        return None
    candidate = address.strip()
    # A proxy may write `[2001:db8::1]:443` or `203.0.113.7:51234`.
    if candidate.startswith("[") and "]" in candidate:
        candidate = candidate[1 : candidate.index("]")]
    elif candidate.count(":") == 1:
        candidate = candidate.split(":", 1)[0]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None
