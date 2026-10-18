"""Throwaway store server for the per-store website checks (MT9).

    STORE_E2E_WORK=<dir> STORE_E2E_CODE=STOREOPS_TEST_TOAST STORE_E2E_PORT=8861 \\
        python -I serve.py

One fake store (docs/M2_CONTRACT.md section 7) on a FRESH database under the
work dir, with generated users only - never a real store's data. Every
outside service is off or pointed at 127.0.0.1:9 (the discard port), the
schedulers and push are off, and smtplib is replaced so nothing can be sent.

The store's settings come from the committed branding fixture
(frontend/e2e/fixtures/branding-<code>.json). On a server that doesn't have
the M2 settings batch yet (no GET /api/app/branding, no Clover provider),
this harness adds those two pieces in-process from the same fixture, so the
website is tested against the contract's response either way; it says which
it used on startup.
"""
import json
import os
import pathlib
import smtplib
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]
WORK = pathlib.Path(os.environ["STORE_E2E_WORK"]).resolve()
CODE = os.environ["STORE_E2E_CODE"]
PORT = int(os.environ["STORE_E2E_PORT"])
STORE = WORK / CODE
FIXTURE = json.loads((HERE.parent / "fixtures" / f"branding-{CODE}.json").read_text())
DISCARD = "http://127.0.0.1:9"

# ---- settings: the fake store, nothing real ----
PROVIDER = {"Toast": "toast", "Clover": "clover", "CSV upload": "csv"}[FIXTURE["pos_name"]]
feats = set(FIXTURE["store_features"])
env = {
    "DATABASE_URL": f"sqlite:///{STORE}/store.db",
    "FRONTEND_DIST_DIR": str(WORK / "dist"),
    "UPLOADS_ROOT": str(STORE / "uploads"),
    "FLYERS_DIR": str(STORE / "uploads" / "flyers"),
    "STORE_CODE": CODE,
    "STORE_NAME": FIXTURE["name"],
    "STORE_SHORT_NAME": FIXTURE["short_name"],
    "STORE_TIMEZONE": FIXTURE["timezone"],
    "BRAND_COLOR": FIXTURE["primary_color"],
    "BRAND_SECONDARY_COLOR": FIXTURE["secondary_color"],
    "STORE_ADDRESS": FIXTURE["address"] or "",
    "STORE_PHONE": FIXTURE["phone"] or "",
    "POS_PROVIDER": PROVIDER,
    "PUBLIC_HOST": f"http://127.0.0.1:{PORT}",
    "LABELS_URL": FIXTURE["labels_url"] or "",
    "AP_INBOX_EMAIL": "ap@storeops-test-toast.storeops.invalid" if "ap_inbox" in feats else "",
    "TOAST_INVOICE_EMAIL": "",
    "AI_ENABLED": "true" if "ai" in feats else "false",
    "DEMO_MODE": "true" if FIXTURE["demo"] else "false",
    "WHAPI_API_TOKEN": "test-not-a-real-token" if "whatsapp" in feats else "",
    "WHAPI_BASE_URL": DISCARD,
    "TOAST_CLIENT_ID": "",
    "TOAST_CLIENT_SECRET": "",
    "TOAST_API_BASE": DISCARD,
    "TOAST_ANALYTICS_BASE": DISCARD,
    "PUBLIC_BASE_URL": f"http://127.0.0.1:{PORT}",
    "TOAST_ANALYTICS_CLIENT_ID": "",
    "ANTHROPIC_API_KEY": "",
    "FIREBASE_CREDENTIALS_FILE": "",
    "GMAIL_ADDRESS": "",
    "GMAIL_APP_PASSWORD": "",
    "SMTP_HOST": "127.0.0.1",
    "SMTP_PORT": "9",
    "SCHEDULER_ENABLED": "false",
    "NOTIFICATIONS_ENABLED": "false",
    "SESSION_COOKIE_SECURE": "false",
}
os.environ.update(env)
os.chdir(STORE)  # no stray .env is read


