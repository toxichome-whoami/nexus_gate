from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
from config.loader import ConfigManager
from utils.size_parser import parse_size

class WAFMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp):
        super().__init__(app)
        self.config = ConfigManager.get()
        self.body_limit_bytes = parse_size(self.config.server.body_limit)

    async def dispatch(self, request: Request, call_next):
        # 1. URL Length Check
        if len(str(request.url)) > 2048:
            raise HTTPException(status_code=status.HTTP_414_REQUEST_URI_TOO_LONG, detail="URI Too Long")

        # 2. Null Byte Check
        # Check path and query for null bytes
        if b'\x00' in request.url.path.encode('utf-8') or b'\x00' in request.url.query.encode('utf-8'):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Null byte detected")

        # 3. Path Traversal Regex Check (on path and query)
        import re
        traversal_pattern = re.compile(r'(\.\./|\.\.\\|%2e%2e%2f|%2e%2e/|\.\.%2f|%2e%2e%5c)', re.IGNORECASE)
        if traversal_pattern.search(request.url.path) or traversal_pattern.search(request.url.query):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Path traversal attempt detected")

        # 4. Query Parameter Count Check
        if len(request.query_params) > 50:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Too many query parameters")

        # 5. Body Limit Check
        # Check Content-Length header if present
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > self.body_limit_bytes:
             raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Request body too large")

        # We can implement stream-based size checking for chunked uploads
        # But for now rely on content-length and framework limits
        
        response = await call_next(request)
        return response
