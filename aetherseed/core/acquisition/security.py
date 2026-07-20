"""SSRF protection and URL egress policy.

Before any request is made, the target URL is validated:

1. Scheme must be ``http`` or ``https``.
2. The host is resolved and **every** resolved address is checked. If any
   resolves to a private, loopback, link-local, reserved, or multicast range the
   request is refused (blocks cloud metadata endpoints like ``169.254.169.254``).
3. If an egress allowlist is configured, the host/IP must match it.

This is the single choke point for outbound acquisition traffic.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

from aetherseed.config import Settings, get_settings
from aetherseed.errors import SSRFError

_ALLOWED_SCHEMES = {"http", "https"}


@dataclass(slots=True)
class ResolvedTarget:
    url: str
    scheme: str
    host: str
    port: int
    addresses: list[str]


def _is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _matches_allowlist(host: str, addresses: list[str], entries: list[str]) -> bool:
    if not entries:
        return True  # no allowlist => public internet is permitted
    for entry in entries:
        if host == entry or host.endswith("." + entry):
            return True
        try:
            net = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            continue
        for addr in addresses:
            try:
                if ipaddress.ip_address(addr) in net:
                    return True
            except ValueError:
                continue
    return False


def resolve_and_validate(url: str, settings: Settings | None = None) -> ResolvedTarget:
    """Validate ``url`` against the egress policy and return the resolved target.

    Raises
    ------
    SSRFError
        If the scheme is disallowed, the host cannot be resolved, any resolved
        address is non-public, or the target fails the allowlist.

    Examples
    --------
    >>> resolve_and_validate("http://127.0.0.1/")  # doctest: +IGNORE_EXCEPTION_DETAIL
    Traceback (most recent call last):
    SSRFError: ...
    """
    s = settings or get_settings()
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise SSRFError(
            f"scheme {scheme!r} not allowed", context={"url": url, "scheme": scheme}
        )
    host = parsed.hostname
    if not host:
        raise SSRFError("URL has no host", context={"url": url})
    port = parsed.port or (443 if scheme == "https" else 80)

    # Resolve every address the host maps to.
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise SSRFError(f"cannot resolve host {host!r}", context={"url": url}) from exc

    addresses = sorted({str(info[4][0]) for info in infos})
    if not addresses:
        raise SSRFError(f"host {host!r} resolved to no addresses", context={"url": url})

    entries = s.egress_allowlist_entries
    for addr in addresses:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError as exc:
            raise SSRFError(f"invalid resolved address {addr!r}", context={"url": url}) from exc
        # Private/loopback/link-local targets are denied by default, and permitted
        # only when *explicitly* named in the egress allowlist (opt-in for
        # internal crawling / local testing).
        if not _is_public(ip) and not (entries and _matches_allowlist(host, [addr], entries)):
            raise SSRFError(
                f"target resolves to non-public address {addr}",
                context={"url": url, "address": addr, "host": host},
            )

    if not _matches_allowlist(host, addresses, entries):
        raise SSRFError(
            f"host {host!r} not in egress allowlist",
            context={"url": url, "host": host},
        )

    return ResolvedTarget(
        url=url, scheme=scheme, host=host, port=port, addresses=addresses
    )
