"""ASGI application: REST API (/api), web GUI (/ui), MCP (/mcp), mock GitHub SCIM (/mock-github).

Run:  uvicorn mockhris.app:app --port 8000
"""
import hmac
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from mcp.server.transport_security import TransportSecuritySettings

from . import api, avatar, mock_scim, services, settings
from .hr import ServiceError
from .mcp_server import mcp
from .store import get_store

log = logging.getLogger("mockhris")
STATIC = Path(__file__).parent / "static"
AVATAR_CACHE_SECONDS = 300


def _transport_security():
    hosts = ["127.0.0.1", "localhost", "[::1]", "127.0.0.1:*", "localhost:*", "[::1]:*"]
    hosts += settings.get().mcp_allowed_hosts
    origins = [f"http://{h}" for h in hosts] + [f"https://{h}" for h in hosts]
    return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts,
                                     allowed_origins=origins)


def create_app():
    # A fresh MCP transport per app: its session manager can only be started once.
    mcp_app = mcp.streamable_http_app(streamable_http_path="/mcp", stateless_http=True,
                                      transport_security=_transport_security())

    @asynccontextmanager
    async def lifespan(app):
        store = get_store()
        store.ensure_tables()
        if settings.get().auto_seed and store.count("users") == 0:
            result = services.hr().seed()
            log.warning("seeded empty database: %s", result)
        services.integrations().ensure_defaults()
        filled = services.hr().backfill_avatars()
        if filled:
            log.warning("added avatars to %d existing users", filled)
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="MockHRIS", version="1.0.0", lifespan=lifespan,
                  description="Mock HRIS for SCIM provisioning of GitHub Enterprise Managed Users. "
                              "GUI at /ui, MCP at /mcp, mock GitHub SCIM target at /mock-github.")

    @app.middleware("http")
    async def api_key_guard(request: Request, call_next):
        key = settings.get().api_key
        path = request.url.path
        if key and (path.startswith("/api") or path.startswith("/mcp")) and path != "/api/health":
            supplied = request.headers.get("x-api-key") or ""
            auth = request.headers.get("authorization", "")
            if not supplied and auth.lower().startswith("bearer "):
                supplied = auth[7:].strip()
            if not hmac.compare_digest(supplied.encode(), key.encode()):
                return JSONResponse({"detail": "invalid or missing API key"}, status_code=401)
        return await call_next(request)

    @app.exception_handler(ServiceError)
    async def service_error(_request, exc: ServiceError):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.exception_handler(mock_scim.ScimHTTPError)
    async def scim_error(_request, exc: mock_scim.ScimHTTPError):
        return mock_scim.scim_error_response(exc)

    app.include_router(api.router)
    app.include_router(mock_scim.router)
    app.mount("/ui", StaticFiles(directory=STATIC, html=True), name="ui")

    svg_cache = {}                                   # user id -> (rendered at, svg); pages request dozens at once

    @app.get("/avatars/{user_id}.svg", tags=["users"], summary="Generated avatar image for a user (by HiBob id)")
    def avatar_svg(user_id: str):
        hit = svg_cache.get(user_id)
        if hit and time.monotonic() - hit[0] < AVATAR_CACHE_SECONDS:
            svg = hit[1]
        else:
            svg = avatar.render(services.hr().user_by_id(user_id) or {"id": user_id})
            svg_cache[user_id] = (time.monotonic(), svg)
        return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=3600"})

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/ui/")

    # Must be last: the MCP Starlette app serves /mcp and 404s everything else.
    app.mount("/", mcp_app)
    return app


app = create_app()
