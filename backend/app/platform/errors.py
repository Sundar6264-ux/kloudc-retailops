"""JSON errors for the new routes: ``{"error": "<code>", "message": "..."}``
(contract 13). Old routes keep FastAPI's ``detail``."""

from typing import Any, Dict, Optional

from fastapi import Request
from fastapi.responses import JSONResponse

MESSAGES: Dict[str, str] = {
    "not_logged_in": "Not logged in.",
    "forbidden": "You don't have access to this.",
    "master_only": "Only a platform master can do this.",
    "admin_only": "Only a store admin can do this.",
    "not_found": "Not found.",
    "unknown_store": "No store with that code.",
    "invalid_code": "That isn't a valid store code. Codes are 3 to 24 letters, numbers or underscores.",
    "rate_limited": "Too many requests. Try again in {retry_after} seconds.",
    "locked": "Too many failed sign-ins. Try again in {retry_after} seconds.",
    "bad_login": "That Store ID, username or password isn't right. Check all three and try again.",
    "store_suspended": "This store is paused. Contact support.",
    "store_not_ready": "This store is still being set up. Try again shortly.",
    "store_moved": "This store has moved to its own server.",
    "store_mismatch": "That request is for a different store than your session.",
    "dedicated_hosting_disabled": "Dedicated hosting is switched off on this platform.",
    "platform_mode_required": "Integrations are set in the server's settings on a single-store server.",
    "demo_read_only": "The demo account can look around but can't change the store's real data.",
    "not_available": "This isn't available yet.",
    "signup_disabled": "Sign-up is closed.",
    "invalid": "Some values are not valid.",
    "conflict": "That conflicts with the current state.",
    "totp_required": "Enter the 6-digit code from your authenticator app.",
    "totp_setup_required": "Set up two-step sign-in before using the console.",
}


class PlatformError(Exception):
    def __init__(self, status: int, code: str, message: Optional[str] = None, headers: Optional[Dict[str, str]] = None, **extra: Any):
        self.status = status
        self.code = code
        fmt = MESSAGES.get(code, code)
        try:
            fmt = fmt.format(**extra)
        except (KeyError, IndexError):
            pass
        self.message = message or fmt
        self.headers = headers or {}
        self.extra = extra
        super().__init__(self.message)

    def body(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"error": self.code, "message": self.message}
        out.update(self.extra)
        return out


async def platform_error_handler(request: Request, exc: PlatformError) -> JSONResponse:
    headers = dict(exc.headers)
    headers.setdefault("Cache-Control", "no-store")
    return JSONResponse(status_code=exc.status, content=exc.body(), headers=headers)
