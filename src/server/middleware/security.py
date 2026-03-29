import sys
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        # Pre-process payload size optionally (Max 5MB raw stream abort to prevent memory bombs)
        # Note: Starlette does not read the body here, so it is O(1) pure header check.
        content_length = request.headers.get('content-length')
        if content_length and int(content_length) > 5_242_880: # 5MB limit
            from starlette.responses import JSONResponse
            return JSONResponse(
                {"success": False, "error": {"code": "PAYLOAD_TOO_LARGE", "message": "Max 5MB payload limit exceeded."}},
                status_code=413
            )

        # Process the request
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

        # Content-Security-Policy (Airtight API only restriction unless serving standard images)
        if not headers.get("Content-Security-Policy"):
            headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"

        return response
