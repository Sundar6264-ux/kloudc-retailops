"""One client-IP rule for every rate limit and login lockout (pre-production
2026-10-21, docs/PREPROD_PLAN.md 2.2 item 1).

The public address app.kloudc.com reaches the server through a Cloudflare
Tunnel: ``cloudflared`` on this machine connects to 127.0.0.1, so without
this rule every user in the world would share the IP 127.0.0.1 and one
store's Wi-Fi could lock everyone out.

Rule:

1. The TCP peer is the address the request came from (after uvicorn's own
   ``--proxy-headers`` handling, which already only trusts
   ``FORWARDED_ALLOW_IPS``).
2. The client-IP header (``DIRECTORY_CLIENT_IP_HEADER``, normally
   ``CF-Connecting-IP``; ``CF-Connecting-IP`` when blank) is used ONLY when
   that peer is a trusted proxy: an address or network listed in
   ``FORWARDED_ALLOW_IPS`` (default ``127.0.0.1``; ``::1`` always goes with
   ``127.0.0.1``; ``*`` trusts every peer). From anyone else the header is
   ignored, so a caller can't pick its own rate-limit key.
3. The header's value must be an IP address (the last entry of a comma list
   - the one the proxy itself wrote); anything else falls back to the peer.
4. As a rate-limit key an IPv6 address becomes its /64 (one household or
   phone gets a whole /64) and an IPv4-mapped IPv6 address its IPv4 form.

Used by login (``platform/throttle.client_ip``), store lookup, support login,
sign-up and the public checklist link (``services/checklist_public``).
"""

import ipaddress
import os
from typing import Iterable, List, Mapping, Optional, Union

DEFAULT_HEADER = "CF-Connecting-IP"
DEFAULT_TRUSTED = "127.0.0.1"

_Net = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]


def _header_name() -> str:
    try:
        from app.platform.config import pget

        name = str(pget("directory_client_ip_header") or "").strip()
    except Exception:  # noqa: BLE001 - before the platform settings exist
        name = ""
    return name or DEFAULT_HEADER


def trusted_proxies(raw: Optional[str] = None) -> Optional[List[_Net]]:
    """The networks in FORWARDED_ALLOW_IPS (None = ``*``, every peer)."""
    raw = os.environ.get("FORWARDED_ALLOW_IPS", DEFAULT_TRUSTED) if raw is None else raw
    nets: List[_Net] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        if part == "*":
            return None
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            continue  # a unix socket path or a typo: never trusted
    if any(n == ipaddress.ip_network("127.0.0.1/32") for n in nets):
        nets.append(ipaddress.ip_network("::1/128"))
    return nets


def _parse(value: Optional[str]):
    try:
        return ipaddress.ip_address((value or "").strip())
    except ValueError:
        return None


def is_trusted_peer(peer: Optional[str], trusted: Optional[Iterable[_Net]] = None) -> bool:
    nets = trusted_proxies() if trusted is None else trusted
    if nets is None:
        return True
    addr = _parse(peer)
    if addr is None:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return any(addr.version == n.version and addr in n for n in nets)


def limit_key(ip: str) -> str:
    """An IP as a rate-limit key: IPv6 -> its /64, IPv4-mapped -> IPv4."""
    addr = _parse(ip)
    if addr is None:
        return (ip or "unknown")[:64]
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None:
            return str(addr.ipv4_mapped)
        return str(ipaddress.IPv6Network(f"{addr}/64", strict=False))
    return str(addr)


def resolve(headers: Mapping[str, str], peer: Optional[str], *, header: Optional[str] = None,
            trusted: Optional[Iterable[_Net]] = None) -> str:
    """The client's IP (not yet a limit key): the header from a trusted
    proxy, else the peer, else "unknown"."""
    name = header or _header_name()
    if is_trusted_peer(peer, trusted):
        raw = headers.get(name) or headers.get(name.lower()) or ""
        last = raw.split(",")[-1].strip()
        addr = _parse(last)
        if addr is not None:
            return str(addr)
    return (peer or "unknown")[:64]


def client_ip(request) -> str:
    """For a FastAPI / Starlette request: the client's IP, as a rate-limit key."""
    peer = request.client.host if request.client else None
    return limit_key(resolve(request.headers, peer))
