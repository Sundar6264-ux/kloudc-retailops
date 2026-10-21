import logging
import pathlib
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import Depends, FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings, frontend_dist_dir
from app.demo_guard import demo_write_guard
from app.ops.health import router as ops_health_router
from app.ops.http import RequestContextMiddleware, server_error_handler
from app.ops.logs import configure_logging
from app.ops.prod_checks import enforce as enforce_production_config
from app.plain_errors import bare_phrase_body, validation_body
from starlette.exceptions import HTTPException as StarletteHTTPException
from app.routers import (
    ask,
    auth,
    branding,
    category_overrides,
    checklist_admin,
    checklist_public,
    checklists,
    critical_dismissals,
    digest,
    flyers,
    glossary,
    help as help_router,
    inventory,
    invoices,
    issues,
    jobs,
    notifications,
    ordering,
    orders,
    po_sections,
    reconciliation,
    reports,
    sales,
    transfers,
    vendor_calendar,
    well_known,
)
from app.routers import po_send as po_send_router  # Send PO + vendor contacts (automation item 8)
from app.routers import inventory_api as inventory_api_router  # Inventory from the Toast API (automation items 1, 6)
from app.routers import insights as insights_router  # Insights from Toast Analytics (docs/INSIGHTS_CONTRACT.md)
from app.routers import issue_leads as issue_leads_router  # Fix Issues category & vendor leads (W4)
from app.routers import order_actions as order_actions_router  # Today-tab order actions (BATCH_OCT14 W1)
from app.routers import legal as legal_router  # GET /api/app/legal: legal links + AI notice (M2 MT13)
from app.services import flyer_sender
from app.store.identity import check_store_identity
from app.store.settings_view import effective_public_base_url
from app.tenancy import is_multi_tenant
from app.tenancy.middleware import TenantMiddleware
from app.team import router_core as team_router_core
from app.team import router_chat as team_router_chat
from app.team import router_tasks as team_router_tasks
from app.team import auto_tasks as _team_auto_tasks  # noqa: F401 - loaded with the app; its rules run inside tasks_tick
from app.scheduler import (
    shutdown_notification_scheduler,
    shutdown_scheduler,
    shutdown_team_scheduler,
    start_notification_scheduler,
    start_scheduler,
    start_team_scheduler,
)

# Request id + store code on every log line; LOG_FORMAT / LOG_DIR (app/ops/logs.py).
configure_logging()
logger = logging.getLogger("retailops.lifecycle")


def _stop_steps(steps) -> None:
    """Graceful shutdown: every step runs even when an earlier one fails, and
    each is logged (uvicorn waits --timeout-graceful-shutdown seconds for
    open requests first; launchd / Docker give the process 30 s)."""
    import time

    started = time.monotonic()
    for name, fn in steps:
        try:
            fn()
        except Exception:  # noqa: BLE001 - keep stopping the rest
            logger.exception("shutdown step %s failed", name)
    logger.info("stopped in %.1f s", time.monotonic() - started)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Production refuses an unsafe configuration before anything starts
    # (app/ops/prod_checks.py); other environments only log the findings.
    enforce_production_config()
    # STORAGE_BACKEND=s3 without its endpoint, bucket and keys refuses to
    # start (website-ready C1); the default local backend needs nothing.
    from app.storage import check_storage_config

    check_storage_config()
    # Usage metering (website-ready I3, track B): the buffered counters'
    # flush thread runs in both modes; never raises.
    from app.platform import usage as _usage

    _usage.start()
    if is_multi_tenant():
        # Platform milestone (docs/PLATFORM_CONTRACT.md 5, 6): the identity
        # check runs per store on its engine's first open; the schedulers run
        # on the elected leader only (app/tenancy/boot.py).
        from app.tenancy.boot import start_multi_tenant, stop_multi_tenant

        start_multi_tenant()
        logger.info("started (multi-store)")
        yield
        logger.info("shutting down")
        _stop_steps([("multi-store", stop_multi_tenant), ("usage", _usage.stop)])
        return
    # KloudC RetailOps M2: refuse to start against another store's database or upload
    # folder (app/store/identity.py) - before any job can write anything.
    check_store_identity()
    start_scheduler()
    start_notification_scheduler()
    start_team_scheduler()
    # Marketing flyers: fail sends a previous run left stuck, start the
    # (idle) sender thread. Never raises.
    flyer_sender.start()
    logger.info("started (single store)")
    yield
    logger.info("shutting down")
    _stop_steps(
        [
            ("flyer sender", flyer_sender.stop),
            ("team scheduler", shutdown_team_scheduler),
            ("notification scheduler", shutdown_notification_scheduler),
            ("scheduler", shutdown_scheduler),
            ("usage", _usage.stop),
        ]
    )


