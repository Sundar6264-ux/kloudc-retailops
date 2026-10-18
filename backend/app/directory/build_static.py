"""Build the static directory (Cloudflare Pages fallback) and the Worker's
data module from one stores file (M2 contract 4.3).

    cd backend
    python -m app.directory.build_static --stores ../directory/stores.json --out ../directory/dist \\
        [--worker-data ../directory/worker/src/data.json] [--refresh-branding]

Writes, under ``--out``:
- ``v1/stores/<CODE>.json``: the exact ``200`` body the service serves;
- ``.well-known/apple-app-site-association``, ``.well-known/assetlinks.json``;
- ``s/index.html``: the open-in-app page (the code is read from the URL in JS);
- ``_headers`` / ``_redirects``: Cloudflare Pages header rules and the
  ``/s/*`` rewrite;
- ``404.html``: without a top-level 404 page Cloudflare Pages treats the
  site as a single-page app and answers unknown codes with 200, so this file
  must exist.
``--worker-data`` writes the JSON module the Worker bundles (same bytes).
``--refresh-branding`` copies each store's branding subset from its
``/api/app/branding`` (dev only, against fake stores; never from tests).
"""

import argparse
import json
import pathlib
import sys
from collections import OrderedDict
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.directory.config import DirectoryConfig
from app.directory.main import CACHE_200, CACHE_404, NO_STORE, OPEN_PAGE_CACHE
from app.directory.open_page import open_page
from app.directory.stores import (
    BRANDING_KEYS,
    ERROR_MESSAGES,
    StoresFile,
    StoresFileError,
    updated_at_from_mtime_ns,
    error_body,
    etag_for,
    file_stamp,
    parse_stores,
)
from app.directory.wellknown import CACHE_CONTROL, aasa_bytes, assetlinks_bytes

# /api/app/branding key -> directory branding key (contract 2 and 4.1).
_BRANDING_FROM_SERVER = OrderedDict(
    [
        ("short_name", "short_name"),
        ("logo_url", "logo_url"),
        ("icon_url", "icon_url"),
        ("primary_color", "primary_color"),
        ("secondary_color", "secondary_color"),
        ("timezone", "timezone"),
        ("pos_name", "pos_name"),
    ]
)
assert tuple(_BRANDING_FROM_SERVER.values()) == BRANDING_KEYS

Fetch = Callable[[str], Dict[str, Any]]


def _http_fetch(url: str) -> Dict[str, Any]:
    import httpx

    resp = httpx.get(url, timeout=8.0, follow_redirects=False)
    resp.raise_for_status()
    return resp.json()


def refresh_branding(raw: Dict[str, Any], fetch: Fetch) -> List[str]:
    """Replace each store's branding with the subset its server reports.
    Returns notes; a store whose server answers for another store code is
    left as it is."""
    notes: List[str] = []
    for store in raw.get("stores", []):
        code = store.get("code")
        url = f"{store.get('api_base_url', '')}/api/app/branding"
        try:
            payload = fetch(url)
        except Exception as exc:  # network errors keep the file's branding
            notes.append(f"{code}: branding not refreshed ({exc.__class__.__name__})")
            continue
        server_code = str(payload.get("store_code") or "").upper()
        if server_code != code:
            notes.append(f"{code}: server reports store_code {server_code or 'null'}; branding kept")
            continue
        store["branding"] = OrderedDict((dst, payload.get(src)) for src, dst in _BRANDING_FROM_SERVER.items())
        notes.append(f"{code}: branding refreshed")
    return notes


def load_for_build(
    stores_path: pathlib.Path, cfg: DirectoryConfig, fetch: Optional[Fetch] = None
) -> Tuple[StoresFile, List[str]]:
    try:
        stamp = file_stamp(stores_path)
        raw = json.loads(stores_path.read_text(encoding="utf-8"), object_pairs_hook=OrderedDict)
    except FileNotFoundError:
        raise StoresFileError(str(stores_path), ["file not found"])
    except ValueError as exc:
        raise StoresFileError(str(stores_path), [f"not valid JSON: {exc}"])
    notes: List[str] = []
    if fetch is not None:
        notes = refresh_branding(raw, fetch)
    stores = parse_stores(raw, cfg, str(stores_path))
    return StoresFile(path=str(stores_path), stores=stores, updated_at=updated_at_from_mtime_ns(stamp[0]), stamp=stamp), notes


def _headers_file(page_csp: str) -> str:
    return (
        "/*\n"
        "  X-Content-Type-Options: nosniff\n"
        "/v1/stores/*\n"
        "  Content-Type: application/json\n"
        f"  Cache-Control: {CACHE_200}\n"
        "  Access-Control-Allow-Origin: *\n"
        "/.well-known/*\n"
        "  Content-Type: application/json\n"
        f"  Cache-Control: {CACHE_CONTROL}\n"
        "/s/*\n"
        f"  Cache-Control: {OPEN_PAGE_CACHE}\n"
        f"  Content-Security-Policy: {page_csp}\n"
        "  Referrer-Policy: no-referrer\n"
    )


