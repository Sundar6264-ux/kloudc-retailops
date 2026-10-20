"""Master console: masters, platform settings, store detail (docs/WEBSITE_READY_CONTRACT.md, track A).

ROUTER STUB landed with the website-ready contract and already mounted
(app/platform/routes.py install() / app/main.py), so track A only adds
routes here. Prefix: /api/platform. Master routes use the same guards as
app/platform/routes.py; store routes use app.auth dependencies.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/api/platform", tags=["platform"])
