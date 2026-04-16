from starlette.types import ASGIApp, Scope, Receive, Send, Message
import orjson
import re
from typing import Optional, Tuple

from config.loader import ConfigManager
from utils.size_parser import parse_size

class WAFMiddleware:
    """Security middleware intercepting malicious payloads before execution."""
    __slots__ = ("app", "config", "body_limit_bytes", "traversal_pattern")

    def __init__(self, app: ASGIApp):
        self.app = app
        self.config = ConfigManager.get()
        self.body_limit_bytes = parse_size(self.config.server.body_limit)
        
        # Highly optimized traversal pattern for urlencodings and literal slashes
        self.traversal_pattern = re.compile(br'(\.\./|\.\.\\|%2e%2e%2f|%2e%2e/|\.\.%2f|%2e%2e%5c)', re.IGNORECASE)

    # ─────────────────────────────────────────────────────────────────────────────
    # Request Validators
    # ─────────────────────────────────────────────────────────────────────────────

    def _validate_uri_length(self, raw_path: bytes, query_string: bytes) -> Optional[Tuple[int, str, str]]:
        """Ensures the URI does not exceed typical buffer limits."""
        if len(raw_path) + len(query_string) > 2048:
            return 414, "WAF_URI_TOO_LONG", "URI exceeds 2048 character limit"
        return None

    def _validate_null_bytes(self, raw_path: bytes, query_string: bytes) -> Optional[Tuple[int, str, str]]:
        """Blocks raw null bytes from crashing underlying C libraries."""
        if b'\x00' in raw_path or b'\x00' in query_string:
            return 400, "WAF_NULL_BYTE", "Null byte detected in request"
        return None

    def _validate_path_traversal(self, raw_path: bytes, query_string: bytes) -> Optional[Tuple[int, str, str]]:
        """Detects directory traversal sequences."""
        if self.traversal_pattern.search(raw_path) or self.traversal_pattern.search(query_string):
            return 400, "WAF_PATH_TRAVERSAL", "Path traversal attempt detected"
        return None

    def _validate_query_params(self, query_string: bytes) -> Optional[Tuple[int, str, str]]:
        """Limits the attack surface of large parameter floods."""
        if query_string.count(b'&') > 50:
            return 400, "WAF_TOO_MANY_PARAMS", "Too many query parameters (limit 50)"
        return None

    def _validate_body_size(self, headers: dict) -> Optional[Tuple[int, str, str]]:
        """Denies requests aggressively based on Content-Length prior to buffering."""
        content_length = headers.get(b"content-length")
        if content_length:
            try:
                if int(content_length) > self.body_limit_bytes:
                    return 413, "WAF_BODY_TOO_LARGE", f"Request body too large. Limit is {self.config.server.body_limit}"
            except ValueError:
                return 400, "WAF_INVALID_HEADER", "Invalid Content-Length header"
        return None

    # ─────────────────────────────────────────────────────────────────────────────
    # Core Injection
    # ─────────────────────────────────────────────────────────────────────────────

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        raw_path = scope.get("raw_path", b"")
        query_string = scope.get("query_string", b"")
        headers = dict(scope.get("headers", []))

        # Check validations in fast-fail order
        error = (
            self._validate_uri_length(raw_path, query_string) or
            self._validate_null_bytes(raw_path, query_string) or
            self._validate_path_traversal(raw_path, query_string) or
            self._validate_query_params(query_string) or
            self._validate_body_size(headers)
        )

        if error:
            status_code, code, message = error
            return await self._send_error(send, status_code, code, message)

        return await self.app(scope, receive, send)

    async def _send_error(self, send: Send, status_code: int, code: str, message: str) -> None:
        """Emits a hard WAF interrupt as a JSON payload."""
        body = orjson.dumps({
            "success": False,
            "error": {"code": code, "message": message}
        })
        
        await send({
            "type": "http.response.start",
            "status": status_code,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        })
        await send({"type": "http.response.body", "body": body, "more_body": False})
