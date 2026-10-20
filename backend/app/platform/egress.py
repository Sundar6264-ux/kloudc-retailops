"""Outgoing connections to addresses a store typed (docs/SECURITY_REVIEW.md SR-08).

A store admin can point Toast (``api_base``), WhatsApp (``base_url``) and
SMTP (``host``, ``port``) at any public address. On the shared service those
connections run on the platform's own machine, so a name that resolves to
the machine itself or its private network would let one store reach - or
port-scan - what only the server should see. ``credentials.host_problem``
refuses private addresses typed as IP literals when they are saved and the
connection test checks names after DNS; these checks run again on every real
connection (nightly syncs, sends), after DNS, so a name changed to a private
address later is refused too.

Only on the shared service (``TENANCY_MODE=multi``): a single-store server's
settings come from its own owner. The POS vendors' own hosts are not looked
up again.
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlsplit

TRUSTED_SUFFIXES = ("toasttab.com", "clover.com", "whapi.cloud")


class EgressRefused(OSError):
    """The address resolves to a private or local network."""


def _trusted(host: str) -> bool:
    h = (host or "").lower().rstrip(".")
    return any(h == s or h.endswith("." + s) for s in TRUSTED_SUFFIXES)


def check_host(host: Optional[str], port: int = 443) -> None:
    """Raise EgressRefused when ``host`` is private (shared service only)."""
    from app.platform.config import is_multi

    if not host or not is_multi() or _trusted(host):
        return
    from app.platform.connector_tests import NOT_ALLOWED, private_target

    if private_target(host, int(port or 443)):
        raise EgressRefused(NOT_ALLOWED)


def check_url(url: str) -> str:
    """``url`` when its host may be reached; else httpx.ConnectError."""
    import httpx

    parts = urlsplit(str(url or ""))
    try:
        check_host(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    except EgressRefused as exc:
        raise httpx.ConnectError(str(exc)) from None
    return url


def httpx_request_hook(request) -> None:
    """``httpx.Client(event_hooks={"request": [...]})``: every request the
    client sends goes through ``check_url``."""
    check_url(str(request.url))


__all__ = ("EgressRefused", "check_host", "check_url", "httpx_request_hook")
