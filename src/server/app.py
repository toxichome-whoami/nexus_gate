import os

import orjson
from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from __init__ import __version__
from api import database, federation, storage
from api.admin import router as admin_router

# Routers
from api.core import health
from api.core.metrics import router as metrics_router
from config.provider import GlobalConfigProvider
from server.lifespan import lifespan
from server.middleware.cors import setup_cors
from server.middleware.idempotency import IdempotencyMiddleware
from server.middleware.logging_mw import LoggingMiddleware
from server.middleware.rate_limit import RateLimitMiddleware
from server.middleware.request_id import RequestIDMiddleware
from server.middleware.security_headers import SecurityHeadersMiddleware
from server.middleware.waf import WAFMiddleware

# ─────────────────────────────────────────────────────────────────────────────
# Path Verification
# ─────────────────────────────────────────────────────────────────────────────


def _is_playground_route(path: str) -> bool:
    """Checks if the request path belongs to the Swagger or OpenAPI spec."""
    return path.startswith("/api/docs") or path.startswith("/api/spec")


def _get_favicon_path() -> str:
    """Builds the absolute path to the application's favicon."""
    return os.path.join(os.path.dirname(__file__), "..", "icon", "favicon.ico")


# ─────────────────────────────────────────────────────────────────────────────
# Middleware Handlers
# ─────────────────────────────────────────────────────────────────────────────


class PlaygroundSecurityMiddleware:
    """Blocks access to API documentation endpoints if playground is disabled natively without breaking SSE."""

    def __init__(self, app):
        self.app = app
        self._playground_enabled = (
            GlobalConfigProvider().get_config().features.playground
        )

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        if not self._playground_enabled:
            request_path = scope.get("path", "")
            if _is_playground_route(request_path):
                response = JSONResponse(
                    status_code=404,
                    content={
                        "success": False,
                        "error": {
                            "code": "FEATURE_DISABLED",
                            "message": "Playground is currently disabled",
                        },
                    },
                )
                return await response(scope, receive, send)

        return await self.app(scope, receive, send)


def _attach_middlewares(app: FastAPI):
    """Attaches all global middlewares to the application pipeline."""
    app.add_middleware(PlaygroundSecurityMiddleware)
    app.add_middleware(LoggingMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(IdempotencyMiddleware)
    app.add_middleware(WAFMiddleware)
    setup_cors(app)
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(GZipMiddleware, minimum_size=1000)


# ─────────────────────────────────────────────────────────────────────────────
# Exception Handlers
# ─────────────────────────────────────────────────────────────────────────────


def _build_error_response(
    request: Request, status_code: int, code: str, message: str, details=None
) -> Response:
    """Standardizes JSON response structures for server errors."""
    return Response(
        status_code=status_code,
        content=orjson.dumps(
            {
                "success": False,
                "error": {"code": code, "message": message, "details": details},
                "meta": {
                    "request_id": getattr(request.state, "request_id", "-"),
                    "version": request.app.version,
                },
            }
        ),
        media_type="application/json",
    )


def _attach_exception_handlers(app: FastAPI):
    """Registers standard RESTful JSON responses for unhandled application exceptions."""

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        error_code = getattr(exc, "code", "SERVER_HTTP_ERROR")
        error_details = getattr(exc, "details", None)
        return _build_error_response(
            request, exc.status_code, error_code, exc.detail, error_details
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ):
        return _build_error_response(
            request,
            422,
            "INPUT_SCHEMA_INVALID",
            "Request validation failed",
            exc.errors(),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Router Attachments
# ─────────────────────────────────────────────────────────────────────────────


def _attach_routers(app: FastAPI):
    """Registers all API versioned and unversioned core routing endpoints."""
    config = GlobalConfigProvider().get_config()

    # Version 1 API structure
    api_v1 = APIRouter(prefix="/api/v1")
    api_v1.include_router(database.router, prefix="/db")
    api_v1.include_router(storage.router, prefix="/fs")
    api_v1.include_router(federation.router, prefix="/fed")
    api_v1.include_router(admin_router, prefix="/admin")

    if config.features.mcp:
        from api.mcp import router as mcp_router

        api_v1.include_router(mcp_router, prefix="/mcp")
        app.state.mcp_initialized = True

    app.include_router(api_v1)

    # Core System Endpoints (Unversioned)
    app.include_router(health.router)
    app.include_router(metrics_router)

    # Static Assets
    @app.get("/favicon.ico", include_in_schema=False)
    async def serve_favicon():
        icon_path = _get_favicon_path()
        if os.path.exists(icon_path):
            return FileResponse(icon_path)
        return JSONResponse(status_code=404, content={"error": "Icon not found"})


# ─────────────────────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────────────────────


def create_app() -> FastAPI:
    """Instantiates the NexusGate application with all layers loaded and properly structured."""
    app = FastAPI(
        title="NexusGate",
        description="High-Performance Unified API Gateway with Dynamic Federation, Webhooks, MCP & Storage Management",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/spec",
    )

    _attach_middlewares(app)
    _attach_routers(app)
    _attach_exception_handlers(app)

    return app
