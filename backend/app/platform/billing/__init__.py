"""Billing provider interface (docs/WEBSITE_READY_CONTRACT.md B3; track B).

``BILLING_PROVIDER`` picks it: ``none`` (the default, ``NullBilling``: does
nothing) or ``stripe`` (``StripeBilling``: a stub that builds Stripe API
requests and sends them through a transport; it needs ``STRIPE_SECRET_KEY``
and ``STRIPE_WEBHOOK_SECRET`` and refuses to start without them).

Nothing here is ever called in tests against the real Stripe: tests give
``StripeBilling`` a fake transport. Plans stay soft limits; billing never
blocks a store's work.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Protocol

from app.platform.billing.null import NullBilling
from app.platform.billing.stripe import StripeBilling, StripeError, verify_webhook


class BillingConfigError(RuntimeError):
    """BILLING_PROVIDER is set to something that cannot work."""


class BillingProvider(Protocol):
    name: str

    def ensure_customer(self, store_code: str, *, name: str, email: Optional[str] = None) -> Optional[str]: ...

    def plan_changed(self, store_code: str, plan: str) -> Dict[str, Any]: ...

    def handle_webhook(self, payload: bytes, signature: str) -> Dict[str, Any]: ...


PROVIDERS = ("none", "stripe")


def _setting(name: str) -> str:
    from app.platform.config import pget

    return str(pget(name) or "").strip()


def provider_name() -> str:
    return (_setting("billing_provider") or "none").lower()


def startup_check() -> None:
    """Refuse to start with a billing provider that cannot work."""
    name = provider_name()
    if name not in PROVIDERS:
        raise BillingConfigError(f"BILLING_PROVIDER={name!r}: use one of {', '.join(PROVIDERS)}.")
    if name == "stripe":
        missing = [n.upper() for n in ("stripe_secret_key", "stripe_webhook_secret") if not _setting(n)]
        if missing:
            raise BillingConfigError(f"BILLING_PROVIDER=stripe needs {' and '.join(missing)}.")


def get_billing(transport=None) -> BillingProvider:
    startup_check()
    if provider_name() == "stripe":
        return StripeBilling(_setting("stripe_secret_key"), _setting("stripe_webhook_secret"), transport=transport)
    return NullBilling()


__all__ = (
    "BillingConfigError", "BillingProvider", "NullBilling", "PROVIDERS", "StripeBilling", "StripeError",
    "get_billing", "provider_name", "startup_check", "verify_webhook",
)
