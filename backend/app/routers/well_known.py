"""``/.well-known/apple-app-site-association`` and ``assetlinks.json`` on
every store server (M2 contract 4.4, MT10).

Store servers serve public checklist links (``/c/...``), so iOS and Android
must find the association files on the store's own host too. The bodies are
the directory's (``app.directory.wellknown``); with ``APPLE_TEAM_ID`` /
``ANDROID_CERT_SHA256`` blank the lists are empty. Registered before the
static mount at "/" so the SPA never answers these paths.
"""

import logging
import os
from typing import Any, Tuple

from fastapi import APIRouter
from fastapi.responses import Response

from app.config import get_settings
from app.directory.config import BUNDLE_RE, DEFAULT_BUNDLE_ID, FINGERPRINT_RE, TEAM_ID_RE
from app.directory.wellknown import (
    AASA_PATH,
    ASSETLINKS_PATH,
    CACHE_CONTROL,
    aasa_bytes,
    assetlinks_bytes,
)

log = logging.getLogger(__name__)

router = APIRouter(tags=["well-known"])

def _setting(name: str, default: Any) -> Any:
    # Contract rule 0.8: MT7 adds these fields; until then the environment.
    value = getattr(get_settings(), name, None)
    if value is None:
        value = os.environ.get(name.upper())
    return default if value is None else value


def _owner_ids() -> Tuple[str, str, Tuple[str, ...]]:
    """(team id, bundle id, fingerprints). A malformed owner value is left
    out with a warning (an empty list is safe; a wrong id is not)."""
    bundle = str(_setting("app_bundle_id", DEFAULT_BUNDLE_ID) or DEFAULT_BUNDLE_ID).strip()
    if not BUNDLE_RE.match(bundle):
        log.warning("APP_BUNDLE_ID %r is not a bundle id; using com.kloudc.storeops", bundle)
        bundle = DEFAULT_BUNDLE_ID
    team = str(_setting("apple_team_id", "") or "").strip().upper()
    if team and not TEAM_ID_RE.match(team):
        log.warning("APPLE_TEAM_ID is not 10 letters/digits; apple-app-site-association lists no app")
        team = ""
    raw = _setting("android_cert_sha256", "")
    items = raw if isinstance(raw, (list, tuple)) else str(raw or "").split(",")
    prints = []
    for item in items:
        fp = str(item).strip().upper()
        if not fp:
            continue
        if FINGERPRINT_RE.match(fp):
            prints.append(fp)
        else:
            log.warning("ANDROID_CERT_SHA256 entry is not a SHA-256 fingerprint; left out")
    return team, bundle, tuple(prints)


def _json(body: bytes) -> Response:
    return Response(content=body, media_type="application/json", headers={"Cache-Control": CACHE_CONTROL})


@router.get(AASA_PATH, include_in_schema=False)
def apple_app_site_association() -> Response:
    team, bundle, _ = _owner_ids()
    return _json(aasa_bytes(team, bundle))


@router.get(ASSETLINKS_PATH, include_in_schema=False)
def assetlinks() -> Response:
    _, bundle, prints = _owner_ids()
    return _json(assetlinks_bytes(bundle, prints))