# Belt and braces: this process may only talk to itself. Any name lookup or
# connection to anything but the loopback address fails at once, whatever
# library tries it (a setting name that changed, a new outside service).
import socket  # noqa: E402

_LOCAL = {"127.0.0.1", "localhost", "::1"}
_real_getaddrinfo = socket.getaddrinfo
_real_connect = socket.socket.connect


def _local_only_getaddrinfo(host, *a, **k):
    if host not in _LOCAL and host is not None:
        raise OSError(f"store e2e: outside lookup blocked ({host})")
    return _real_getaddrinfo(host, *a, **k)


def _local_only_connect(self, address):
    if isinstance(address, tuple) and address[0] not in _LOCAL:
        raise OSError(f"store e2e: outside connection blocked ({address[0]})")
    return _real_connect(self, address)


socket.getaddrinfo = _local_only_getaddrinfo
socket.socket.connect = _local_only_connect


class _NoMail:
    def __init__(self, *a, **k):
        raise RuntimeError("store e2e: sending mail is blocked")


smtplib.SMTP = _NoMail
smtplib.SMTP_SSL = _NoMail

sys.path.insert(0, str(REPO / "backend"))
from app.config import get_settings  # noqa: E402

s = get_settings()
assert s.database_url == env["DATABASE_URL"], s.database_url
assert str(pathlib.Path(s.database_url.split("///", 1)[1]).resolve()).startswith(str(WORK))
assert not s.toast_client_id and not s.anthropic_api_key and not s.firebase_credentials_file
assert s.whapi_base_url == DISCARD
assert s.toast_api_base == DISCARD and s.toast_analytics_base == DISCARD, (s.toast_api_base, s.toast_analytics_base)
assert not s.scheduler_enabled and not s.notifications_enabled

used = []

# ---- pieces the M2 settings batch adds, only when the server lacks them ----
from app.pos import gating  # noqa: E402
from app.pos.capabilities import Capability  # noqa: E402

if PROVIDER not in gating.PROVIDERS:
    gating.PROVIDERS[PROVIDER] = (FIXTURE["pos_name"], frozenset(Capability(c) for c in FIXTURE["capabilities"]))
    used.append(f"provider '{PROVIDER}' from the fixture")
assert gating.pos_name() == FIXTURE["pos_name"], gating.pos_name()
assert gating.capability_values() == FIXTURE["capabilities"], gating.capability_values()

from fastapi.responses import FileResponse, JSONResponse  # noqa: E402

from app.main import app  # noqa: E402

paths = {getattr(r, "path", None) for r in app.router.routes}
local = f"http://127.0.0.1:{PORT}"
if "/api/app/branding" not in paths:
    body = dict(FIXTURE)
    body["logo_url"] = f"{local}/branding/logo.png"
    body["icon_url"] = f"{local}/branding/icon.png"
    body["public_host"] = local

    async def _branding():
        return JSONResponse(body, headers={"Cache-Control": "public, max-age=300"})

    app.router.add_api_route("/api/app/branding", _branding, methods=["GET"])
    used.append("GET /api/app/branding from the fixture")
if "/branding/logo.png" not in paths:
    assets = REPO / "backend" / "app" / "assets" / "branding"

    async def _logo():
        return FileResponse(assets / "logo-128.png")

    async def _icon():
        return FileResponse(assets / "logo-512.png")

    app.router.add_api_route("/branding/logo.png", _logo, methods=["GET"])
    app.router.add_api_route("/branding/icon.png", _icon, methods=["GET"])
    used.append("/branding/*.png from backend/app/assets")
# Added routes must come before the website's catch-all static mount.
mounts = [r for r in app.router.routes if r.__class__.__name__ == "Mount"]
app.router.routes[:] = [r for r in app.router.routes if r not in mounts] + mounts

print(json.dumps({"store": CODE, "port": PORT, "harness_fallbacks": used or "none (server has M2 settings)"}), flush=True)

import uvicorn  # noqa: E402

uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
