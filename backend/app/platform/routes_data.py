"""Store data (master side): usage, plans, exports, off-boarding (docs/WEBSITE_READY_CONTRACT.md, track B).

ROUTER STUB landed with the website-ready contract and already mounted
(app/platform/routes.py install() / app/main.py), so track B only adds
routes here. Prefix: /api/platform. Master routes use the same guards as
app/platform/routes.py; store routes use app.auth dependencies.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/api/platform", tags=["platform"])

# The store's own side (owner/admin): GET /api/settings/usage,
# POST /api/settings/exports, GET /api/settings/exports[/{id}/download].
store_router = APIRouter(prefix="/api/settings", tags=["settings"])
