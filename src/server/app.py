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

def create_app() -> FastAPI:
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
        # icon is a directory containing favicon.ico
        icon_path = os.path.join(os.path.dirname(__file__), "..", "icon", "favicon.ico")
        if os.path.exists(icon_path):
            return FileResponse(icon_path)
        return JSONResponse(status_code=404, content={"error": "Icon not found"})

    @app.middleware("http")
    async def dynamic_playground_middleware(request, call_next):
        path = request.url.path
        if path.startswith("/api/docs") or path.startswith("/api/spec"):
            cfg = ConfigManager.get()
            if not cfg.features.playground:
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

    # Middleware stack — added in reverse execution order
    app.add_middleware(LoggingMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(IdempotencyMiddleware)
    app.add_middleware(WAFMiddleware)
    setup_cors(app)
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    # --- API Versioning (v1) ---
    api_v1 = APIRouter(prefix="/api/v1")

    # Include functional routers with their specific prefixes
    api_v1.include_router(database.router, prefix="/db")
    api_v1.include_router(storage.router, prefix="/fs")
    api_v1.include_router(federation.router, prefix="/fed")
    api_v1.include_router(admin_router, prefix="/admin")

    # Register the versioned API
    app.include_router(api_v1)

    # Core system routes (usually not versioned)
    app.include_router(health.router)
    app.include_router(metrics_router)

    # Add custom exception handlers
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

    return app
