"""Resolve client addresses without trusting spoofable proxy headers by default."""

from ipaddress import ip_address, ip_network

from fastapi import Request

from app.core.config import settings


def _trusted_proxy_networks():
    configured = settings.trusted_proxy_cidrs or ""
    networks = []
    for value in configured.split(","):
        candidate = value.strip()
        if candidate:
            networks.append(ip_network(candidate, strict=False))
    return tuple(networks)


def _is_trusted(address, networks) -> bool:
    return any(address in network for network in networks)


def get_client_ip(request: Request) -> str | None:
    """Return the effective client IP for security controls and audit events.

    Proxy headers are considered only when the socket peer belongs to an
    explicitly configured trusted proxy CIDR. The chain is then traversed
    from right to left, returning the first untrusted hop. Malformed chains
    fail closed to the socket peer.
    """

    peer_host = request.client.host if request.client else None
    if not peer_host:
        return None

    try:
        peer = ip_address(peer_host)
        networks = _trusted_proxy_networks()
    except ValueError:
        return peer_host

    if not networks or not _is_trusted(peer, networks):
        return peer_host

    forwarded_for = request.headers.get("x-forwarded-for")
    if not forwarded_for:
        return peer_host

    try:
        chain = [
            ip_address(value.strip())
            for value in forwarded_for.split(",")
            if value.strip()
        ]
    except ValueError:
        return peer_host

    if not chain:
        return peer_host

    for address in reversed(chain):
        if not _is_trusted(address, networks):
            return str(address)

    return str(chain[0])