_REDIRECTS = "/s/* /s/index.html 200\n/s /s/index.html 200\n"
_NOT_FOUND_HTML = (
    "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"><title>Not found</title></head>"
    "<body><p>Not found.</p></body></html>\n"
)


def worker_data(stores: StoresFile, cfg: DirectoryConfig) -> "OrderedDict[str, Any]":
    page, csp = open_page(cfg.product_name, cfg.app_store_url, cfg.effective_play_store_url)
    entries: "OrderedDict[str, Any]" = OrderedDict()
    for code in stores.stores:
        body = stores.body(code)
        assert body is not None
        entries[code] = OrderedDict([("body", body.decode("utf-8")), ("etag", etag_for(body))])
    return OrderedDict(
        [
            ("schema", 1),
            ("generated_by", "app.directory.build_static"),
            ("rate", OrderedDict([("per_minute", cfg.rate_per_minute), ("per_hour", cfg.rate_per_hour)])),
            ("client_ip_header", cfg.client_ip_header or "CF-Connecting-IP"),
            (
                "cache",
                OrderedDict(
                    [
                        ("ok", CACHE_200),
                        ("not_found", CACHE_404),
                        ("no_store", NO_STORE),
                        ("well_known", CACHE_CONTROL),
                        ("open_page", OPEN_PAGE_CACHE),
                    ]
                ),
            ),
            (
                "errors",
                OrderedDict(
                    [
                        ("invalid_code", error_body("invalid_code").decode("utf-8")),
                        ("unknown_store", error_body("unknown_store").decode("utf-8")),
                        ("directory_unavailable", error_body("directory_unavailable").decode("utf-8")),
                        ("not_found", error_body("not_found").decode("utf-8")),
                        ("rate_limited_message", ERROR_MESSAGES["rate_limited"]),
                    ]
                ),
            ),
            ("aasa", aasa_bytes(cfg.apple_team_id, cfg.app_bundle_id).decode("utf-8")),
            ("assetlinks", assetlinks_bytes(cfg.app_bundle_id, cfg.android_cert_sha256).decode("utf-8")),
            ("open_page", OrderedDict([("html", page.decode("utf-8")), ("csp", csp)])),
            ("stores", entries),
        ]
    )


def _write(path: pathlib.Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def build(
    stores: StoresFile,
    cfg: DirectoryConfig,
    out: Optional[pathlib.Path] = None,
    worker_data_path: Optional[pathlib.Path] = None,
) -> List[pathlib.Path]:
    written: List[pathlib.Path] = []
    if out is not None:
        store_dir = out / "v1" / "stores"
        # A store removed from the file must answer 404 again.
        if store_dir.is_dir():
            for old in store_dir.glob("*.json"):
                if old.stem not in stores.stores:
                    old.unlink()
        for code in stores.stores:
            body = stores.body(code)
            assert body is not None
            _write(store_dir / f"{code}.json", body)
            written.append(store_dir / f"{code}.json")
        page, csp = open_page(cfg.product_name, cfg.app_store_url, cfg.effective_play_store_url)
        files = {
            out / ".well-known" / "apple-app-site-association": aasa_bytes(cfg.apple_team_id, cfg.app_bundle_id),
            out / ".well-known" / "assetlinks.json": assetlinks_bytes(cfg.app_bundle_id, cfg.android_cert_sha256),
            out / "s" / "index.html": page,
            out / "_headers": _headers_file(csp).encode("utf-8"),
            out / "_redirects": _REDIRECTS.encode("utf-8"),
            out / "404.html": _NOT_FOUND_HTML.encode("utf-8"),
        }
        for path, data in files.items():
            _write(path, data)
            written.append(path)
    if worker_data_path is not None:
        data = json.dumps(worker_data(stores, cfg), ensure_ascii=False, indent=1).encode("utf-8") + b"\n"
        _write(worker_data_path, data)
        written.append(worker_data_path)
    return written


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.directory.build_static", description=__doc__.split("\n")[0])
    ap.add_argument("--stores", help="stores file (default: DIRECTORY_STORES_FILE)")
    ap.add_argument("--out", help="output folder for the static site (Cloudflare Pages)")
    ap.add_argument("--worker-data", help="write the Worker's data module (directory/worker/src/data.json)")
    ap.add_argument(
        "--refresh-branding",
        action="store_true",
        help="copy each store's branding from its /api/app/branding (dev only, fake stores)",
    )
    args = ap.parse_args(argv)
    if not args.out and not args.worker_data:
        ap.error("give --out and/or --worker-data")
    cfg = DirectoryConfig.from_settings()
    stores_path = pathlib.Path(args.stores) if args.stores else cfg.stores_path
    try:
        stores, notes = load_for_build(stores_path, cfg, _http_fetch if args.refresh_branding else None)
    except StoresFileError as exc:
        print(f"refused: {exc.path}", file=sys.stderr)
        for p in exc.problems:
            print(f"  - {p}", file=sys.stderr)
        return 2
    for n in notes:
        print(n)
    written = build(
        stores,
        cfg,
        out=pathlib.Path(args.out) if args.out else None,
        worker_data_path=pathlib.Path(args.worker_data) if args.worker_data else None,
    )
    print(f"{len(stores.stores)} stores; wrote {len(written)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
