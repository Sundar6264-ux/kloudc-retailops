"""Connect with Clover: OAuth authorize, callback, refresh, disconnect, manual token (docs/WEBSITE_READY_CONTRACT.md, track D).

ROUTER STUB landed with the website-ready contract and already mounted
(app/platform/routes.py install() / app/main.py), so track D only adds
routes here. Prefix: /api/settings/integrations/clover. Master routes use the same guards as
app/platform/routes.py; store routes use app.auth dependencies.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/api/settings/integrations/clover", tags=["settings"])
