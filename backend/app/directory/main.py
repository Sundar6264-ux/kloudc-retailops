"""The directory service (M2 contract 4.2): its own ASGI app, never mounted
in a store server.

    cd backend && uvicorn app.directory.main:app --port 8090

Production runs the Cloudflare Worker in ``directory/worker/`` with the same
responses; this app is the reference the tests run, the local dev server and
a self-host option.

The stores file is checked at startup (a bad file stops the service) and
re-read whenever it changes on disk; if it later goes missing or invalid,
lookups answer 503 until it is fixed.
"""

import logging
import threading
from contextlib import asynccontextmanager
from typing import Callable, Optional

from fastapi import FastAPI, Request
from fastapi.responses import Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.directory.config import DirectoryConfig
from app.directory.open_page import open_page
from app.directory.ratelimit import SlidingWindowLimiter
from app.directory.stores import (
    StoresFile,
    StoresFileError,
    error_body,
    etag_for,
    file_stamp,
    load_stores_file,
    normalize_code,
)
from app.directory.wellknown import AASA_PATH, ASSETLINKS_PATH, CACHE_CONTROL, aasa_bytes, assetlinks_bytes

log = logging.getLogger("retailops.directory")

JSON = "application/json"
CACHE_200 = "public, max-age=300, stale-while-revalidate=3600"
CACHE_404 = "public, max-age=60"
NO_STORE = "no-store"
OPEN_PAGE_CACHE = "public, max-age=3600"


class StoresRegistry:
    """Holds the parsed stores file and re-reads it when it changes."""

    def __init__(self, config: DirectoryConfig):
        self.config = config
        self._lock = threading.Lock()
        self._current: Optional[StoresFile] = None

    def get(self) -> StoresFile:
        path = self.config.stores_path
        try:
            stamp = file_stamp(path)
        except OSError:
            raise StoresFileError(str(path), ["file not found"])
        cur = self._current
        if cur is not None and cur.stamp == stamp and cur.path == str(path):
            return cur
        with self._lock:
            cur = self._current
            if cur is not None and cur.stamp == stamp and cur.path == str(path):
                return cur
            try:
                loaded = load_stores_file(path, self.config)
            except StoresFileError:
                self._current = None
                raise
            self._current = loaded
            log.info("directory: loaded %d stores from %s", len(loaded.stores), path)
            return loaded


def _if_none_match(request: Request, etag: str) -> bool:
    header = request.headers.get("if-none-match")
    if not header:
        return False
    for tag in header.split(","):
        t = tag.strip()
        if t == "*":
            return True
        if t.startswith("W/"):
            t = t[2:]
        if t == etag:
            return True
    return False


def _client_ip(request: Request, header: str) -> str:
    if header:
        # The last entry: in a comma list (X-Forwarded-For) the client writes
        # everything before the value the proxy in front of us appended.
        value = request.headers.get(header, "")
        last = value.split(",")[-1].strip()
        if last:
            return last
    return request.client.host if request.client else "unknown"


def _json(status: int, body: bytes, cache: str, **headers: str) -> Response:
    h = {"Cache-Control": cache, "Access-Control-Allow-Origin": "*", "X-Content-Type-Options": "nosniff"}
    h.update(headers)
    return Response(content=body, status_code=status, media_type=JSON, headers=h)