# demo_write_guard: the App Review demo login is read-only except for an
# explicit allowlist (app/demo_guard.py). Set here, on the app, so every
# route included below - and any added later - runs it.
def api_docs_urls(app_env) -> dict:
    """FastAPI's /docs, /redoc and /openapi.json: off in production (the
    full list of routes and fields is a map for an attacker; docs/
    SECURITY_REVIEW.md SR-02), on in development and tests."""
    from app.platform.config import env_is_production

    if env_is_production(app_env):
        return {"docs_url": None, "redoc_url": None, "openapi_url": None}
    return {"docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/openapi.json"}


app = FastAPI(title="KloudC RetailOps", lifespan=lifespan, dependencies=[Depends(demo_write_guard)],
              **api_docs_urls(get_settings().app_env))

settings = get_settings()
# The store of each request (multi mode; a no-op in single mode). Added
# before CORS so CORS stays outermost and its headers are on every answer.
app.add_middleware(TenantMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,  # required for the session cookie to be sent/accepted
)
# Outermost: request id, access log, security headers (app/ops/http.py).
app.add_middleware(RequestContextMiddleware)
# An unhandled error: logged with its request id by the middleware, answered
# here as JSON carrying the request id.
app.add_exception_handler(Exception, server_error_handler)

app.include_router(auth.router)
app.include_router(legal_router.router)
app.include_router(legal_router.page_router)  # /terms (public, before the static mount)
app.include_router(jobs.router)
app.include_router(inventory.router)
app.include_router(inventory_api_router.router)
app.include_router(sales.router)
app.include_router(insights_router.router)
app.include_router(ordering.router)
app.include_router(po_send_router.router)
app.include_router(critical_dismissals.router)
app.include_router(order_actions_router.router)
app.include_router(reports.router)
app.include_router(reconciliation.router)
app.include_router(ask.router)
app.include_router(digest.router)
app.include_router(transfers.router)
app.include_router(orders.router)
app.include_router(invoices.router)
app.include_router(invoices.public_router)
app.include_router(vendor_calendar.router)
app.include_router(category_overrides.router)
app.include_router(po_sections.router)
app.include_router(issues.router)
app.include_router(glossary.router)
# Help content (docs/DESIGN_SYSTEM.md 5.2): /api/help and the logged-out /api/public/help.
app.include_router(help_router.router)
app.include_router(help_router.public_router)
app.include_router(checklist_admin.router)
app.include_router(flyers.router)
# Public checklist link: the ONLY logged-out routes besides login/health
# (tests/test_review_route_gates.py lists them). The /c/{path} page must be
# registered before the StaticFiles mount at "/" below.
app.include_router(checklist_public.public_router)
app.include_router(checklist_public.page_router)
# Public store branding (KloudC RetailOps M2): /api/app/branding and the bundled
# /branding/*.png images, also before the static mount.
app.include_router(branding.router)
app.include_router(branding.assets_router)
app.include_router(well_known.router)  # AASA / assetlinks for /c/ links (M2 MT10)
app.include_router(checklists.router)
app.include_router(notifications.router)
app.include_router(team_router_core.router)
app.include_router(team_router_core.app_router)
app.include_router(team_router_chat.router)
app.include_router(team_router_tasks.router)
app.include_router(issue_leads_router.router)
from app.routers import item_vendors as item_vendors_router  # noqa: E402 - item vendors editor (website-ready track D)

app.include_router(item_vendors_router.router)
# Platform (docs/PLATFORM_CONTRACT.md 13; track C): /api/platform, /api/signup,
# /api/public/stores, /api/settings/integrations - and, in multi mode, the
# platform registry/settings sources.
from app.platform.routes import install as _install_platform  # noqa: E402

