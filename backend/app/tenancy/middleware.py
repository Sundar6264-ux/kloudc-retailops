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
   the store from ``?store=CODE``. Store ID privacy (2026-10): an unknown,
   inactive or moved code must read exactly like a real one, so
   - ``/api/app/branding``, ``/api/app/config`` and ``/api/app/legal`` never
     select a store without a session: they answer the product's neutral
     body for every code (the store's own look needs a signed-in session);
   - the public checklist routes answer an unknown code exactly like a
     wrong link (the route sees ``checklist_store_unknown`` and gives its
     own 404 "This link is not active.", with the same rate limits).
4. ``X-Store`` / ``?store=`` WITH a store token must name the token's store,
   else 409 ``store_mismatch``. Never used to pick the store.
5. On the logged-out routes (the public store routes above and the ``/c``
   checklist pages) a token counts only when it is a live session in its
   store's own database; any other token (forged, expired, signed out, an
   unknown or inactive store's) is ignored, so the answer is exactly the one
   with no token at all. Before (verification, 2026-10-22) a forged token
   naming an active store selected that store with no session check, so the
   "not active" page showed the store's name and logo and the checklist API
   answered 404 for a real store but 401 for an unknown one.
"""

import json
import logging
from typing import List, Optional, Tuple
from urllib.parse import parse_qs

from app.tenancy.context import _use_store, blocked_store, dead_token, no_store, platform_scope
from app.tenancy.mode import is_multi_tenant
from app.tenancy.registry import find_store, is_valid_store_code, normalize_store_code
from app.tenancy.routing import foreign_on_dedicated_node, moved_to_shared_body, node_serves, store_moved_body
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


# Of those, the ones that answer the same neutral body for every code before
# sign-in (Store ID privacy, 2026-10): no store is selected without a token.
NEUTRAL_PUBLIC_ROUTES: Tuple[str, ...] = ("/api/app/branding", "/api/app/config", "/api/app/legal")
CHECKLIST_PUBLIC_PREFIX = "/api/public/checklists/"
# The public checklist pages (app/routers/checklist_public.py page_router):
# logged out, no ?store= (the store is in the path).
CHECKLIST_PAGE_PATH = "/c"


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


def is_logged_out_route(path: str) -> bool:
    """A route anyone may call without signing in that may still show a
    store: the public store routes and the /c checklist pages. A token there
    must be a live session to count (rule 5)."""
    if path == CHECKLIST_PAGE_PATH or path.startswith(CHECKLIST_PAGE_PATH + "/"):
        return True
    return _is_public_store_route(path)


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


def _live_session_in(code: str, token: str) -> bool:
    """True when `token` is a live session in store `code`'s own database
    (the store may be paused, archived or not ready). Any trouble -> False."""
    try:
        from app.models import UserSession
        from app.tenancy.engines import tenant_session
        from app.tenancy.tokens import token_hash
        from app.timeutil import utcnow

        with tenant_session(code, allow_inactive=True) as db:
            row = db.get(UserSession, token_hash(token))
            if row is None or row.expires_at is None:
                return False
            expires = row.expires_at
            now = utcnow()
            if expires.tzinfo is None:
                now = now.replace(tzinfo=None)
            return expires >= now
    except Exception:  # noqa: BLE001 - no database, refused, gone: not a live session
        return False


async def _live(code: str, token: str) -> bool:
    from starlette.concurrency import run_in_threadpool

    return await run_in_threadpool(_live_session_in, code, token)


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
            if prefix and is_valid_store_code(prefix) and foreign_on_dedicated_node(prefix):
                # A dedicated server: every code it doesn't serve reads the
                # same (real, archived or unknown alike).
                if path.startswith("/api/"):
                    await _send_json(send, 409, moved_to_shared_body(prefix))
                    return
                with no_store():
                    await self.app(scope, receive, send)
                return
            logged_out_route = is_logged_out_route(path)
            if ref is not None and node_serves(ref) and (ref.status != "active" or logged_out_route):
                # A paused / archived / not-ready store says so only to a
                # live session of its own; a forged token reads like an
                # unknown store's (Store ID privacy, 2026-10). On a
                # logged-out route an active store needs a live session too.
                if not await _live(ref.code, token):
                    ref = None
            if ref is None and logged_out_route:
                # Not a live session anywhere here: exactly as with no token.
                await self._anonymous(scope, receive, send, path, explicit_code, state)
                return
            if ref is not None and ref.status == "archived":
                # An archived store: no store context, like an unknown one,
                # but the auth dependency answers 403 store_archived (with a
                # plain message) instead of a bare 401 (pre-production G2).
                with blocked_store("archived", ref.code):
                    await self.app(scope, receive, send)
                return
            if ref is None:
                with dead_token():
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
                with blocked_store(ref.status, ref.code):
                    await self.app(scope, receive, send)
                return
            with _use_store(ref):
                await self.app(scope, receive, send)
            return

        await self._anonymous(scope, receive, send, path, explicit_code, state)

    async def _anonymous(self, scope, receive, send, path, explicit_code, state) -> None:
        """A request with no (live) session."""
        if path in NEUTRAL_PUBLIC_ROUTES:
            # Before sign-in: the same neutral answer for every Store ID.
            with no_store():
                await self.app(scope, receive, send)
            return

        if path.startswith(CHECKLIST_PUBLIC_PREFIX) and not explicit_code:
            # No store named: a wrong link's answer, like an unknown code.
            state["checklist_store_unknown"] = True
            with no_store():
                await self.app(scope, receive, send)
            return

        if explicit_code and _is_public_store_route(path):
            ref = find_store(explicit_code) if is_valid_store_code(explicit_code) else None
            if ref is None or ref.status != "active" or not node_serves(ref):
                if path.startswith(CHECKLIST_PUBLIC_PREFIX):
                    # Exactly a wrong link's answer (the route gives it).
                    state["checklist_store_unknown"] = True
                    with no_store():
                        await self.app(scope, receive, send)
                    return
                await _send_json(send, 404, {"error": "unknown_store", "message": "No store with that Store ID."})
                return
            with _use_store(ref):
                await self.app(scope, receive, send)
            return

        with no_store():
            await self.app(scope, receive, send)
