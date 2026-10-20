"""System status for masters: storage, job queue, workers, live updates (docs/WEBSITE_READY_CONTRACT.md, track C).

ROUTER STUB landed with the website-ready contract and already mounted
(app/platform/routes.py install() / app/main.py), so track C only adds
routes here. Prefix: /api/platform/system. Master routes use the same guards as
app/platform/routes.py; store routes use app.auth dependencies.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/api/platform/system", tags=["platform"])