_install_platform(app)


@app.get("/api/health")
def health():
    """Liveness: the process answers (launchd / Docker restarts)."""
    return {"status": "ok"}


# Readiness: databases answer and migrations are current (app/ops/health.py).
app.include_router(ops_health_router)


# A WhatsApp recipient tapping the invoice link (/public/invoices/{token})
# without a valid dashboard session used to just see the raw
# {"detail": "Session expired or invalid"} JSON body - correct per the auth
# gate, but a dead end for someone who followed a real link from their phone.
# Redirect that one case to the web login page instead (with a `next` param
# the frontend uses to bounce straight back to the real file once logged in)
# rather than leaving them staring at an API error. Every other route keeps
# FastAPI's normal JSON error behavior unchanged.
@app.exception_handler(StarletteHTTPException)
async def _public_invoice_auth_redirect(request: Request, exc: StarletteHTTPException):
    code = getattr(exc, "error_code", None)
    if code and not request.url.path.startswith("/public/invoices/"):
        # Store access refused (app/db.py, PLATFORM_CONTRACT 4.3): the old
        # "detail" plus the new {"error", "message"}.
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "error": code, "message": exc.detail},
            headers=getattr(exc, "headers", None),
        )
    if request.url.path.startswith("/public/invoices/") and exc.status_code in (401, 403):
        next_param = quote(request.url.path, safe="")
        login_url = f"{effective_public_base_url()}/#/login?next={next_param}"
        return RedirectResponse(login_url, status_code=302)
    # A bare "Not Found" / "Internal Server Error" (G27): a sentence too
    # (app/plain_errors.py; "Not Found" stays in detail when no route matched).
    plain = bare_phrase_body(exc.status_code, exc.detail, route_matched=request.scope.get("endpoint") is not None)
    if plain is not None:
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=exc.status_code, content=plain, headers=getattr(exc, "headers", None))
    return await http_exception_handler(request, exc)


@app.exception_handler(RequestValidationError)
async def _plain_form_errors(request: Request, exc: RequestValidationError):
    """A form the server can't accept (422): a plain sentence per field
    (G25, app/plain_errors.py), the old detail list kept."""
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=422, content=jsonable_encoder(validation_body(exc.errors())))


# Bounds how long a browser (especially a phone that rarely hard-refreshes a
# bookmarked/PWA-like tab) can keep serving a stale hashed asset bundle -
# plain StaticFiles sets no explicit Cache-Control at all, which lets a
# browser's own heuristics cache indefinitely.
#
# index.html is deliberately excluded from that 1-day cache - a real bug,
# found from a real report ("I don't see any appearance changes"): index.html
# is the SPA entry point that names the *current* build's hashed JS/CSS
# filenames, so caching it for a day meant a browser could keep loading
# yesterday's index.html - pointing at yesterday's now-superseded bundle -
# for up to 24h after every single deploy, silently masking every UI change
# shipped in between. It must always be revalidated so a new deploy's new
# filenames are picked up on the very next load. The hashed asset files
# themselves stay safe to cache long: a new build always gets new filenames
# (Vite content-hashes them), so a cached old one is simply never referenced
# by a fresh index.html - there's no way for it to serve stale content.
class _DayCachedStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        # Verified against Starlette's actual StaticFiles.get_path(): the
        # root URL ("/", what every real page load hits) normalizes to the
        # literal string ".", not "" as a first guess would assume - checked
        # live via curl before trusting this, since getting this condition
        # wrong here is exactly the class of bug this whole fix exists to
        # prevent. "index.html" covers a direct /index.html request.
        if path in (".", "index.html"):
            response.headers["Cache-Control"] = "no-cache"
        else:
            response.headers["Cache-Control"] = "public, max-age=86400"
        return response


# Serves the production frontend build (`npm run build` in frontend/) directly
# from the backend, so persistent deployment only needs one process/port. Only
# mounted if the build exists, so `npm run dev` + CORS still works untouched
# for local frontend development.
_frontend_dist = frontend_dist_dir()
if _frontend_dist.is_dir():
    app.mount("/", _DayCachedStaticFiles(directory=str(_frontend_dist), html=True), name="frontend")
