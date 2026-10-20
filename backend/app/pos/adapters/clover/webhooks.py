"""Clover webhooks (website-ready track D6; plan 4.7 "Webhooks").

Off by default. Clover webhooks carry only ids, a type and a time stamp, and
are set up per Clover **app** (so a merchant API token store gets none), with
a static ``X-Clover-Auth`` header and no documented retries. They can only
make syncs sooner: the nightly pulls stay the main path.

Turning them on (owner steps in docs/CLOVER_OWNER_CHECKLIST.md):
``CLOVER_WEBHOOKS_ENABLED=true``, then in the Clover app set the webhook URL
to ``https://<service>/api/settings/integrations/clover/webhook``; Clover
POSTs ``{"verificationCode": ...}`` once - it is written to the server log
to paste back into Clover - and then shows the auth code to put in
``CLOVER_WEBHOOK_AUTH``.

A notification ``{"appId", "merchants": {mId: [{"objectId": "O:..",
"type": "CREATE|UPDATE|DELETE", "ts": ms}]}}`` is matched to the store(s)
connected to that merchant, and for an order (``O:``) or item (``I:``)
change queues ``clover_webhook_sync`` for that store: today's business date
is synced again (orders), or the catalog cache refreshed (items). At most
one sync per store and kind every ``DEBOUNCE_SECONDS`` in this process.
"""

import json
import logging
import threading
import time
from typing import Any, Dict, Iterable, List, Set, Tuple

from app.jobqueue import enqueue, register

logger = logging.getLogger(__name__)

JOB_KIND = "clover_webhook_sync"
DEBOUNCE_SECONDS = 60.0
MAX_MERCHANTS = 200

_last_lock = threading.Lock()
_last_run: Dict[Tuple[str, str], float] = {}  # global-state: debounce per (store code, what), process-wide


def enabled() -> bool:
    from app.platform.config import pget

    return str(pget("clover_webhooks_enabled") or "").strip().lower() in ("1", "true", "yes", "on")


def auth_code() -> str:
    from app.platform.config import pget

    return str(pget("clover_webhook_auth") or "").strip()


def stores_for_merchants(merchant_ids: Iterable[str]) -> Dict[str, List[str]]:
    """merchant id -> store codes whose saved Clover connection names it."""
    from sqlalchemy import select

    from app.platform.db import platform_session
    from app.platform.models import Store, StoreCredential

    wanted = {str(m).strip().upper() for m in merchant_ids if m}
    out: Dict[str, List[str]] = {}
    if not wanted:
        return out
    from app.platform.config import is_multi, pget

    if not is_multi():
        # One store per server: its own CLOVER_MERCHANT_ID ("" = this process).
        mine = str(pget("clover_merchant_id") or "").strip().upper()
        return {mine: [""]} if mine and mine in wanted else {}
    with platform_session() as s:
        rows = s.execute(
            select(Store.code, StoreCredential.public_fields)
            .join(StoreCredential, StoreCredential.store_id == Store.id)
            .where(StoreCredential.kind == "clover")
        ).all()
    for code, public_json in rows:
        try:
            mid = str((json.loads(public_json or "{}") or {}).get("merchant_id") or "").upper()
        except ValueError:
            continue
        if mid in wanted:
            out.setdefault(mid, []).append(code)
    return out


def changes(payload: Dict[str, Any]) -> Dict[str, Set[str]]:
    """merchant id -> {"orders", "items"} touched by this notification."""
    out: Dict[str, Set[str]] = {}
    merchants = payload.get("merchants") if isinstance(payload, dict) else None
    if not isinstance(merchants, dict):
        return out
    for mid, events in list(merchants.items())[:MAX_MERCHANTS]:
        kinds: Set[str] = set()
        for ev in events if isinstance(events, list) else []:
            oid = str((ev or {}).get("objectId") or "")
            if oid.startswith("O:"):
                kinds.add("orders")
            elif oid.startswith("I:"):
                kinds.add("items")
        if kinds:
            out[str(mid).upper()] = kinds
    return out


def _due(code: str, what: str, now: float) -> bool:
    with _last_lock:
        last = _last_run.get((code, what))
        if last is not None and now - last < DEBOUNCE_SECONDS:
            return False
        _last_run[(code, what)] = now
        return True


def handle(payload: Dict[str, Any], now: float = None) -> Dict[str, Any]:
    """Queue the syncs a notification asks for. Returns what was queued."""
    now = time.monotonic() if now is None else now
    touched = changes(payload)
    stores = stores_for_merchants(touched)
    queued: List[str] = []
    for mid, kinds in touched.items():
        for code in stores.get(mid, []):
            for what in sorted(kinds):
                if _due(code, what, now):
                    enqueue(JOB_KIND, {"what": what}, code=code or None, dedupe_key=f"{code}:{what}")
                    queued.append(f"{code}:{what}")
    return {"merchants": len(touched), "queued": queued}


def reset() -> None:
    with _last_lock:
        _last_run.clear()


@register(JOB_KIND, max_attempts=2, timeout_seconds=600)
def run_webhook_sync(payload: Dict[str, Any]) -> Dict[str, Any]:
    """In the store's context: re-sync today's sales, or refresh the catalog."""
    from app.db import SessionLocal

    what = (payload or {}).get("what")
    db = SessionLocal()
    try:
        if what == "orders":
            from app.pos.sync.sales import sync_sales_for_date
            from app.timeutil import restaurant_today

            job = sync_sales_for_date(db, restaurant_today())
            return {"status": job.status}
        if what == "items":
            from app.pos.sync.catalog import refresh_catalog_cache

            job = refresh_catalog_cache(db, force=True)
            return {"status": job.status}
        return {"status": "ignored"}
    finally:
        db.close()
