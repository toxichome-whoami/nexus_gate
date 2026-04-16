from fastapi import FastAPI, APIRouter
from fastapi.responses import JSONResponse, FileResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
import os

from config.loader import ConfigManager
from server.lifespan import lifespan
from server.middleware.security_headers import SecurityHeadersMiddleware
from server.middleware.request_id import RequestIDMiddleware
from server.middleware.waf import WAFMiddleware
from server.middleware.logging_mw import LoggingMiddleware
from server.middleware.cors import setup_cors
from server.middleware.rate_limit import RateLimitMiddleware
from server.middleware.idempotency import IdempotencyMiddleware

# Routers
from api.core import health
from api.core.metrics import router as metrics_router
from api import database, storage, federation
from api.admin import router as admin_router

# ─────────────────────────────────────────────────────────────────────────────
# Application Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _playground_middleware_handler(request, call_next):
    """Dynamically prevents access to Swagger/Redoc if the playground is disabled."""
    path = request.url.path
    if path.startswith("/api/docs") or path.startswith("/api/spec"):
        if not ConfigManager.get().features.playground:
            return JSONResponse(
                status_code=404,
                content={
                    "success": False,
                    "error": {
                        "code": "FEATURE_DISABLED",
                        "message": "Playground is currently disabled"
                    }
                }
            )
    return await call_next(request)

def _register_exception_handlers(app: FastAPI):
    """Registers standard RESTful JSON responses for all server exceptions."""
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request, exc):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "error": {
                    "code": getattr(exc, "code", "SERVER_HTTP_ERROR"),
                    "message": exc.detail,
                    "details": getattr(exc, "details", None)
                },
                "meta": {
                    "request_id": getattr(request.state, "request_id", "-"),
                    "version": app.version
                }
            }
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request, exc):
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "error": {
                    "code": "INPUT_SCHEMA_INVALID",
                    "message": "Request validation failed",
                    "details": exc.errors()
                },
                "meta": {
                    "request_id": getattr(request.state, "request_id", "-"),
                    "version": app.version
                }
            }
        )

# ─────────────────────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    """Instantiates the NexusGate application with all layers loaded."""
    config = ConfigManager.get()
    app = FastAPI(
        title="NexusGate",
        description="High-Performance Unified API Gateway with Dynamic Federation, Webhooks & Storage Management",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/spec"
    )

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        icon_path = os.path.join(os.path.dirname(__file__), "..", "icon", "favicon.ico")
        if os.path.exists(icon_path):
            return FileResponse(icon_path)
        return JSONResponse(status_code=404, content={"error": "Icon not found"})

    # Middlewares (Injected globally)
    app.middleware("http")(_playground_middleware_handler)

    app.add_middleware(LoggingMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(IdempotencyMiddleware)
    app.add_middleware(WAFMiddleware)
    setup_cors(app)
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    # API Versioning Structure
    api_v1 = APIRouter(prefix="/api/v1")
    api_v1.include_router(database.router, prefix="/db")
    api_v1.include_router(storage.router, prefix="/fs")
    api_v1.include_router(federation.router, prefix="/fed")
    api_v1.include_router(admin_router, prefix="/admin")

    # MCP Server (conditionally mounted to save resources when disabled)
    if config.features.mcp:
        from api.mcp import router as mcp_router
        api_v1.include_router(mcp_router, prefix="/mcp")

    app.include_router(api_v1)

    # Core System Endpoints (Unversioned)
    app.include_router(health.router)
    app.include_router(metrics_router)

    # Attach Central Exception Parsers
    _register_exception_handlers(app)

    return app
