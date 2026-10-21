"""Request context and security headers (one pure-ASGI middleware, the
outermost one, so every answer gets them - CORS preflights and errors too).

Per request:
- a request id: the caller's ``X-Request-ID`` when it is a plain token,
  else a new one; set for every log line of the request
  (app/ops/logs.py) and sent back as ``X-Request-ID``;
- one access log line (logger ``retailops.access``): method, path (never
  the query string - it can hold tokens), status, milliseconds, client;
- an unhandled error is logged once with its traceback and the request id
  (error.log), and answered by ``server_error_handler`` as JSON with the
  request id (no internals);
- security headers (SECURITY_HEADERS=enforce | report-only | off). A header
  a route already set wins (the public checklist page sets its own, tighter
  CSP).

The website's CSP is ``'self'`` only (no outside fonts or scripts). Inline
``<script>`` blocks in the built index.html (a theme script, for example) are
allowed by their SHA-256 hash, computed from the file itself, so a new build
needs no CSP change. Store logos may be https URLs (``img-src https:``).
"""

import base64
import hashlib
import logging
import re
import time
from typing import Dict, List, Optional, Tuple

from app.ops.logs import REQUEST_ID_HEADER, accept_request_id, request_id_var

access_logger = logging.getLogger("retailops.access")
error_logger = logging.getLogger("retailops.error")

HSTS_VALUE = "max-age=31536000"
_INLINE_SCRIPT_RE = re.compile(r"<script(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>", re.S | re.I)
_hash_cache: Dict[str, Tuple[float, List[str]]] = {}


def inline_script_hashes(index_html) -> List[str]:
    """'sha256-...' sources for every inline <script> in the built
    index.html (cached by modification time; [] when there is no build)."""
    import pathlib

    path = pathlib.Path(index_html)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return []
    key = str(path)
    hit = _hash_cache.get(key)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    hashes = []
    for body in _INLINE_SCRIPT_RE.findall(text):
        if not body.strip():
            continue
        digest = base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode("ascii")
        hashes.append(f"'sha256-{digest}'")
    _hash_cache[key] = (mtime, hashes)
    return hashes


def content_security_policy(script_hashes: Optional[List[str]] = None) -> str:
    script_src = " ".join(["'self'"] + list(script_hashes or []))
    return "; ".join(
        [
            "default-src 'self'",
            f"script-src {script_src}",
            # Inline style attributes (React style={...}) need 'unsafe-inline' for styles only.
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: blob: https:",
            "font-src 'self' data:",
            "connect-src 'self'",
            "media-src 'self' data: blob:",
            # PDF previews (PO PDF in an iframe, flyer PDF in an <object>).
            "frame-src 'self' blob:",
            "object-src 'self' blob:",
            "worker-src 'self' blob:",
            "manifest-src 'self'",
            "frame-ancestors 'self'",
            "base-uri 'self'",
            "form-action 'self'",
        ]
    )


def _settings():
    from app.config import process_settings

    return process_settings()


def security_headers(path: str, content_type: str) -> List[Tuple[str, str]]:
    """The headers for one answer (before "a route's own header wins")."""
    s = _settings()
    mode = getattr(s, "security_headers", "enforce")
    if mode == "off":
        return []
    out = [
        ("X-Content-Type-Options", "nosniff"),
        ("Referrer-Policy", "strict-origin-when-cross-origin"),
        ("X-Frame-Options", "SAMEORIGIN"),
        ("Permissions-Policy", "camera=(self), microphone=(self), geolocation=(), payment=(), usb=()"),
        ("Cross-Origin-Opener-Policy", "same-origin"),
    ]
    from app.platform.config import is_production

    if is_production() and bool(getattr(s, "session_cookie_secure", True)):
        # Browsers ignore HSTS on plain http, so this is safe behind a tunnel
        # that terminates https.
        out.append(("Strict-Transport-Security", HSTS_VALUE))
    if content_type.startswith("text/html"):
        from app.config import frontend_dist_dir

        csp = content_security_policy(inline_script_hashes(frontend_dist_dir() / "index.html"))
        name = "Content-Security-Policy-Report-Only" if mode == "report-only" else "Content-Security-Policy"
        out.append((name, csp))
    return out


def _client(scope) -> str:
    client = scope.get("client")
    return client[0] if client else "-"


class RequestContextMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        incoming = None
        for k, v in scope.get("headers") or []:
            if k == b"x-request-id":
                incoming = v.decode("latin-1")
                break
        rid = accept_request_id(incoming)
        token = request_id_var.set(rid)
        state = scope.setdefault("state", {})
        if isinstance(state, dict):
            state["request_id"] = rid
        path = scope.get("path") or ""
        started = time.monotonic()
        status_holder = {"status": 500}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = list(message.get("headers") or [])
                present = {k.decode("latin-1").lower() for k, _ in headers}
                content_type = ""
                for k, v in headers:
                    if k.lower() == b"content-type":
                        content_type = v.decode("latin-1").lower()
                        break
                if "x-request-id" not in present:
                    headers.append((b"x-request-id", rid.encode("latin-1")))
                try:
                    extra = security_headers(path, content_type)
                except Exception:  # noqa: BLE001 - headers must never break an answer
                    error_logger.exception("security headers failed")
                    extra = []
                for name, value in extra:
                    if name.lower() not in present:
                        headers.append((name.lower().encode("latin-1"), value.encode("latin-1")))
                message = dict(message)
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            status_holder["status"] = 500
            error_logger.exception("Unhandled error on %s %s", scope.get("method"), path)
            raise
        finally:
            ms = (time.monotonic() - started) * 1000.0
            access_logger.info(
                "%s %s %s %.0fms %s",
                scope.get("method"),
                path,
                status_holder["status"],
                ms,
                _client(scope),
                extra={"fields": {"method": scope.get("method"), "path": path, "status": status_holder["status"],
                                  "ms": round(ms, 1), "client": _client(scope)}},
            )
            request_id_var.reset(token)


async def server_error_handler(request, exc):
    """Starlette's last resort for an unhandled error (already logged by the
    middleware): a JSON 500 with the request id, nothing internal."""
    from fastapi.responses import JSONResponse

    rid = None
    state = request.scope.get("state")
    if isinstance(state, dict):
        rid = state.get("request_id")
    rid = rid or request_id_var.get()
    return JSONResponse(
        status_code=500,
        content={"detail": "Something went wrong on our side. Try again in a minute.", "error": "server_error", "request_id": rid},
        headers={REQUEST_ID_HEADER: rid},
    )
