import logging
import pathlib
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import Depends, FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings, frontend_dist_dir
from app.demo_guard import demo_write_guard
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

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # StoreOps M2: refuse to start against another store's database or upload
    # folder (app/store/identity.py) - before any job can write anything.
    check_store_identity()
    start_scheduler()
    start_notification_scheduler()
    start_team_scheduler()
    # Marketing flyers: fail sends a previous run left stuck, start the
    # (idle) sender thread. Never raises.
    flyer_sender.start()
    yield
    flyer_sender.stop()
    shutdown_team_scheduler()
    shutdown_notification_scheduler()
    shutdown_scheduler()


# demo_write_guard: the App Review demo login is read-only except for an
# explicit allowlist (app/demo_guard.py). Set here, on the app, so every
# route included below - and any added later - runs it.
app = FastAPI(title="StoreOps", lifespan=lifespan, dependencies=[Depends(demo_write_guard)])

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,  # required for the session cookie to be sent/accepted
)

app.include_router(auth.router)
app.include_router(legal_router.router)
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
app.include_router(checklist_admin.router)
app.include_router(flyers.router)
# Public checklist link: the ONLY logged-out routes besides login/health
# (tests/test_review_route_gates.py lists them). The /c/{path} page must be
# registered before the StaticFiles mount at "/" below.
app.include_router(checklist_public.public_router)
app.include_router(checklist_public.page_router)
# Public store branding (StoreOps M2): /api/app/branding and the bundled
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
# Platform (docs/PLATFORM_CONTRACT.md 13; track C): /api/platform, /api/signup,
# /api/public/stores, /api/settings/integrations - and, in multi mode, the
# platform registry/settings sources.
from app.platform.routes import install as _install_platform  # noqa: E402

_install_platform(app)


@app.get("/api/health")
def health():
    return {"status": "ok"}


# A WhatsApp recipient tapping the invoice link (/public/invoices/{token})
# without a valid dashboard session used to just see the raw
# {"detail": "Session expired or invalid"} JSON body - correct per the auth
# gate, but a dead end for someone who followed a real link from their phone.
# Redirect that one case to the web login page instead (with a `next` param
# the frontend uses to bounce straight back to the real file once logged in)
# rather than leaving them staring at an API error. Every other route keeps
# FastAPI's normal JSON error behavior unchanged.
@app.exception_handler(HTTPException)
async def _public_invoice_auth_redirect(request: Request, exc: HTTPException):
    if request.url.path.startswith("/public/invoices/") and exc.status_code in (401, 403):
        next_param = quote(request.url.path, safe="")
        login_url = f"{effective_public_base_url()}/#/login?next={next_param}"
        return RedirectResponse(login_url, status_code=302)
    return await http_exception_handler(request, exc)


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
