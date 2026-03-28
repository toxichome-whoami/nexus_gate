from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
from config.loader import ConfigManager
from utils.size_parser import parse_size
from api.responses import error_response
import re

class WAFMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp):
        super().__init__(app)
        self.config = ConfigManager.get()
        self.body_limit_bytes = parse_size(self.config.server.body_limit)
        self.traversal_pattern = re.compile(r'(\.\./|\.\.\\|%2e%2e%2f|%2e%2e/|\.\.%2f|%2e%2e%5c)', re.IGNORECASE)

    async def dispatch(self, request: Request, call_next):
        # 1. URL Length Check
        if len(str(request.url)) > 2048:
            return JSONResponse(
                status_code=status.HTTP_414_REQUEST_URI_TOO_LONG,
                content=error_response(request, "WAF_URI_TOO_LONG", "URI exceeds 2048 character limit")
            )

        # 2. Null Byte Check
        if b'\x00' in request.url.path.encode('utf-8') or b'\x00' in request.url.query.encode('utf-8'):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content=error_response(request, "WAF_NULL_BYTE", "Null byte detected in request")
            )

        # 3. Path Traversal Check
        if self.traversal_pattern.search(request.url.path) or self.traversal_pattern.search(request.url.query):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content=error_response(request, "WAF_PATH_TRAVERSAL", "Path traversal attempt detected")
            )

        # 4. Query Parameter Count Check
        if len(request.query_params) > 50:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content=error_response(request, "WAF_TOO_MANY_PARAMS", "Too many query parameters (limit 50)")
            )

        # 5. Body Limit Check
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > self.body_limit_bytes:
             return JSONResponse(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                content=error_response(request, "WAF_BODY_TOO_LARGE", f"Request body too large. Limit is {self.config.server.body_limit}")
            )
        
        return await call_next(request)
