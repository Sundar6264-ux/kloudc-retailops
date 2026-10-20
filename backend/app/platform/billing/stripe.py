"""Stripe billing - STUB until the owner opens a Stripe account (owner action 3).

Builds the requests Stripe's API expects (form-encoded, bearer secret key,
idempotency keys) and hands them to a transport. The default transport sends
over HTTPS with the standard library; tests always pass a fake one. Prices
per plan come from ``STRIPE_PRICE_<PLAN>`` (e.g. ``STRIPE_PRICE_STARTER``);
a plan without a price is not billed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Optional, Tuple

API = "https://api.stripe.com/v1"
# (method, url, headers, form body) -> (status, json body)
Transport = Callable[[str, str, Dict[str, str], Dict[str, str]], Tuple[int, Dict[str, Any]]]
TOLERANCE_SECONDS = 300


class StripeError(RuntimeError):
    pass


def _https_transport(method: str, url: str, headers: Dict[str, str], form: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    data = urllib.parse.urlencode(form).encode() if form else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 - fixed https host
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def verify_webhook(payload: bytes, signature: str, secret: str, *, now: Optional[float] = None,
                   tolerance: int = TOLERANCE_SECONDS) -> Dict[str, Any]:
    """Check a ``Stripe-Signature`` header (t=..., v1=...) and return the event."""
    parts: Dict[str, list] = {}
    for item in (signature or "").split(","):
        k, _, v = item.strip().partition("=")
        parts.setdefault(k, []).append(v)
    try:
        ts = int(parts.get("t", [""])[0])
    except ValueError:
        raise StripeError("bad signature header") from None
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, v) for v in parts.get("v1", [])):
        raise StripeError("signature does not match")
    if abs((now if now is not None else time.time()) - ts) > tolerance:
        raise StripeError("signature too old")
    return json.loads(payload or b"{}")


class StripeBilling:
    name = "stripe"

    def __init__(self, secret_key: str, webhook_secret: str, *, transport: Optional[Transport] = None):
        if not secret_key or not webhook_secret:
            raise StripeError("Stripe needs STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET")
        self._key = secret_key
        self._webhook_secret = webhook_secret
        self._send = transport or _https_transport

    def _headers(self, idempotency_key: Optional[str] = None) -> Dict[str, str]:
        h = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/x-www-form-urlencoded",
             "Stripe-Version": "2024-06-20"}
        if idempotency_key:
            h["Idempotency-Key"] = idempotency_key
        return h

    def _call(self, method: str, path: str, form: Dict[str, str], idem: Optional[str] = None) -> Dict[str, Any]:
        status, body = self._send(method, f"{API}{path}", self._headers(idem), form)
        if status >= 400:
            msg = (body.get("error") or {}).get("message") if isinstance(body, dict) else None
            raise StripeError(f"Stripe answered {status}: {msg or 'error'}")
        return body

    @staticmethod
    def price_for(plan: str) -> Optional[str]:
        return (os.environ.get(f"STRIPE_PRICE_{plan.upper()}") or "").strip() or None

    def customer_payload(self, store_code: str, *, name: str, email: Optional[str] = None) -> Dict[str, str]:
        form = {"name": name, "metadata[store_id]": store_code}
        if email:
            form["email"] = email
        return form

    def ensure_customer(self, store_code: str, *, name: str, email: Optional[str] = None) -> Optional[str]:
        body = self._call("POST", "/customers", self.customer_payload(store_code, name=name, email=email),
                          idem=f"customer-{store_code}")
        return body.get("id")

    def plan_changed(self, store_code: str, plan: str) -> Dict[str, Any]:
        price = self.price_for(plan)
        if price is None:
            return {"provider": "stripe", "sent": False, "reason": f"no price for plan {plan}"}
        form = {"metadata[store_id]": store_code, "metadata[plan]": plan, "items[0][price]": price}
        body = self._call("POST", "/subscriptions", form, idem=f"plan-{store_code}-{plan}")
        return {"provider": "stripe", "sent": True, "id": body.get("id")}

    def handle_webhook(self, payload: bytes, signature: str) -> Dict[str, Any]:
        event = verify_webhook(payload, signature, self._webhook_secret)
        return {"provider": "stripe", "handled": True, "type": event.get("type")}