def create_app(
    config: Optional[DirectoryConfig] = None,
    *,
    clock: Optional[Callable[[], float]] = None,
) -> FastAPI:
    cfg = config or DirectoryConfig.from_settings()
    registry = StoresRegistry(cfg)
    limiter = (
        SlidingWindowLimiter(cfg.rate_per_minute, cfg.rate_per_hour, clock=clock)
        if clock is not None
        else SlidingWindowLimiter(cfg.rate_per_minute, cfg.rate_per_hour)
    )
    aasa = aasa_bytes(cfg.apple_team_id, cfg.app_bundle_id)
    assetlinks = assetlinks_bytes(cfg.app_bundle_id, cfg.android_cert_sha256)
    page, page_csp = open_page(cfg.product_name, cfg.app_store_url, cfg.effective_play_store_url)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Refuse to start on a bad stores file (contract 4.1).
        registry.get()
        yield

    app = FastAPI(
        title=f"{cfg.product_name} directory",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    # Contract 4.2: any other path is 404 not_found, like the Worker; no
    # 307 to the slash-less URL (built from the request's Host header).
    app.router.redirect_slashes = False
    app.state.config = cfg
    app.state.registry = registry
    app.state.limiter = limiter

    @app.exception_handler(StarletteHTTPException)
    async def _http_errors(request: Request, exc: StarletteHTTPException) -> Response:
        if exc.status_code == 404:
            return _json(404, error_body("not_found"), NO_STORE)
        return Response(status_code=exc.status_code, headers=getattr(exc, "headers", None) or {})

    @app.get("/v1/stores/{code}")
    async def lookup(code: str, request: Request) -> Response:
        wait = limiter.check(_client_ip(request, cfg.client_ip_header))
        if wait is not None:
            return _json(
                429,
                error_body("rate_limited", retry_after=wait),
                NO_STORE,
                **{"Retry-After": str(wait)},
            )
        norm = normalize_code(code)
        if norm is None:
            return _json(400, error_body("invalid_code"), NO_STORE)
        try:
            stores = registry.get()
        except StoresFileError as exc:
            log.error("directory unavailable: %s", exc)
            return _json(503, error_body("directory_unavailable"), NO_STORE, **{"Retry-After": "30"})
        body = stores.body(norm)
        if body is None:
            return _json(404, error_body("unknown_store"), CACHE_404)
        etag = etag_for(body)
        if _if_none_match(request, etag):
            return Response(
                status_code=304,
                headers={"ETag": etag, "Cache-Control": CACHE_200, "Access-Control-Allow-Origin": "*"},
            )
        return _json(200, body, CACHE_200, ETag=etag)

    @app.get("/healthz")
    async def healthz() -> Response:
        try:
            stores = registry.get()
        except StoresFileError:
            return _json(503, b'{"ok":false,"stores":0}', NO_STORE)
        return _json(200, b'{"ok":true,"stores":%d}' % len(stores.stores), NO_STORE)

    @app.get(AASA_PATH)
    async def aasa_route() -> Response:
        return Response(content=aasa, media_type=JSON, headers={"Cache-Control": CACHE_CONTROL})

    @app.get(ASSETLINKS_PATH)
    async def assetlinks_route() -> Response:
        return Response(content=assetlinks, media_type=JSON, headers={"Cache-Control": CACHE_CONTROL})

    def _page() -> Response:
        return Response(
            content=page,
            media_type="text/html; charset=utf-8",
            headers={
                "Cache-Control": OPEN_PAGE_CACHE,
                "Content-Security-Policy": page_csp,
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            },
        )

    @app.get("/s/{code}")
    async def open_in_app(code: str) -> Response:
        return _page()

    @app.get("/s/")
    async def open_in_app_bare() -> Response:
        return _page()

    @app.get("/s")
    async def open_in_app_root() -> Response:
        return _page()

    return app


class _LazyApp:
    """``uvicorn app.directory.main:app`` builds the app from settings on
    first use, so importing this module (tests, tooling) never needs a
    stores file."""

    def __init__(self) -> None:
        self._app: Optional[FastAPI] = None

    async def __call__(self, scope, receive, send):  # type: ignore[no-untyped-def]
        if self._app is None:
            try:
                self._app = create_app()
            except Exception as exc:
                if scope["type"] != "lifespan":
                    raise
                # A bad setting stops the server (uvicorn exits on
                # startup.failed, even in its default "auto" lifespan mode).
                await receive()
                await send({"type": "lifespan.startup.failed", "message": str(exc)})
                return
        await self._app(scope, receive, send)


app = _LazyApp()
