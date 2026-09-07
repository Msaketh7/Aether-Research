"""URL validation, the SSRF guard, and canonicalisation.

This is the security core of Phase 6. Threat model 3.2: a search result or an
injected instruction points the fetcher at ``http://169.254.169.254/latest/
meta-data/`` and the worker helpfully retrieves its own cloud credentials.

The guard has four layers, and each exists because the one before it can be
defeated:

1. **Scheme, credentials and port checks.** ``file://`` and ``gopher://`` are
   refused outright; so are URLs carrying ``user:password@``, which leak into
   logs and referrers; so are ports that belong to protocols HTTP can be
   smuggled into.
2. **DNS resolution before the request.** ``localhost`` is obvious, but
   ``evil.example.com`` resolving to ``127.0.0.1`` is not, and neither is a
   decimal-encoded address like ``http://2130706433/``. So the *name* is never
   trusted - the resolved addresses are.
3. **Every resolved address is checked, not the first.** A hostile resolver can
   answer with one public address and one private one; a client that validates
   only ``addresses[0]`` and then lets the connection layer pick will eventually
   pick the other.
4. **The connected peer is verified after connecting** (in ``http.py``). Between
   step 2 and the TCP connection, DNS can change - classic rebinding. Because
   ``httpx2`` exposes the real peer address, the fetcher can compare it against
   the set validated here and abort before reading a byte of the body.

Redirects are followed manually so that every hop repeats all four layers. A
client with ``follow_redirects=True`` validates the first URL and then follows a
``302`` to anywhere, which is the single most common way this control is
bypassed in practice.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.core.logging import get_logger
from app.sources.errors import UrlRefused

logger = get_logger(__name__)

IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

#: The only schemes a research tool may fetch. An allowlist, not a denylist:
#: the set of URL schemes is open-ended and new ones keep arriving.
ALLOWED_SCHEMES = frozenset({"http", "https"})

#: Ports that speak a protocol an HTTP request can be smuggled into, or that
#: front a datastore. Blocking them is defence in depth - the address checks
#: already stop the internal cases - but it costs nothing for research traffic,
#: which lives on 80, 443 and the occasional 8080.
BLOCKED_PORTS = frozenset(
    {
        22,  # ssh
        23,  # telnet
        25,  # smtp
        110,  # pop3
        143,  # imap
        465,  # smtps
        587,  # submission
        1433,  # mssql
        3306,  # mysql
        3389,  # rdp
        5432,  # postgres
        6379,  # redis
        9200,  # elasticsearch
        11211,  # memcached
        27017,  # mongodb
    }
)

#: Extra ranges that ``ipaddress`` does not flag but that must never be reached.
#: The first is the cloud metadata service - AWS, GCP and Azure all answer on
#: 169.254.169.254 - and it is the single highest-value SSRF target in a cloud
#: deployment. It is inside link-local, which is already blocked, but it is
#: named here so the intent survives a refactor of the checks below.
_EXTRA_BLOCKED_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("169.254.169.254/32"),  # cloud metadata (AWS/GCP/Azure)
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT (RFC 6598)
    ipaddress.ip_network("192.0.0.0/24"),  # IETF protocol assignments
    ipaddress.ip_network("198.18.0.0/15"),  # benchmarking (RFC 2544)
    ipaddress.ip_network("::ffff:0:0/96"),  # IPv4-mapped IPv6, checked separately
)

#: Tracking parameters stripped during canonicalisation. Two identical articles
#: whose URLs differ only by a campaign tag are one source, not two, and
#: Phase 7's deduplication depends on that being true (TDD 9.3).
_TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "gclid",
        "fbclid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "igshid",
        "ref",
        "ref_src",
        "spm",
        "_ga",
        "yclid",
    }
)

_DEFAULT_PORTS = {"http": 80, "https": 443}

#: Hostnames refused by name, before DNS. The resolved-address check would catch
#: these anyway on a sane machine - but only if the machine's resolver is sane,
#: and a compromised or misconfigured `hosts` file is exactly the case where a
#: name-level check earns its keep. It also produces a clearer refusal reason.
_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "metadata",
        "metadata.google.internal",
        "instance-data",
    }
)

#: A hostname label. Deliberately strict: no underscores, no leading hyphen.
_LABEL = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$")


@dataclass(frozen=True, slots=True)
class ValidatedUrl:
    """A URL that passed every check, and the addresses it resolved to.

    Carrying the addresses is the point: the fetcher compares the peer it
    actually connected to against this set, which is what makes DNS rebinding
    detectable rather than theoretical.
    """

    url: str
    scheme: str
    host: str
    port: int
    addresses: tuple[IpAddress, ...]

    def permits(self, peer: str) -> bool:
        """Whether an address the transport actually connected to was approved."""
        try:
            resolved = ipaddress.ip_address(peer)
        except ValueError:
            return False
        return any(resolved == address for address in self.addresses)


def is_blocked_address(address: IpAddress) -> bool:
    """Whether an address is one a research fetch must never reach.

    Leans on ``ipaddress``'s own classification rather than a hand-written CIDR
    list, because that list is where these guards usually go wrong - someone
    remembers 10/8 and 192.168/16 and forgets 172.16/12, or covers IPv4 and
    forgets that ``::1`` and ``fc00::/7`` exist.
    """
    # An IPv4 address expressed as IPv6 (::ffff:127.0.0.1) is the same host and
    # must be judged as the IPv4 address it wraps.
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped

    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        return True

    return any(address in network for network in _EXTRA_BLOCKED_NETWORKS)


async def resolve(host: str, port: int) -> tuple[IpAddress, ...]:
    """Resolve a hostname to every address it answers with.

    ``getaddrinfo`` blocks, so it runs in a thread: a DNS lookup that stalls the
    event loop stalls every other research task in the process.
    """
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UrlRefused(
            "dns_resolution_failed",
            message="That host could not be resolved.",
            context={"host": host, "error": str(exc)},
        ) from exc

    addresses: list[IpAddress] = []
    for info in infos:
        sockaddr = info[4]
        try:
            address = ipaddress.ip_address(sockaddr[0])
        except ValueError:  # pragma: no cover - getaddrinfo returns literals
            continue
        if address not in addresses:
            addresses.append(address)

    if not addresses:
        raise UrlRefused(
            "dns_resolution_empty",
            message="That host could not be resolved.",
            context={"host": host},
        )
    return tuple(addresses)


async def validate_url(
    raw: str,
    *,
    allowed_domains: frozenset[str] | None = None,
    blocked_domains: frozenset[str] = frozenset(),
) -> ValidatedUrl:
    """Check a URL and resolve it, or refuse it with a reason.

    Raises ``UrlRefused`` for anything that fails. The reason string is stable
    and greppable, because these are security events and someone will one day
    need to count them by cause.
    """
    parts = urlsplit(raw.strip())

    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise _refuse("scheme_not_allowed", raw, scheme=parts.scheme)

    # Credentials in a URL leak through logs, referrers and error messages, and
    # are also a classic way to disguise the real host: http://good.com@evil.com
    if parts.username or parts.password:
        raise _refuse("credentials_in_url", raw)

    host = (parts.hostname or "").strip().rstrip(".").lower()
    if not host:
        raise _refuse("missing_host", raw)

    try:
        port = parts.port or _DEFAULT_PORTS[parts.scheme.lower()]
    except ValueError as exc:  # a non-numeric port
        raise _refuse("invalid_port", raw) from exc

    if port in BLOCKED_PORTS:
        raise _refuse("port_not_allowed", raw, port=port)

    if host in _BLOCKED_HOSTNAMES or host.endswith(".localhost") or host.endswith(".internal"):
        raise _refuse("blocked_hostname", raw, host=host)

    _check_domain_policy(host, raw, allowed_domains, blocked_domains)

    # An IP literal skips DNS entirely, including decimal and hex forms that do
    # not look like addresses: http://2130706433/ is http://127.0.0.1/.
    addresses: tuple[IpAddress, ...]
    literal = _as_ip_literal(host)
    if literal is not None:
        if is_blocked_address(literal):
            raise _refuse("blocked_address", raw, address=str(literal))
        addresses = (literal,)
    else:
        if not _is_plausible_hostname(host):
            raise _refuse("invalid_hostname", raw, host=host)
        addresses = await resolve(host, port)
        # Every address, not the first: a hostile resolver can return one public
        # address and one private one, and the connection layer picks.
        for address in addresses:
            if is_blocked_address(address):
                raise _refuse("blocked_address", raw, host=host, address=str(address))

    return ValidatedUrl(
        url=urlunsplit((parts.scheme.lower(), parts.netloc, parts.path or "/", parts.query, "")),
        scheme=parts.scheme.lower(),
        host=host,
        port=port,
        addresses=addresses,
    )


def canonicalize(raw: str) -> str:
    """A stable form of a URL, for deduplication (TDD 9.3).

    Lowercases scheme and host, drops the default port, strips the fragment and
    known tracking parameters, and sorts what remains. Two URLs that differ only
    by a campaign tag become one string, so ten reposts of a wire story are one
    source rather than ten - which matters because corroboration is counted per
    source and inflating it inflates confidence.

    Deliberately conservative: query parameters that are not known tracking tags
    are kept, because ``?id=42`` usually *is* the article.
    """
    parts = urlsplit(raw.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").rstrip(".").lower()

    netloc = host
    if parts.port and parts.port != _DEFAULT_PORTS.get(scheme):
        netloc = f"{host}:{parts.port}"

    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key.lower() not in _TRACKING_PARAMS
        )
    )

    path = parts.path or "/"
    # A trailing slash on a non-root path is almost never meaningful.
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    return urlunsplit((scheme, netloc, path, query, ""))


def registrable_domain(host: str) -> str:
    """A rough eTLD+1, for domain-diversity and reputation checks.

    Rough on purpose: a full public-suffix list is a dependency and a data file
    that goes stale, and the callers here only need "are these two URLs from the
    same place" rather than a legally precise answer.
    """
    labels = host.rstrip(".").lower().split(".")
    if len(labels) <= 2:
        return ".".join(labels)
    # Handles the common two-part suffixes (co.uk, com.au) without a full list.
    if len(labels[-1]) == 2 and len(labels[-2]) <= 3:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


# --- internals ------------------------------------------------------------


def _refuse(reason: str, url: str, **context: object) -> UrlRefused:
    """Build the refusal, logging it as the security event it is."""
    logger.warning(
        "url refused by the ssrf guard",
        extra={"reason": reason, "url": url[:200], **context},
    )
    return UrlRefused(reason, context={"url": url[:200], "reason": reason, **context})


def _check_domain_policy(
    host: str,
    raw: str,
    allowed_domains: frozenset[str] | None,
    blocked_domains: frozenset[str],
) -> None:
    """Per-deployment allow and deny lists (TDD 15.2).

    Matching is on the registrable domain and on suffixes, so blocking
    ``example.com`` also blocks ``tracker.example.com``.
    """
    if any(host == blocked or host.endswith(f".{blocked}") for blocked in blocked_domains):
        raise _refuse("domain_blocked", raw, host=host)

    if allowed_domains is not None and not any(
        host == allowed or host.endswith(f".{allowed}") for allowed in allowed_domains
    ):
        raise _refuse("domain_not_allowed", raw, host=host)


def _as_ip_literal(host: str) -> IpAddress | None:
    """Parse a host as an IP address, including every encoding `inet_aton` takes.

    ``ipaddress.ip_address`` only accepts the canonical dotted quad, so a guard
    built on it alone lets all of these through to DNS - where a resolver
    resolves them perfectly well:

        http://2130706433/    decimal
        http://0x7f000001/    hex
        http://127.1/         short form: a.(24-bit)
        http://0177.0.0.1/    octal

    These are not exotic; they are the standard SSRF filter bypass list. So the
    historical ``inet_aton`` grammar is implemented here rather than delegated:
    one to four parts, each decimal, octal (leading zero) or hex (``0x``), with
    the final part absorbing the remaining bytes.
    """
    stripped = host.strip("[]")
    try:
        return ipaddress.ip_address(stripped)
    except ValueError:
        pass

    parts = stripped.split(".")
    if not 1 <= len(parts) <= 4:
        return None

    values: list[int] = []
    for part in parts:
        parsed = _parse_octet(part)
        if parsed is None:
            return None
        values.append(parsed)

    # The last part absorbs whatever bytes the earlier ones did not name:
    # `127.1` is 127.0.0.1, `192.168.257` is 192.168.1.1.
    leading, last = values[:-1], values[-1]
    remaining_bytes = 4 - len(leading)
    if last >= 1 << (8 * remaining_bytes):
        return None
    if any(value > 0xFF for value in leading):
        return None

    packed = 0
    for value in leading:
        packed = (packed << 8) | value
    packed = (packed << (8 * remaining_bytes)) | last

    try:
        return ipaddress.IPv4Address(packed)
    except (ipaddress.AddressValueError, ValueError):
        return None


def _parse_octet(part: str) -> int | None:
    """One component of an ``inet_aton`` address: decimal, octal or hex."""
    if not part:
        return None
    lowered = part.lower()
    try:
        if lowered.startswith("0x"):
            return int(lowered, 16)
        if lowered.startswith("0") and len(lowered) > 1:
            return int(lowered, 8)
        if not lowered.isdigit():
            return None
        return int(lowered, 10)
    except ValueError:
        return None


def _is_plausible_hostname(host: str) -> bool:
    """Reject hostnames that cannot be real before spending a DNS lookup.

    A single-label host is an intranet name - `wiki`, `jenkins`, `grafana` - and
    a research fetcher has no business resolving one. Requiring a dot is a
    cheaper and more complete rule than trying to enumerate them.
    """
    if len(host) > 253:
        return False
    if "." not in host:
        return False
    return all(_LABEL.match(label) for label in host.split("."))
