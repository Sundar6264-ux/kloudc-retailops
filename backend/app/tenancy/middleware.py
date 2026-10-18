"""TenantMiddleware: pick the store of each request (docs/PLATFORM_CONTRACT.md 4.3).

Pure ASGI, runs before routing; a no-op in single mode.

1. Token from the ``session_token`` cookie or ``Authorization: Bearer``.
2. Token present -> its prefix:
   - PLATFORM_LOGIN_CODE -> platform scope (no store);
   - unknown store -> no store (the route's auth answers 401);
   - a dedicated store this node does not serve -> 409 ``store_moved`` for
     ``/api/*``;
   - store not active -> no store + the status kept for the auth
     dependency (403 ``store_suspended`` / ``store_not_ready``);
   - else the store is selected. The session itself is checked in that
     store's own database by ``get_current_user``.
3. No token: a public store route (``register_public_store_route``) takes
   the store from ``?store=CODE``; unknown / inactive -> 404
   ``unknown_store``.
4. ``X-Store`` / ``?store=`` WITH a store token must name the token's store,
   else 409 ``store_mismatch``. Never used to pick the store.
"""

import json
import logging
from typing import List, Optional, Tuple
from urllib.parse import parse_qs

from app.tenancy.context import _use_store, blocked_store, no_store, platform_scope
from app.tenancy.mode import is_multi_tenant
from app.tenancy.registry import find_store, is_valid_store_code, normalize_store_code
from app.tenancy.routing import node_serves, store_moved_body
from app.tenancy.tokens import token_scope_code

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "session_token"
# Logged-out routes that take the store from ?store=CODE (contract 4.4).
_PUBLIC_STORE_ROUTES: List[str] = [
    "/api/public/checklists/",
    "/api/app/branding",
    "/api/app/config",
    "/api/app/legal",
]


def register_public_store_route(prefix: str) -> None:
    """Track C: a logged-out route (path prefix) that takes ``?store=CODE``."""
    if prefix not in _PUBLIC_STORE_ROUTES:
        _PUBLIC_STORE_ROUTES.append(prefix)


def public_store_routes() -> Tuple[str, ...]:
    return tuple(_PUBLIC_STORE_ROUTES)


def _is_public_store_route(path: str) -> bool:
    for prefix in _PUBLIC_STORE_ROUTES:
        if prefix.endswith("/"):
            if path.startswith(prefix):
                return True
        elif path == prefix or path.startswith(prefix + "/"):
            return True
    return False


def _headers(scope) -> dict:
    out = {}
    for k, v in scope.get("headers") or []:
        out.setdefault(k.decode("latin-1").lower(), v.decode("latin-1"))
    return out


def _cookie(header: str, name: str) -> Optional[str]:
    from starlette.requests import cookie_parser

    try:
        return cookie_parser(header or "").get(name)
    except Exception:  # noqa: BLE001 - a malformed cookie header is just "no cookie"
        return None


def request_token(scope) -> Optional[str]:
    headers = _headers(scope)
    token = _cookie(headers.get("cookie", ""), SESSION_COOKIE_NAME)
    if token:
        return token
    auth = headers.get("authorization") or ""
    scheme, _, value = auth.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return None


async def _send_json(send, status: int, body: dict) -> None:
    raw = json.dumps(body).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": raw})


class TenantMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") not in ("http", "websocket") or not is_multi_tenant():
            await self.app(scope, receive, send)
            return
        from app.config import process_settings

        path = scope.get("path") or ""
        headers = _headers(scope)
        query = parse_qs((scope.get("query_string") or b"").decode("latin-1"))
        explicit = headers.get("x-store") or (query.get("store") or [None])[0]
        explicit_code = normalize_store_code(explicit) if explicit else None
        token = request_token(scope)
        state = scope.setdefault("state", {})

        if token:
            prefix = token_scope_code(token)
            if prefix and prefix == process_settings().platform_login_code:
                # A master's request: no store. (?store= is a filter on
                # platform routes, e.g. /api/platform/audit?store=.)
                with platform_scope():
                    await self.app(scope, receive, send)
                return
            ref = find_store(prefix) if prefix and is_valid_store_code(prefix) else None
            if explicit_code and explicit_code != prefix:
                await _send_json(
                    send,
                    409,
                    {"error": "store_mismatch", "message": "This request names a different store than your login."},
                )
                return
            if ref is None:
                with no_store():
                    await self.app(scope, receive, send)
                return
            if not node_serves(ref):
                if path.startswith("/api/"):
                    await _send_json(send, 409, store_moved_body(ref))
                    return
                with no_store():
                    await self.app(scope, receive, send)
                return
            if ref.status != "active":
                state["store_block"] = ref.status
                with blocked_store(ref.status):
                    await self.app(scope, receive, send)
                return
            with _use_store(ref):
                await self.app(scope, receive, send)
            return

        if explicit_code and _is_public_store_route(path):
            ref = find_store(explicit_code)
            if ref is None or ref.status != "active":
                await _send_json(send, 404, {"error": "unknown_store", "message": "No store with that Store ID."})
                return
            if not node_serves(ref):
                await _send_json(send, 409, store_moved_body(ref))
                return
            with _use_store(ref):
                await self.app(scope, receive, send)
            return

        with no_store():
            await self.app(scope, receive, send)
