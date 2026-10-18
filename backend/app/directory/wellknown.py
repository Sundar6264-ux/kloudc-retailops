"""``apple-app-site-association`` and ``assetlinks.json`` (M2 contract 4.4).

Served with ``Content-Type: application/json`` and no redirect from the
directory host, the static host, the bare platform domain and every store
server (``app/routers/well_known.py``). While the owner ids are blank the
lists are empty, so nothing claims to be the app before it exists.
"""

from collections import OrderedDict
from typing import Any, Iterable, List

from app.directory.stores import compact_json

AASA_PATH = "/.well-known/apple-app-site-association"
ASSETLINKS_PATH = "/.well-known/assetlinks.json"
CACHE_CONTROL = "public, max-age=3600"

# Paths the app opens: store-code links / QR codes. Public checklist links
# (/c/...) are left out on purpose: the app has no /c/ route (MT11 As
# built), so claiming them would open those links in the app (on a page it
# can't show) instead of the browser. Add "/c/*" only with an app route.
APP_LINK_COMPONENTS = (("/s/*", "store-code links and QR codes"),)


def aasa_payload(apple_team_id: str, bundle_id: str) -> "OrderedDict[str, Any]":
    app_ids: List[str] = [f"{apple_team_id}.{bundle_id}"] if apple_team_id else []
    components = [OrderedDict([("/", path), ("comment", comment)]) for path, comment in APP_LINK_COMPONENTS]
    detail = OrderedDict([("appIDs", app_ids), ("components", components)])
    return OrderedDict([("applinks", OrderedDict([("details", [detail])]))])


def assetlinks_payload(bundle_id: str, fingerprints: Iterable[str]) -> List[Any]:
    target = OrderedDict(
        [
            ("namespace", "android_app"),
            ("package_name", bundle_id),
            ("sha256_cert_fingerprints", list(fingerprints)),
        ]
    )
    return [
        OrderedDict(
            [
                ("relation", ["delegate_permission/common.handle_all_urls"]),
                ("target", target),
            ]
        )
    ]


def aasa_bytes(apple_team_id: str, bundle_id: str) -> bytes:
    return compact_json(aasa_payload(apple_team_id, bundle_id))


def assetlinks_bytes(bundle_id: str, fingerprints: Iterable[str]) -> bytes:
    return compact_json(assetlinks_payload(bundle_id, fingerprints))
