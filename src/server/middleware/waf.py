from starlette.types import ASGIApp, Scope, Receive, Send, Message
import orjson
import re
from urllib.parse import unquote

from config.loader import ConfigManager
from utils.size_parser import parse_size

class WAFMiddleware:
    __slots__ = ("app", "config", "body_limit_bytes", "traversal_pattern")

    def __init__(self, app: ASGIApp):
        self.app = app
        self.config = ConfigManager.get()
        self.body_limit_bytes = parse_size(self.config.server.body_limit)
        
        # Highly optimized traversal pattern for both urlencodings and literal slashes
        self.traversal_pattern = re.compile(br'(\.\./|\.\.\\|%2e%2e%2f|%2e%2e/|\.\.%2f|%2e%2e%5c)', re.IGNORECASE)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        # 1. URL Length Check
        raw_path = scope.get("raw_path", b"")
        query_string = scope.get("query_string", b"")
        
        # In ASGI, path + query limits are typically around 2048
        if len(raw_path) + len(query_string) > 2048:
            await self._send_error(send, 414, "WAF_URI_TOO_LONG", "URI exceeds 2048 character limit")
            return

        # 2. Null Byte Check (operate on raw bytes, much faster)
        if b'\x00' in raw_path or b'\x00' in query_string:
            await self._send_error(send, 400, "WAF_NULL_BYTE", "Null byte detected in request")
            return

        # 3. Path Traversal Check (check raw bytes using compiled pattern)
        if self.traversal_pattern.search(raw_path) or self.traversal_pattern.search(query_string):
            await self._send_error(send, 400, "WAF_PATH_TRAVERSAL", "Path traversal attempt detected")
            return

        # 4. Query Parameter Count Check (rough count of & separators without parsing)
        if query_string.count(b'&') > 50:
            await self._send_error(send, 400, "WAF_TOO_MANY_PARAMS", "Too many query parameters (limit 50)")
            return

        # 5. Body Limit Check via Headers
        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        
        if content_length:
            try:
                if int(content_length) > self.body_limit_bytes:
                    await self._send_error(send, 413, "WAF_BODY_TOO_LARGE", f"Request body too large. Limit is {self.config.server.body_limit}")
                    return
            except ValueError:
                await self._send_error(send, 400, "WAF_INVALID_HEADER", "Invalid Content-Length header")
                return

        return await self.app(scope, receive, send)

    async def _send_error(self, send: Send, status_code: int, code: str, message: str) -> None:
        body = orjson.dumps({
            "success": False,
            "error": {
                "code": code,
                "message": message
            }
        })
        await send({
            "type": "http.response.start",
            "status": status_code,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        })
        await send({
            "type": "http.response.body",
            "body": body,
            "more_body": False
        })
