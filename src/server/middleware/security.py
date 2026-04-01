import sys
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        # Inject standard airtight security headers extremely fast
        headers = response.headers
        headers["X-Content-Type-Options"] = "nosniff"
        headers["X-Frame-Options"] = "DENY"
        headers["X-XSS-Protection"] = "1; mode=block"
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Disable caching dynamically for /api endpoints to prevent caching sensitive metadata/secrets
        if request.url.path.startswith("/api/"):
            headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            headers["Pragma"] = "no-cache"

        # Content-Security-Policy (Airtight API only restriction, bypass for Swagger UI)
        if not headers.get("Content-Security-Policy"):
            path = request.url.path
            if not (path.startswith("/api/docs") or path.startswith("/api/spec") or path.startswith("/docs") or path.startswith("/redoc")):
                headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"

        return response
