from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send
import orjson

class SecurityHeadersMiddleware:
    """ASGIMiddleware to add security headers to every response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
            
        # Fast memory bomb abort (bypass for storage uploads)
        path = scope.get("path", "")
        is_upload = path.startswith("/api/fs/")
        headers_dict = dict(scope.get("headers", []))
        cl_bytes = headers_dict.get(b"content-length")
        if cl_bytes and int(cl_bytes) > 5242880 and not is_upload:  # 5MB Limit
            payload = orjson.dumps({"success": False, "error": {"code": "PAYLOAD_TOO_LARGE", "message": "Max 5MB payload limit exceeded."}})
            await send({"type": "http.response.start", "status": 413, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode("utf-8"))]})
            await send({"type": "http.response.body", "body": payload})
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])

                # OWASP Security Headers
                security_headers = [
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"x-xss-protection", b"0"),
                    (b"strict-transport-security", b"max-age=63072000; includeSubDomains; preload"),
                    (b"cache-control", b"no-store"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"permissions-policy", b"interest-cohort=()"),
                ]

                # Add strict CSP only for non-documentation endpoints
                path = scope.get("path", "")
                if not (path.startswith("/api/docs") or path.startswith("/api/spec")):
                    security_headers.append((b"content-security-policy", b"default-src 'none'"))

                # Append security headers if they don't already exist
                existing_keys = {k.lower() for k, v in headers}
                for key, value in security_headers:
                    if key not in existing_keys:
                        headers.append((key, value))

            await send(message)

        await self.app(scope, receive, send_wrapper)
